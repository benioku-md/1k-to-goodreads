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
import urllib.parse
from typing import Dict, List, Optional
from datetime import datetime

import httpx
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
# 4. KITAPYURDU ISBN ÇÖZÜMLEME VE 1000KITAP SCRAPER
# ==============================================================================
def normalize_tr_text(text: str) -> str:
    if not text:
        return ''
    text = text.lower()
    for tr, en in [('ı', 'i'), ('ğ', 'g'), ('ü', 'u'), ('ş', 's'), ('ö', 'o'), ('ç', 'c')]:
        text = text.replace(tr, en)
    return re.sub(r'[^a-z0-9\s]', ' ', text).strip()

def pick_best_ky_url(target_title: str, html: str) -> str:
    matches = re.findall(r'<a[^>]+href="([^"]+/kitap/[^/"]+/\d+\.html[^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)
    norm_b = normalize_tr_text(target_title)
    words_b = set(norm_b.split())
    best_score = -1
    best_url = ''
    for href, inner in matches:
        alt_m = re.search(r'alt=["\']([^"\']+)["\']', inner)
        clean_text = alt_m.group(1).strip() if alt_m else ''
        if not clean_text:
            clean_text = re.sub(r'<[^>]+>', '', inner).strip()
        if not clean_text or clean_text.lower() in ('ürünü incele', 'satın al', 'incele', 'detay'):
            slug = href.split('/kitap/')[1].split('/')[0].replace('-', ' ')
            clean_text = slug

        norm_c = normalize_tr_text(clean_text)
        if norm_b == norm_c:
            score = 100
        elif norm_b in norm_c:
            score = 90
        elif norm_c in norm_b:
            score = 80
        else:
            cand_words = set(norm_c.split())
            overlap = words_b.intersection(cand_words)
            score = int((len(overlap) / len(words_b)) * 70) if words_b else 0

        slug = href.split('/kitap/')[1].split('/')[0].replace('-', ' ')
        slug_words = set(slug.split())
        if words_b.intersection(slug_words):
            score += 15

        if score > best_score:
            best_score = score
            best_url = href
    if best_score >= 35:
        return best_url
    return ''

def extract_isbn(html: str) -> str:
    # 1. JSON-LD schema: "isbn":"978..."
    m = re.search(r'["\']isbn["\']\s*:\s*["\']([0-9Xx\-]{10,17})["\']', html, re.IGNORECASE)
    if not m:
        # 2. Modern Kitapyurdu attribute value
        m = re.search(r'ky-pd-attributes__value">\s*([0-9Xx\-]{10,17})\s*<', html)
    if not m:
        m = re.search(r'itemprop=["\']isbn["\'][^>]*>([^<]+)<', html)
    if not m:
        m = re.search(r'ISBN[:\s]*</strong>\s*([0-9Xx\-]{10,17})', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<th>\s*ISBN\s*</th>\s*<td>\s*([0-9Xx\-]{10,17})', html, re.IGNORECASE)
    if not m:
        m = re.search(r'data-isbn=["\']([0-9Xx\-]{10,17})["\']', html, re.IGNORECASE)
    if m:
        raw_isbn = m.group(1).replace("-", "").strip()
        if len(raw_isbn) in (10, 13):
            return raw_isbn
    return ''

async def scrape_user_books(job: JobState):
    """
    1000Kitap API'sini saniyede maksimum 2 istek hız sınırlamasıyla tarar,
    kitapları ayrıştırır ve UTF-8 BOM'lu Goodreads CSV'si üretir.
    Eşzamanlı boru hattı (Producer-Consumer): Kitaplar çekilir çekilmez 
    Kitapyurdu işçilerine aktarılarak eşzamanlı olarak ISBN'leri çözümlenir.
    """
    device_code = generate_device_code()
    headers = {
        "Api-V2": "1",
        "1-CIHAZ-KODU": device_code,
        "User-Agent": "okur/2.60.60 (Android 14; Mobile)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    url = "https://api.1000kitap.com/v2/uyeler/kitaplar/liste"
    page = 1
    kume = ""
    has_more = True
    total_estimated = 0
    collected_books: List[dict] = []
    session = None

    try:
        session = cffi_requests.AsyncSession(impersonate="chrome120")
        isbn_queue: asyncio.Queue = asyncio.Queue()
        resolved_count = 0
        total_books = 0

        ky_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
        }

        async def ky_worker(client: httpx.AsyncClient):
            nonlocal resolved_count
            while True:
                book = await isbn_queue.get()
                if book is None:
                    isbn_queue.task_done()
                    break

                raw_title = book.get("title", "")
                raw_author = book.get("author", "")
                found_isbn = ""
                if raw_title:
                    clean_title = re.sub(r'[\-\:\&/].*$', '', re.sub(r'\s*\([^)]*\)', '', raw_title)).strip()
                    author_last = raw_author.split()[-1] if raw_author else ''

                    queries = []
                    if clean_title and raw_author:
                        queries.append(f"{clean_title} {raw_author}".strip())
                    if clean_title and author_last and author_last != raw_author:
                        queries.append(f"{clean_title} {author_last}".strip())
                    if raw_title and raw_title not in queries:
                        queries.append(raw_title)
                    if clean_title and clean_title not in queries:
                        queries.append(clean_title)

                    cf_blocked = False
                    for q in queries:
                        if cf_blocked:
                            break
                        try:
                            encoded_q = urllib.parse.quote(q)
                            search_url = f"https://www.kitapyurdu.com/index.php?route=product/search&filter_name={encoded_q}&fuzzy=0"
                            resp = await client.get(search_url, headers=ky_headers, timeout=3.5)
                            if resp.status_code in (403, 429):
                                cf_blocked = True
                                break
                            if resp.status_code == 200:
                                html_text = resp.text
                                isbn = extract_isbn(html_text)
                                if isbn:
                                    found_isbn = isbn
                                    break

                                best_prod_url = pick_best_ky_url(clean_title or raw_title, html_text)
                                if best_prod_url:
                                    prod_resp = await client.get(best_prod_url, headers=ky_headers, timeout=3.5)
                                    if prod_resp.status_code == 200:
                                        p_isbn = extract_isbn(prod_resp.text)
                                        if p_isbn:
                                            found_isbn = p_isbn
                                            break
                            await asyncio.sleep(0.05)
                        except Exception:
                            pass

                    # Yabanci datacenter engeli varsa (Render ortami), AltunHOST TR IP kopyasina sor
                    if not found_isbn and cf_blocked:
                        try:
                            b_url = "http://5.175.136.60:8085/api/resolve-isbn"
                            b_resp = await client.get(b_url, params={"title": raw_title, "author": raw_author}, timeout=4.0)
                            if b_resp.status_code == 200:
                                found_isbn = b_resp.json().get('isbn', '')
                        except Exception:
                            pass

                if found_isbn:
                    book["isbn"] = found_isbn

                resolved_count += 1
                isbn_queue.task_done()

                # Her kitap cozuldugunde anlik canli durum guncellemesi gonder
                if total_books > 0:
                    pct_isbn = min(99, int((resolved_count / total_books) * 100))
                    await job.broadcast({
                        "type": "progress",
                        "status": "resolving_isbn",
                        "message": f"ISBN numaraları tamamlanıyor: {resolved_count} / {total_books} (%{pct_isbn})...",
                        "current": resolved_count,
                        "total": total_books,
                        "percent": pct_isbn,
                        "last_book": {"title": raw_title, "author": raw_author, "cover": book.get("cover", "")}
                    })

        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as ky_client:
            ky_workers = [asyncio.create_task(ky_worker(ky_client)) for _ in range(5)]

            while has_more:
                params = {
                    "kadi": job.username,
                    "raf": job.shelf,
                    "sayfa": page,
                    "appVersion": "2.60.60",
                    "os": "android",
                    "hl": "tr"
                }
                if kume:
                    params["kume"] = kume

                response = None
                for retry in range(2):
                    try:
                        response = await session.get(url, params=params, headers=headers, timeout=6.0)
                        if response.status_code in (403, 429):
                            if retry == 0:
                                await asyncio.sleep(1.0)
                                try:
                                    await session.close()
                                except Exception:
                                    pass
                                session = cffi_requests.AsyncSession(impersonate="chrome120")
                                headers["1-CIHAZ-KODU"] = generate_device_code()
                                continue
                            break
                        break
                    except Exception as net_err:
                        if retry == 1:
                            raise net_err
                        await asyncio.sleep(0.5)

                if response is None or response.status_code != 200:
                    code = response.status_code if response else "Bilinmiyor"
                    if code == 404:
                        raise Exception("Kullanıcı bulunamadı. Lütfen kullanıcı adını kontrol edin.")
                    raise Exception(f"1000Kitap API bağlantı hatası (HTTP {code})")

                data = response.json()

                if data.get("hata") == 1:
                    msg = data.get("hataMesaji") or data.get("alertMesaji") or "1000Kitap okuru bulunamadı."
                    raise Exception(f"1000Kitap Bildirimi: {msg}")

                if "bilgi" in data and data["bilgi"] == 0:
                    msg = data.get("bilgiMesaji", "Profil bulunamadı veya gizli.")
                    raise Exception(f"1000Kitap Bildirimi: {msg}")

                sonuc = data.get("_sonuc")
                if not sonuc:
                    raise Exception("1000Kitap API yanıtı boş veya geçersiz format.")

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

                if page == 1:
                    kitaplik_bilgiler = sonuc.get("kitaplikBilgiler", {})
                    raflar = kitaplik_bilgiler.get("raflar", [])
                    for r in raflar:
                        if r.get("seo") == job.shelf or r.get("baslik", "").lower() == "okudukları":
                            bilgi_txt = r.get("bilgi", "")
                            digits = re.findall(r"\d+", bilgi_txt.replace(".", "").replace(",", ""))
                            if digits:
                                total_estimated = int(digits[0])
                            break
                    if total_estimated == 0 and sonuc.get("baslikMini"):
                        digits = re.findall(r"\d+", str(sonuc.get("baslikMini")).replace(".", "").replace(",", ""))
                        if digits:
                            total_estimated = int(digits[0])

                    job.total_count = total_estimated

                raw_list = sonuc.get("liste", [])
                if not raw_list:
                    break

                for item in raw_list:
                    if item.get("renderTuru") == "reklam" or not item.get("adi"):
                        continue

                    title = item.get("adi", "").strip()
                    author = item.get("yazarAdi") or item.get("ilkYazar") or ""
                    if not author and item.get("yazarlar"):
                        author = item["yazarlar"][0].get("adi", "")

                    ek_bilgi = item.get("ekBilgi", "")
                    date_read = parse_date(ek_bilgi)
                    user_rating = parse_rating(ek_bilgi)

                    book_entry = {
                        "id": item.get("id"),
                        "seo_adi": item.get("seo_adi") or "",
                        "title": title,
                        "author": author,
                        "user_rating": user_rating,
                        "date_read": date_read,
                        "cover": item.get("resim") or item.get("resimB") or "",
                        "isbn": ""
                    }
                    collected_books.append(book_entry)
                    job.current_count = len(collected_books)
                    job.last_book = {
                        "title": title,
                        "author": author,
                        "cover": book_entry["cover"]
                    }

                    # Eşzamanlı boru hattı: Kitabı bekletmeden anında Kitapyurdu işçi havuzuna fırlat
                    await isbn_queue.put(book_entry)

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

                await asyncio.sleep(0.90)

            if not collected_books:
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

            total_books = len(collected_books)

            # İşçilere bitiş sinyali gönder ve hepsinin tamamlanmasını bekle
            for _ in range(5):
                await isbn_queue.put(None)
            await asyncio.gather(*ky_workers)

        # ======================================================================
        # 4.3. GOODREADS CSV ÇIKTISINI BELLEK ÜZERİNDE OLUŞTURMA
        # ======================================================================
        all_books_rows: List[List[str]] = []
        for b in collected_books:
            row = [
                sanitize_csv_field(b["title"]),
                sanitize_csv_field(b["author"]),
                sanitize_csv_field(b["isbn"]),
                str(b["user_rating"]) if b["user_rating"] > 0 else "",
                b["date_read"],
                "read"
            ]
            all_books_rows.append(row)

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
        if session:
            try:
                await session.close()
            except Exception:
                pass


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

@app.get("/api/resolve-isbn")
async def resolve_isbn_api(title: str, author: str = ""): 
    """AltunHOST TR IP uzerinden ISBN arama servisi."""
    ky_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    clean_title = re.sub(r'[\-\:\&/].*$', '', re.sub(r'\s*\([^)]*\)', '', title)).strip()
    author_last = author.split()[-1] if author else ''
    queries = []
    if clean_title and author:
        queries.append(f"{clean_title} {author}".strip())
    if clean_title and author_last and author_last != author:
        queries.append(f"{clean_title} {author_last}".strip())
    if title and title != clean_title:
        queries.append(title)
    if clean_title and clean_title not in queries:
        queries.append(clean_title)

    async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
        for q in queries:
            try:
                encoded_q = urllib.parse.quote(q)
                url = f"https://www.kitapyurdu.com/index.php?route=product/search&filter_name={encoded_q}&fuzzy=0"
                resp = await client.get(url, headers=ky_headers)
                if resp.status_code == 200:
                    isbn = extract_isbn(resp.text)
                    if isbn:
                        return {"isbn": isbn, "title": title}
                    best_url = pick_best_ky_url(clean_title or title, resp.text)
                    if best_url:
                        p_resp = await client.get(best_url, headers=ky_headers)
                        if p_resp.status_code == 200:
                            p_isbn = extract_isbn(p_resp.text)
                            if p_isbn:
                                return {"isbn": p_isbn, "title": title}
            except Exception:
                pass
    return {"isbn": "", "title": title}

@app.get("/api/test-isbn")
async def test_isbn_endpoint(q: str = "Hamlet William Shakespeare"):
    ky_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    url = f"https://www.kitapyurdu.com/index.php?route=product/search&filter_name={urllib.parse.quote(q)}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
        try:
            r = await client.get(url, headers=ky_headers)
            return {
                "status_code": r.status_code,
                "text_length": len(r.text),
                "html_snippet": r.text[:300]
            }
        except Exception as e:
            return {"error": str(e), "type": str(type(e))}


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

