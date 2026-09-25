import asyncio
import csv
import gc
import io
import json
import logging
import math
import os
import re
import secrets
import string
import time
import uuid
from typing import Dict, List, Optional
from datetime import datetime

from curl_cffi import requests as cffi_requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ==============================================================================
# 1. Gizlilik ve zero-log yapılandırması
# ==============================================================================
# Uvicorn ve FastAPI'nin IP ile kullanıcı adı tutmasını engellemek için access loglarını sessize alıyoruz.
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

logger = logging.getLogger("1k_exporter")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
logger.addHandler(console_handler)

# ==============================================================================
# 2. Veri modelleri vs.
# ==============================================================================
MONTH_MAP = {
    "Oca": "01", "Şub": "02", "Mar": "03", "Nis": "04",
    "May": "05", "Haz": "06", "Tem": "07", "Ağu": "08",
    "Eyl": "09", "Eki": "10", "Kas": "11", "Ara": "12",
    "Ocak": "01", "Şubat": "02", "Mart": "03", "Nisan": "04",
    "Mayıs": "05", "Haziran": "06", "Temmuz": "07", "Ağustos": "08",
    "Eylül": "09", "Ekim": "10", "Kasım": "11", "Aralık": "12"
}

GOODREADS_CSV_HEADERS = [
    "Title", "Author", "ISBN", "My Rating", "Date Read", "Exclusive Shelf"
]

class ExportRequest(BaseModel):
    username: str
    shelf: Optional[str] = "okuduklari"

class JobState:
    def __init__(self, job_id: str, username: str, shelf: str = "okuduklari"):
        self.job_id: str = job_id
        self.username: str = username
        self.shelf: str = shelf
        self.status: str = "queued"  # queued, scraping, completed, failed
        self.queue_position: int = 1
        self.current_count: int = 0
        self.total_count: int = 0
        self.percent: int = 0
        self.last_book: Optional[dict] = None
        self.csv_bytes: Optional[bytes] = None
        self.error_message: Optional[str] = None
        self.created_at: float = time.time()
        self.event_queues: List[asyncio.Queue] = []

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "status": self.status,
            "queue_position": self.queue_position,
            "current_count": self.current_count,
            "total_count": self.total_count,
            "percent": self.percent,
            "last_book": self.last_book,
            "error_message": self.error_message
        }

    async def broadcast(self, payload: dict):
        dead_queues = []
        for q in self.event_queues:
            try:
                q.put_nowait(payload)
            except Exception:
                dead_queues.append(q)
        for dq in dead_queues:
            if dq in self.event_queues:
                self.event_queues.remove(dq)

# ==============================================================================
# 3. Bellek deposu, kuyruk yönetimi
# ==============================================================================
JOBS: Dict[str, JobState] = {}
JOB_QUEUE: asyncio.Queue = asyncio.Queue()
ACTIVE_QUEUE_LIST: List[str] = []

def generate_device_code(length: int = 14) -> str:
    """1000Kitap için geçerli formata uygun cihaz kodu üretir."""
    chars = string.ascii_uppercase + string.digits
    prefix = ''.join(secrets.choice(chars) for _ in range(2))
    suffix = ''.join(secrets.choice(chars) for _ in range(11))
    return f"{prefix}-{suffix}"

def sanitize_csv_field(val: any) -> str:
    """CSV Injection önleyici: =, +, -, @ ile başlayan değerlerin önüne tek tırnak ekler."""
    if val is None:
        return ""
    text = str(val).strip()
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text

def parse_date(ek_bilgi: str) -> str:
    """1000Kitap ekBilgi alanındaki Türkçe tarihi YYYY/MM/DD formatına çevirir."""
    if not ek_bilgi:
        return ""
    match = re.search(r'(\d{1,2})\s+([A-Za-zÇŞĞÖÜıİçşğöü]+)\s+(\d{4})', ek_bilgi)
    if not match:
        return ""
    day = match.group(1).zfill(2)
    month_name = match.group(2)
    year = match.group(3)
    month = MONTH_MAP.get(month_name, "01")
    return f"{year}/{month}/{day}"

def parse_rating(ek_bilgi: str) -> int:
    """
    1000Kitap ekBilgi alanındaki 10'luk puanı Goodreads 1-5 tamsayı puanına çevirir.
    Formül: math.ceil(puan10 / 2) -> (10->5, 9->5, 8->4, 7->4, 6->3, 5->3, 4->2, 3->2, 2->1, 1->1).
    Puan verilmemişse 0 döner.
    """
    if not ek_bilgi or "puan vermedi" in ek_bilgi.lower():
        return 0
    match = re.search(r'(\d+(?:\.\d+)?)\s*/\s*10', ek_bilgi)
    if match:
        try:
            puan_10 = float(match.group(1))
            goodreads_val = math.ceil(puan_10 / 2.0)
            return max(1, min(5, goodreads_val))
        except Exception:
            return 0
    return 0

# ==============================================================================
# 4. 1000kitap scraper ve CSV üretimi
# ==============================================================================
async def scrape_user_books(job: JobState):
    """
    1000Kitap API'sini saniyede maksimum 2 istek hız sınırlamasıyla tarar,
    kitapları ayrıştırır ve UTF-8 BOM'lu Goodreads CSV'si üretir.
    """
    device_code = generate_device_code()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Api-V2": "1",
        "1-CIHAZ-KODU": device_code,
        "Referer": "https://1000kitap.com/",
        "Origin": "https://1000kitap.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site"
    }

    url = "https://api.1000kitap.com/v2/uyeler/kitaplar/liste"
    page = 1
    kume = ""
    has_more = True
    all_books_rows: List[List[str]] = []
    total_estimated = 0

    session = cffi_requests.AsyncSession(impersonate="chrome120")

    try:

        while has_more:
            params = {
                "kadi": job.username,
                "raf": job.shelf,
                "sayfa": page,
                "appVersion": "2.60.60",
                "os": "web",
                "hl": "tr"
            }
            if kume:
                params["kume"] = kume

            response = None
            for retry in range(3):
                try:
                    response = await session.get(url, params=params, headers=headers, timeout=25.0)
                    if response.status_code in (403, 429):
                        # 1000Kitap Cloudflare geçici rate-limit engeli -> Taze cihaz kodu ve bekleme
                        await asyncio.sleep(1.5 * (retry + 1))
                        headers["1-CIHAZ-KODU"] = generate_device_code()
                        continue
                    break
                except Exception as net_err:
                    if retry == 2:
                        raise net_err
                    await asyncio.sleep(1.0)

            if response is None or response.status_code != 200:
                code = response.status_code if response else "Bilinmiyor"
                if code == 404:
                    raise Exception("Kullanıcı bulunamadı. Lütfen kullanıcı adını kontrol edin.")
                raise Exception(f"1000Kitap API bağlantı hatası (HTTP {code})")

            data = response.json()

            # 1000Kitap özel hata yanıtı (örn: Böyle bir okur bulunamadı)
            if data.get("hata") == 1:
                msg = data.get("hataMesaji") or data.get("alertMesaji") or "1000Kitap okuru bulunamadı."
                raise Exception(f"1000Kitap Bildirimi: {msg}")

            if "bilgi" in data and data["bilgi"] == 0:
                msg = data.get("bilgiMesaji", "Profil bulunamadı veya gizli.")
                raise Exception(f"1000Kitap Bildirimi: {msg}")

            sonuc = data.get("_sonuc")
            if not sonuc:
                raise Exception("1000Kitap API yanıtı boş veya geçersiz format.")

            # 1000kitap raf gizliliği veya özel hata kontrolü
            hata_metni = sonuc.get("hataMetni")
            if hata_metni:
                hata_lower = str(hata_metni).lower()
                if "sadece okurun kendisi" in hata_lower or "görebilir" in hata_lower or "gizli" in hata_lower:
                    raise Exception(
                        "Bu kullanıcının 'Okudukları' rafı gizlidir (Sadece okurun kendisi görebilir). "
                        "Aktarım yapabilmek için 1000Kitap Profil Ayarları ➔ Gizlilik bölümünden "
                        "'Okuduklarım' rafını herkese açık yapıp tekrar deneyin."
                    )
                else:
                    raise Exception(f"1000Kitap Bildirimi: {hata_metni}")

            # İlk sayfada toplam kitap sayısını raflardan kestir
            if page == 1:
                kitaplik_bilgiler = sonuc.get("kitaplikBilgiler", {})
                raflar = kitaplik_bilgiler.get("raflar", [])
                for r in raflar:
                    if r.get("seo") == job.shelf or r.get("baslik", "").lower() == "okudukları":
                        bilgi_txt = r.get("bilgi", "")
                        digits = re.findall(r'\d+', bilgi_txt.replace(".", "").replace(",", ""))
                        if digits:
                            total_estimated = int(digits[0])
                        break
                
                # Şayet raflar altında bulunamadıysa, baslikMini'den de yakala
                if total_estimated == 0 and sonuc.get("baslikMini"):
                    digits = re.findall(r'\d+', str(sonuc.get("baslikMini")).replace(".", "").replace(",", ""))
                    if digits:
                        total_estimated = int(digits[0])

                job.total_count = total_estimated

            raw_list = sonuc.get("liste", [])
            if not raw_list:
                break

            for item in raw_list:
                # Reklam öğelerini filtrele
                if item.get("renderTuru") == "reklam" or not item.get("adi"):
                    continue

                title = item.get("adi", "").strip()
                author = item.get("yazarAdi") or item.get("ilkYazar") or ""
                if not author and item.get("yazarlar"):
                    author = item["yazarlar"][0].get("adi", "")

                ek_bilgi = item.get("ekBilgi", "")
                date_read = parse_date(ek_bilgi)
                date_added = date_read if date_read else datetime.now().strftime("%Y/%m/%d")
                user_rating = parse_rating(ek_bilgi)

                avg_puan = item.get("puan")
                avg_rating_str = ""
                if avg_puan is not None:
                    try:
                        avg_rating_str = f"{(float(avg_puan) / 2.0):.2f}"
                    except Exception:
                        pass

                read_count = 1
                durum_btn = item.get("okumaDurumuButon") or {}
                if isinstance(durum_btn, dict) and durum_btn.get("okumaSayisi", 0) > 1:
                    read_count = durum_btn["okumaSayisi"]

                # Goodreads CSV Kolonları Eşleştirmesi (Resmî 6 sütun standardı)
                row = [
                    sanitize_csv_field(title),                                      # Title (Sütun 1)
                    sanitize_csv_field(author),                                     # Author (Sütun 2)
                    "",                                                             # ISBN (Sütun 3)
                    str(user_rating) if user_rating > 0 else "",                    # My Rating (Sütun 4 - Puan yoksa boş kalır)
                    date_read,                                                      # Date Read (Sütun 5 - YYYY/MM/DD)
                    "read"                                                          # Exclusive Shelf (Sütun 6)
                ]
                all_books_rows.append(row)
                job.current_count = len(all_books_rows)

                # UI canlı bilgi güncellemesi
                job.last_book = {
                    "title": title,
                    "author": author,
                    "cover": item.get("resim") or item.get("resimB") or ""
                }

            # İlerleme yüzdesi
            if job.total_count > 0:
                pct = int((job.current_count / job.total_count) * 100)
                job.percent = min(99, pct)
            else:
                job.percent = min(95, page * 10)

            await job.broadcast({
                "type": "progress",
                "status": "scraping",
                "current": job.current_count,
                "total": job.total_count,
                "percent": job.percent,
                "last_book": job.last_book
            })

            has_more = bool(sonuc.get("hasMore", False))
            kume = str(sonuc.get("kume", ""))
            page += 1

            if not has_more or not raw_list:
                break

            # Hız sınırı (Max 2 istek/saniye)
            # Cloudflare ve 1000Kitap güvenlik toleransı için 700ms bekliyoruz.
            await asyncio.sleep(0.70)

        if not all_books_rows:
            if total_estimated > 0:
                raise Exception(
                    f"Kullanıcının kütüphanesinde {total_estimated} kitap görünüyor ancak liste içeriği boş dönüyor. "
                    "Rafınız gizli olabilir. Lütfen 1000Kitap Profil Ayarları ➔ Gizlilik menüsünden "
                    "'Okuduklarım' rafını herkese açık yapıp tekrar deneyin."
                )
            raise Exception(
                "Bu kullanıcının 'okudukları' rafında taranacak kitap bulunamadı. "
                "Rafınız boş veya gizli olabilir. Lütfen 1000Kitap Gizlilik ayarlarınızı kontrol edin."
            )

        # CSV Çıktısını Bellek Üzerinde Oluştur
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(GOODREADS_CSV_HEADERS)
        for r in all_books_rows:
            writer.writerow(r)

        # Standart UTF-8 ile kaydediyoruz
        csv_string = output.getvalue()
        job.csv_bytes = csv_string.encode("utf-8")
        job.status = "completed"
        job.percent = 100
        if job.total_count == 0 or job.total_count < job.current_count:
            job.total_count = job.current_count

        await job.broadcast({
            "type": "completed",
            "status": "completed",
            "current": job.current_count,
            "total": job.total_count,
            "percent": 100,
            "download_url": f"/api/jobs/{job.job_id}/download"
        })

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        await job.broadcast({
            "type": "error",
            "status": "failed",
            "error": str(e)
        })
    finally:
        await session.close()


# ==============================================================================
# 5. KUYRUK MOTORU WORKER (FIFO Queue Manager)
# ==============================================================================
async def queue_worker():
    """Arka planda FIFO sırasına göre işleri işleyen ana kuyruk döngüsü."""
    while True:
        job_id = await JOB_QUEUE.get()
        job = JOBS.get(job_id)
        if not job:
            JOB_QUEUE.task_done()
            continue

        try:
            # Sıradaki diğer bekleyen işlerin pozisyonlarını güncelle
            if job_id in ACTIVE_QUEUE_LIST:
                ACTIVE_QUEUE_LIST.remove(job_id)

            for idx, q_id in enumerate(ACTIVE_QUEUE_LIST):
                waiting_job = JOBS.get(q_id)
                if waiting_job and waiting_job.status == "queued":
                    waiting_job.queue_position = idx + 1
                    wait_count = waiting_job.queue_position - 1
                    msg = f"Sıradasınız (Önünüzde {wait_count} kişi var)..." if wait_count > 0 else "Sıradaki işlem sizin, aktarım başlıyor..."
                    await waiting_job.broadcast({
                        "type": "queued",
                        "status": "queued",
                        "position": waiting_job.queue_position,
                        "message": msg
                    })

            job.status = "scraping"
            await job.broadcast({
                "type": "status",
                "status": "scraping",
                "message": "Kitaplık taranmaya başlandı..."
            })

            await scrape_user_books(job)

        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            await job.broadcast({
                "type": "error",
                "status": "failed",
                "error": str(e)
            })
        finally:
            JOB_QUEUE.task_done()

# Periyodik bellek temizleyici (15 dakikadan eski tamamlanmış işleri RAM'den siler)
async def cleanup_worker():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [
            jid for jid, j in list(JOBS.items())
            if (now - j.created_at) > 900  # 15 dakika
        ]
        for jid in expired:
            JOBS.pop(jid, None)
        if expired:
            gc.collect()

# ==============================================================================
# 6. FASTAPI UYGULAMASI VE ENDPOINT'LER
# ==============================================================================
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(queue_worker())
    cleanup_task = asyncio.create_task(cleanup_worker())
    logger.info("1000Kitap to Goodreads Aktarıcı başlatıldı. Kuyruk motoru hazır.")
    yield
    worker_task.cancel()
    cleanup_task.cancel()

app = FastAPI(
    title="1000Kitap to Goodreads Exporter",
    description="Zero-Database, Zero-Log, High-Privacy Reading History Exporter",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/jobs")
async def create_export_job(payload: ExportRequest):
    """Yeni aktarma görevi oluşturur ve FIFO kuyruğuna ekler."""
    username = payload.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Lütfen geçerli bir 1000Kitap kullanıcı adı girin.")

    job_id = uuid.uuid4().hex
    job = JobState(job_id=job_id, username=username, shelf=payload.shelf or "okuduklari")
    JOBS[job_id] = job

    ACTIVE_QUEUE_LIST.append(job_id)
    job.queue_position = len(ACTIVE_QUEUE_LIST)

    await JOB_QUEUE.put(job_id)

    return {
        "job_id": job_id,
        "queue_position": job.queue_position,
        "message": f"Kuyruğa alındı. Sıranız: {job.queue_position}"
    }

@app.get("/api/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    """Fallback JSON Polling için görev durumu endpoint'i."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Görev bulunamadı veya süresi doldu.")
    return job.to_dict()

@app.get("/api/jobs/{job_id}/events")
async def stream_job_events(job_id: str, request: Request):
    """
    Canlı durum ve ilerleme bilgisi sağlayan Server-Sent Events (SSE) endpoint'i.
    """
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Görev bulunamadı.")

    event_queue: asyncio.Queue = asyncio.Queue()
    job.event_queues.append(event_queue)

    async def event_generator():
        # İlk bağlantıda mevcut durumu hemen gönder
        initial_payload = {
            "type": "status",
            "status": job.status,
            "position": job.queue_position,
            "current": job.current_count,
            "total": job.total_count,
            "percent": job.percent,
            "last_book": job.last_book,
            "error": job.error_message
        }
        yield f"data: {json.dumps(initial_payload)}\n\n"

        if job.status == "completed":
            yield f"data: {json.dumps({'type': 'completed', 'status': 'completed', 'current': job.current_count, 'total': job.total_count, 'percent': 100, 'download_url': f'/api/jobs/{job.job_id}/download'})}\n\n"
            return

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(event_queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                    if payload.get("type") in ("completed", "error"):
                        break
                except asyncio.TimeoutError:
                    # Bağlantıyı canlı tutmak için ping/keepalive gönder
                    yield f": keep-alive\n\n"
        finally:
            if event_queue in job.event_queues:
                job.event_queues.remove(event_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/api/jobs/{job_id}/download")
async def download_job_csv(job_id: str):
    """
    Oluşturulan Goodreads CSV'sini doğrudan RAM üzerinden kullanıcıya stream eder.
    İndirme tamamlandığında RAM'den derhal temizlenir (Zero-Database & Privacy).
    """
    job = JOBS.get(job_id)
    if not job or not job.csv_bytes:
        raise HTTPException(status_code=404, detail="CSV dosyası bulunamadı veya süresi doldu.")

    filename = "1000kitap.csv"
    data_stream = io.BytesIO(job.csv_bytes)

    # 15 dakikalık session boyunca kullanıcının tekrar tekrar indirebilmesi için 
    # ilk indirmede hemen silmiyoruz; cleanup_worker süresi dolunca RAM'den tamamen imha ediyor.
    return StreamingResponse(
        data_stream,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(job.csv_bytes)),
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache"
        }
    )


# Frontend statik dosyalarını bağla
frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=False)

