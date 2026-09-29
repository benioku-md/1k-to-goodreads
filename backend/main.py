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
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

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
    "Title", "Author", "ISBN", "My Rating", "Date Read", "Bookshelves", "Exclusive Shelf", "Read Count", "My Review"
]

class ExportRequest(BaseModel):
    username: str
    shelf: Optional[str] = "hepsi"
    include_reviews: Optional[bool] = False

class JobState:
    def __init__(self, job_id: str, username: str, shelf: str = "hepsi", include_reviews: bool = False):
        self.job_id: str = job_id
        self.username: str = username
        self.shelf: str = shelf
        self.include_reviews: bool = include_reviews
        self.status: str = "queued"  # queued, scraping, resolving_isbn, completed, failed
        self.message: str = "Kuyruğa alındı. Sıranız: #1"
        self.queue_position: int = 1
        self.current_count: int = 0
        self.total_count: int = 0
        self.percent: int = 0
        self.last_book: Optional[dict] = None
        self.recent_books: List[dict] = []
        self.csv_bytes: Optional[bytes] = None
        self.error_message: Optional[str] = None
        self.preview_books: List[dict] = []
        self.cancelled: bool = False
        self.created_at: float = time.time()
        self.event_queues: List[asyncio.Queue] = []

    def to_dict(self):
        running_active = is_job_running_active(CURRENT_RUNNING_JOB_ID, self.job_id)
        idx = ACTIVE_QUEUE_LIST.index(self.job_id) if self.job_id in ACTIVE_QUEUE_LIST else 0
        people_ahead = ((1 if running_active else 0) + idx) if self.status == "queued" else 0

        estimated_seconds = None
        if self.status == "queued":
            if people_ahead > 0:
                if running_active:
                    active_job = JOBS.get(CURRENT_RUNNING_JOB_ID)
                    if active_job and active_job.total_count > 0:
                        rem_active = max(0, active_job.total_count - active_job.current_count)
                        active_sec = math.ceil(rem_active / 9) + 1
                    else:
                        active_sec = 25
                    estimated_seconds = active_sec + (max(0, people_ahead - 1) * 25)
                else:
                    estimated_seconds = max(1, people_ahead * 25)
            else:
                estimated_seconds = 0
        elif self.status not in ("completed", "failed", "cancelled", "queued"):
            rem = max(0, self.total_count - self.current_count) if self.total_count > 0 else 50
            estimated_seconds = max(1, math.ceil(rem / 9) + 1)

        return {
            "job_id": self.job_id,
            "status": self.status,
            "message": self.message,
            "queue_position": self.queue_position,
            "people_ahead": people_ahead,
            "estimated_seconds": estimated_seconds,
            "current_count": self.current_count,
            "total_count": self.total_count,
            "percent": self.percent,
            "last_book": self.last_book,
            "recent_books": self.recent_books,
            "preview_books": self.preview_books,
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
CURRENT_RUNNING_JOB_ID: Optional[str] = None
IP_REQUEST_TIMESTAMPS: Dict[str, List[float]] = {}
RATE_LIMIT_WINDOW = 60.0  # 60 saniye
MAX_REQUESTS_PER_WINDOW = 15  # IP başına dakikada maksimum 15 istek (flood kalkanı)

def is_job_running_active(job_id_to_check: Optional[str], exclude_job_id: Optional[str] = None) -> bool:
    """Belirtilen görevin şu anda işçi havuzunda aktif olarak taranıp taranmadığını kesin olarak belirler."""
    if not job_id_to_check or job_id_to_check == exclude_job_id:
        return False
    job = JOBS.get(job_id_to_check)
    if not job or job.cancelled:
        return False
    return job.status not in ("completed", "failed", "cancelled")

def validate_username(username: str) -> Tuple[bool, str]:
    """1000Kitap kullanıcı adı kurallarını doğrular."""
    if not username:
        return False, "Lütfen bir kullanıcı adı girin."
    if len(username) < 4:
        return False, "Kullanıcı adı en az 4 karakter olmalıdır."
    if len(username) > 30:
        return False, "Kullanıcı adı en fazla 30 karakter olabilir."
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, "Kullanıcı adınızda alt tire (_) dışında özel karakter ve Türkçe karakter olmamalıdır."
    if re.match(r'^\d+$', username):
        return False, "Kullanıcı adı sadece rakamlardan oluşamaz."
    return True, ""

def get_client_ip(request: Request) -> str:
    """İstemcinin gerçek IP adresini tespit eder (Cloudflare Tunnel, Proxy veya Doğrudan)."""
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    x_forwarded = request.headers.get("x-forwarded-for")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

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
    """
    1000Kitap ekBilgi alanındaki Türkçe tarihi YYYY/MM/DD formatına çevirir.
    Hem geçmiş yılları (örn: '23 Ara 2025 · 18 günde okudu')
    hem de içinde bulunulan yılı (örn: '17 Eyl 12:40 · 35 günde okudu', '05 Nis 00:19', 'Bugün', 'Dün') destekler.
    """
    if not ek_bilgi:
        return ""
    
    now = datetime.now()
    current_year = str(now.year)
    
    # 1. Tam Tarih (Gün Ay Yıl) -> Örn: "09 Eki 2019", "23 Ara 2025"
    match = re.search(r'(\d{1,2})\s+([A-Za-zÇŞĞÖÜıİçşğöü]+)\s+(\d{4})', ek_bilgi)
    if match:
        day = match.group(1).zfill(2)
        month_name = match.group(2)
        year = match.group(3)
        month = MONTH_MAP.get(month_name, "01")
        return f"{year}/{month}/{day}"
    
    # 2. Mevcut Yıl Tarihi (Gün Ay [Saat]) -> Örn: "17 Eyl 12:40", "05 Nis 00:19", "17 Eyl"
    # (1000Kitap mevcut yılda yılı yazmaz, onun yerine saat veya sadece gün-ay yazar)
    match_cur = re.search(r'(?:^|[·\s])(\d{1,2})\s+([A-Za-zÇŞĞÖÜıİçşğöü]+)(?:\s+\d{1,2}:\d{2})?', ek_bilgi)
    if match_cur:
        day_str = match_cur.group(1).zfill(2)
        month_candidate = match_cur.group(2)
        if month_candidate in MONTH_MAP:
            month = MONTH_MAP[month_candidate]
            return f"{current_year}/{month}/{day_str}"
    
    # 3. Göreceli Tarihler (Bugün, Dün, X gün önce)
    ek_lower = ek_bilgi.lower()
    if "bugün" in ek_lower:
        return now.strftime("%Y/%m/%d")
    if "dün" in ek_lower:
        yesterday = now - timedelta(days=1)
        return yesterday.strftime("%Y/%m/%d")
    
    days_ago = re.search(r'(\d+)\s+gün\s+önce', ek_lower)
    if days_ago:
        past_date = now - timedelta(days=int(days_ago.group(1)))
        return past_date.strftime("%Y/%m/%d")
    
    if "saat önce" in ek_lower or "dakika önce" in ek_lower:
        return now.strftime("%Y/%m/%d")
    
    return ""

def parse_read_count(item: dict, ek_bilgi: str) -> int:
    """
    1000Kitap'taki tekrar okuma sayısını tespit eder.
    Varsayılan 1'dir.
    """
    durum_btn = item.get("durumBtn") or item.get("okumaDurumuButon")
    if isinstance(durum_btn, dict):
        cnt = durum_btn.get("okumaSayisi", 0)
        if isinstance(cnt, int) and cnt > 1:
            return cnt
    if ek_bilgi:
        m = re.search(r'(\d+)\.\s*kez okudu', ek_bilgi.lower())
        if m:
            return int(m.group(1))
    return 1

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

def extract_text_from_token(token) -> str:
    """1000Kitap rich-text token nesnelerini ([object Object] olmadan) saf metne çevirir."""
    if token is None:
        return ""
    if isinstance(token, str):
        return token
    if isinstance(token, (int, float)):
        return str(token)
    if isinstance(token, dict):
        if "adi" in token and token["adi"]:
            return str(token["adi"])
        if "baslik" in token and token["baslik"]:
            return str(token["baslik"])
        if "name" in token and token["name"]:
            return str(token["name"])
        if "text" in token and token["text"]:
            return str(token["text"])
        if "kadi" in token and token["kadi"]:
            return f"@{token['kadi']}"
        if "uye" in token and isinstance(token["uye"], dict):
            uye = token["uye"]
            if uye.get("kadi"):
                return f"@{uye['kadi']}"
            if uye.get("adi"):
                return str(uye["adi"])
        if "kitap" in token and isinstance(token["kitap"], dict) and token["kitap"].get("adi"):
            return str(token["kitap"]["adi"])
        if "yazar" in token and isinstance(token["yazar"], dict) and token["yazar"].get("adi"):
            return str(token["yazar"]["adi"])
        if "parse" in token and isinstance(token["parse"], list):
            return "".join(extract_text_from_token(t) for t in token["parse"])
    if isinstance(token, list):
        return "".join(extract_text_from_token(t) for t in token)
    return ""

def clean_review_text(yorum_data: dict) -> str:
    """1000Kitap yorum yapısını temizleyip Goodreads için maksimum 20.000 karaktere sınırlar."""
    if not yorum_data or not isinstance(yorum_data, dict):
        return ""
    uzun = yorum_data.get("yorumUzunParse")
    if uzun and isinstance(uzun, dict) and "parse" in uzun:
        t = extract_text_from_token(uzun["parse"])
        if t.strip():
            return t.strip()[:20000]
    yp = yorum_data.get("yorumParse")
    if yp and isinstance(yp, dict) and "parse" in yp:
        t = extract_text_from_token(yp["parse"])
        if t.strip():
            return t.strip()[:20000]
    raw = yorum_data.get("metin") or yorum_data.get("icerik") or ""
    return str(raw).strip()[:20000]

async def fetch_user_reviews(session, username: str, headers: dict) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    1000Kitap mobil API'sinden kullanıcının yazdığı tüm incelemeleri çeker.
    Dönen yapıyı (reviews_by_book_id, reviews_by_book_title) olarak döndürür.
    """
    reviews_by_id: Dict[str, str] = {}
    reviews_by_title: Dict[str, str] = {}
    url = "https://api.1000kitap.com/v2/okurlar/okurCekV2"
    page = 1
    has_more = True
    max_pages = 50

    while has_more and page <= max_pages:
        params = {
            "id": username,
            "bolum": "incelemeler",
            "sayfa": page,
            "appVersion": "2.60.60",
            "os": "android",
            "hl": "tr"
        }
        try:
            resp = await session.get(url, params=params, headers=headers, timeout=6.0)
            if resp.status_code != 200:
                break
            data = resp.json()
            sonuc = data.get("_sonuc") or data.get("sonuc") or {}
            liste = sonuc.get("liste", [])
            if not liste:
                break

            for item in liste:
                if item.get("turu") != "yorumlar" and "yorumlar" not in item.get("alt", {}):
                    continue
                kitap = item.get("alt", {}).get("kitaplar", {})
                yorum = item.get("alt", {}).get("yorumlar", {})
                if not kitap.get("adi"):
                    continue

                review_text = clean_review_text(yorum)
                if not review_text:
                    continue

                kitap_id = str(kitap.get("id") or "")
                ana_id = str(kitap.get("anaKitapId") or "")
                kitap_adi_clean = re.sub(r'\s+', ' ', kitap.get("adi", "").lower().strip())

                if kitap_id:
                    reviews_by_id[kitap_id] = review_text
                if ana_id and ana_id != kitap_id:
                    reviews_by_id[ana_id] = review_text
                if kitap_adi_clean:
                    reviews_by_title[kitap_adi_clean] = review_text

            has_more = bool(sonuc.get("hasMore", False))
            page += 1
            if has_more:
                await asyncio.sleep(0.3)
        except Exception:
            break

    return reviews_by_id, reviews_by_title

async def scrape_user_books(job: JobState):
    """
    1000Kitap API'sini saniyede maksimum 2 istek hız sınırlamasıyla tarar,
    kitapları ayrıştırır ve UTF-8 BOM'lu Goodreads CSV'si üretir.
    Eşzamanlı 10 iş parçacıklı hızlı Kitapyurdu motoru ile anında ISBN çözer.
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
                if job.cancelled:
                    isbn_queue.task_done()
                    break
                book = await isbn_queue.get()
                if book is None or job.cancelled:
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
                        if cf_blocked or job.cancelled:
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
                            await asyncio.sleep(0.02)
                        except Exception:
                            pass

                    # Yabanci datacenter engeli varsa, varsa harici cozumleyici uzerinden coz
                    fallback_resolver = os.getenv("FALLBACK_RESOLVER_URL", "")
                    if not found_isbn and cf_blocked and fallback_resolver and not job.cancelled:
                        try:
                            b_resp = await client.get(fallback_resolver, params={"title": raw_title, "author": raw_author}, timeout=4.0)
                            if b_resp.status_code == 200:
                                found_isbn = b_resp.json().get('isbn', '')
                        except Exception:
                            pass

                if found_isbn:
                    book["isbn"] = found_isbn

                resolved_count += 1
                isbn_queue.task_done()

                target_total = job.total_count if job.total_count > 0 else (len(collected_books) or total_books or 1)
                pct_isbn = min(99, int((resolved_count / target_total) * 100))

                # Her 10 kitapta 1 gorsel gondererek sunucuyu ve tarayiciyi rahatlat
                include_cover = (resolved_count % 10 == 0) or (resolved_count == target_total)
                cover_to_send = book.get("cover", "") if include_cover else ""

                job.status = "resolving_isbn"
                job.current_count = resolved_count
                job.percent = pct_isbn
                job.message = f"ISBN numaraları tamamlanıyor: {resolved_count} / {target_total} (%{pct_isbn})..."
                if raw_title:
                    existing_cover = job.last_book.get("cover") if job.last_book else ""
                    isbn_val = book.get("isbn") or ""
                    rating_val = book.get("rating") or ""
                    job.last_book = {
                        "title": raw_title,
                        "author": raw_author,
                        "cover": cover_to_send or existing_cover,
                        "isbn": isbn_val,
                        "rating": rating_val
                    }
                    recent_entry = {
                        "title": raw_title,
                        "author": raw_author,
                        "isbn": isbn_val,
                        "rating": rating_val
                    }
                    job.recent_books = [recent_entry] + [b for b in job.recent_books if b.get("title") != raw_title][:2]

                rem_target = max(0, target_total - resolved_count)
                eta_sec = max(1, math.ceil(rem_target / 9) + 1)

                await job.broadcast({
                    "type": "progress",
                    "status": "resolving_isbn",
                    "message": job.message,
                    "current": resolved_count,
                    "total": target_total,
                    "percent": pct_isbn,
                    "estimated_seconds": eta_sec,
                    "last_book": job.last_book,
                    "recent_books": job.recent_books
                })

        shelves_to_process = []
        if job.shelf == "okuyacaklari":
            shelves_to_process = [("okuyacaklari", "to-read", "Okumak İstediklerim")]
        elif job.shelf == "okuduklari":
            shelves_to_process = [
                ("okuduklari", "read", "Okuduklarım"),
                ("okuyorOlduklari", "currently-reading", "Şu An Okuduklarım")
            ]
        elif job.shelf == "okuyorOlduklari":
            shelves_to_process = [("okuyorOlduklari", "currently-reading", "Şu An Okuduklarım")]
        else:  # "hepsi"
            shelves_to_process = [
                ("okuduklari", "read", "Okuduklarım"),
                ("okuyorOlduklari", "currently-reading", "Şu An Okuduklarım"),
                ("okuyacaklari", "to-read", "Okumak İstediklerim")
            ]

        review_task = None
        if job.include_reviews:
            review_task = asyncio.create_task(fetch_user_reviews(session, job.username, headers))

        ky_limits = httpx.Limits(max_keepalive_connections=35, max_connections=50)
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0, limits=ky_limits) as ky_client:
            # 10 eszamanli paralel isci ile ultra hizli tarama
            ky_workers = [asyncio.create_task(ky_worker(ky_client)) for _ in range(10)]

            for shelf_idx, (shelf_slug, goodreads_shelf, shelf_display) in enumerate(shelves_to_process):
                page = 1
                kume = ""
                has_more = True

                while has_more:
                    if job.cancelled:
                        raise Exception("İşlem kullanıcı tarafından iptal edildi.")

                    params = {
                        "kadi": job.username,
                        "raf": shelf_slug,
                        "sayfa": page,
                        "appVersion": "2.60.60",
                        "os": "android",
                        "hl": "tr"
                    }
                    if kume:
                        params["kume"] = kume

                    response = None
                    for retry in range(2):
                        if job.cancelled:
                            raise Exception("İşlem kullanıcı tarafından iptal edildi.")
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
                            if len(shelves_to_process) > 1:
                                break
                            raise Exception(
                                f"Bu kullanıcının '{shelf_display}' rafı gizlidir (Sadece okurun kendisi görebilir). "
                                "Aktarım yapabilmek için 1000Kitap Profil Ayarları ➔ Gizlilik bölümünden "
                                "ilgili rafı herkese açık yapıp tekrar deneyin."
                            )
                        else:
                            raise Exception(f"1000Kitap Bildirimi: {hata_metni}")

                    if shelf_idx == 0 and page == 1:
                        kitaplik_bilgiler = sonuc.get("kitaplikBilgiler", {})
                        raflar = kitaplik_bilgiler.get("raflar", [])
                        wanted_slugs = {s[0] for s in shelves_to_process}
                        total_from_shelves = 0
                        for r in raflar:
                            if r.get("seo") in wanted_slugs:
                                bilgi_txt = r.get("bilgi", "")
                                digits = re.findall(r"\d+", bilgi_txt.replace(".", "").replace(",", ""))
                                if digits:
                                    total_from_shelves += int(digits[0])
                        if total_from_shelves > 0:
                            total_estimated = total_from_shelves
                        elif total_estimated == 0 and sonuc.get("baslikMini"):
                            digits = re.findall(r"\d+", str(sonuc.get("baslikMini")).replace(".", "").replace(",", ""))
                            if digits:
                                total_estimated = int(digits[0])

                        job.total_count = total_estimated

                    raw_list = sonuc.get("liste", [])
                    if not raw_list:
                        break

                    for item in raw_list:
                        if job.cancelled:
                            break
                        if item.get("renderTuru") == "reklam" or not item.get("adi"):
                            continue

                        title = item.get("adi", "").strip()
                        author = item.get("yazarAdi") or item.get("ilkYazar") or ""
                        if not author and item.get("yazarlar"):
                            author = item["yazarlar"][0].get("adi", "")

                        ek_bilgi = item.get("ekBilgi", "")
                        if goodreads_shelf == "read":
                            date_read = parse_date(ek_bilgi)
                            user_rating = parse_rating(ek_bilgi)
                            read_count = parse_read_count(item, ek_bilgi)
                        else:
                            date_read = ""
                            user_rating = 0
                            read_count = 0

                        book_entry = {
                            "id": item.get("id"),
                            "seo_adi": item.get("seo_adi") or "",
                            "title": title,
                            "author": author,
                            "user_rating": user_rating,
                            "date_read": date_read,
                            "exclusive_shelf": goodreads_shelf,
                            "read_count": read_count,
                            "cover": item.get("resim") or item.get("resimB") or "",
                            "isbn": ""
                        }
                        collected_books.append(book_entry)
                        job.current_count = len(collected_books)
                        isbn_val = book_entry.get("isbn") or ""
                        rating_val = book_entry.get("rating") or ""
                        job.last_book = {
                            "title": title,
                            "author": author,
                            "cover": book_entry["cover"],
                            "isbn": isbn_val,
                            "rating": rating_val
                        }
                        recent_entry = {
                            "title": title,
                            "author": author,
                            "isbn": isbn_val,
                            "rating": rating_val
                        }
                        job.recent_books = [recent_entry] + [b for b in job.recent_books if b.get("title") != title][:2]

                        # Eşzamanlı boru hattı: Kitabı bekletmeden anında Kitapyurdu işçi havuzuna fırlat
                        await isbn_queue.put(book_entry)

                    if job.total_count > 0:
                        pct = int((job.current_count / job.total_count) * 100)
                        job.percent = min(99, pct)
                    else:
                        job.percent = min(95, len(collected_books) * 5)

                    job.message = f"{shelf_display} taranıyor: {job.current_count} / {job.total_count if job.total_count > 0 else '?'} (%{job.percent})..."
                    rem_scrape = max(0, (job.total_count or len(collected_books)) - job.current_count)
                    eta_scrape = max(1, math.ceil(rem_scrape / 9) + 1)

                    shelf_status = "scraping_read" if goodreads_shelf == "read" else ("scraping_to_read" if goodreads_shelf == "to-read" else "scraping_currently_reading")
                    await job.broadcast({
                        "type": "progress",
                        "status": shelf_status,
                        "shelf_display": shelf_display,
                        "shelf_type": goodreads_shelf,
                        "message": job.message,
                        "current": job.current_count,
                        "total": job.total_count,
                        "percent": job.percent,
                        "estimated_seconds": eta_scrape,
                        "last_book": job.last_book,
                        "recent_books": job.recent_books
                    })

                    has_more = bool(sonuc.get("hasMore", False))
                    kume = str(sonuc.get("kume", ""))
                    page += 1

                    if not has_more or not raw_list:
                        break

                    await asyncio.sleep(0.90)

            if not collected_books:
                raise Exception(
                    "Seçilen raflarda aktarılacak kitap bulunamadı. "
                    "Raflarınız boş veya gizli olabilir. Lütfen 1000Kitap Gizlilik ayarlarınızı kontrol edin."
                )

            total_books = len(collected_books)
            if job.total_count < total_books:
                job.total_count = total_books

            # İşçilere bitiş sinyali gönder ve hepsinin tamamlanmasını bekle
            for _ in range(10):
                await isbn_queue.put(None)
            await asyncio.gather(*ky_workers)

        # İncelemeleri bekle ve kitaplarla eşle
        if job.include_reviews and review_task:
            job.status = "fetching_reviews"
            job.message = "Kitap incelemeleri alınıyor ve eşleniyor..."
            await job.broadcast({
                "type": "progress",
                "status": "fetching_reviews",
                "message": job.message,
                "current": job.current_count,
                "total": job.total_count,
                "percent": job.percent,
                "estimated_seconds": 2
            })
            try:
                reviews_by_id, reviews_by_title = await review_task
                for b in collected_books:
                    b_id = str(b.get("id") or "")
                    b_title = re.sub(r'\s+', ' ', b.get("title", "").lower().strip())
                    b["my_review"] = reviews_by_id.get(b_id) or reviews_by_title.get(b_title, "")
            except Exception as e:
                pass

        # ======================================================================
        # 4.3. GOODREADS CSV ÇIKTISINI BELLEK ÜZERİNDE OLUŞTURMA
        # ======================================================================
        all_books_rows: List[List[str]] = []
        for b in collected_books:
            ex_shelf = b.get("exclusive_shelf", "read")
            is_read = (ex_shelf == "read")
            bookshelves_val = "" if is_read else ex_shelf
            row = [
                sanitize_csv_field(b["title"]),
                sanitize_csv_field(b["author"]),
                sanitize_csv_field(b["isbn"]),
                str(b["user_rating"]) if (is_read and b.get("user_rating", 0) > 0) else "",
                b.get("date_read", "") if is_read else "",
                bookshelves_val,
                ex_shelf,
                str(b.get("read_count", 1)) if is_read else "0",
                sanitize_csv_field(b.get("my_review", ""))
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

        # İlk 5 kitap için mini önizleme hazırla
        job.preview_books = []
        for b in collected_books[:5]:
            ex_shelf = b.get("exclusive_shelf", "read")
            shelf_label = "Okundu" if ex_shelf == "read" else ("Okunacak" if ex_shelf == "to-read" else "Şu An Okunuyor")
            job.preview_books.append({
                "title": b.get("title", ""),
                "author": b.get("author", ""),
                "rating": b.get("user_rating", 0),
                "shelf": shelf_label,
                "isbn": b.get("isbn", ""),
                "cover": b.get("cover", "")
            })

        await job.broadcast({
            "type": "completed",
            "status": "completed",
            "current": job.current_count,
            "total": job.total_count,
            "percent": 100,
            "preview_books": job.preview_books,
            "download_url": f"/api/jobs/{job.job_id}/download"
        })

    except Exception as e:
        if job.cancelled:
            job.status = "cancelled"
            job.message = "İşlem iptal edildi."
            await job.broadcast({
                "type": "cancelled",
                "status": "cancelled",
                "message": "İşlem kullanıcı tarafından iptal edildi."
            })
        else:
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
    global CURRENT_RUNNING_JOB_ID
    while True:
        job_id = await JOB_QUEUE.get()
        job = JOBS.get(job_id)
        if not job or job.cancelled:
            JOB_QUEUE.task_done()
            continue

        try:
            CURRENT_RUNNING_JOB_ID = job_id
            job.status = "scraping"
            # Sıradaki diğer bekleyen işlerin pozisyonlarını güncelle
            if job_id in ACTIVE_QUEUE_LIST:
                ACTIVE_QUEUE_LIST.remove(job_id)

            for idx, q_id in enumerate(ACTIVE_QUEUE_LIST):
                waiting_job = JOBS.get(q_id)
                if waiting_job and waiting_job.status == "queued":
                    people_ahead = 1 + idx  # 1 aktif taranan işlem + önündeki bekleyenler
                    waiting_job.queue_position = people_ahead + 1
                    msg = f"Kuyruktasınız (Önünüzde {people_ahead} kişi var)..."
                    waiting_job.message = msg

                    active_job = JOBS.get(job_id)
                    rem_act = max(0, active_job.total_count - active_job.current_count) if (active_job and active_job.total_count > 0) else 180
                    act_sec = math.ceil(rem_act / 9) + 1
                    est_sec = act_sec + (idx * 21)

                    await waiting_job.broadcast({
                        "type": "queued",
                        "status": "queued",
                        "position": waiting_job.queue_position,
                        "queue_position": waiting_job.queue_position,
                        "people_ahead": people_ahead,
                        "estimated_seconds": est_sec,
                        "message": msg
                    })

            job.status = "scraping"
            job.message = "Kitaplık taranmaya başlandı..."
            await job.broadcast({
                "type": "status",
                "status": "scraping",
                "message": job.message,
                "current": job.current_count,
                "total": job.total_count,
                "percent": job.percent,
                "estimated_seconds": job.estimated_seconds or 15
            })

            await scrape_user_books(job)

        except Exception as e:
            if job.cancelled:
                job.status = "cancelled"
                job.message = "İşlem iptal edildi."
            else:
                job.status = "failed"
                job.error_message = str(e)
                await job.broadcast({
                    "type": "error",
                    "status": "failed",
                    "error": str(e)
                })
        finally:
            CURRENT_RUNNING_JOB_ID = None
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
            if jid in ACTIVE_QUEUE_LIST:
                ACTIVE_QUEUE_LIST.remove(jid)

        # IP rate limiter temizliği (penceresi geçmiş kayıtları bellekten at)
        expired_ips = []
        for ip, timestamps in list(IP_REQUEST_TIMESTAMPS.items()):
            active_ts = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
            if active_ts:
                IP_REQUEST_TIMESTAMPS[ip] = active_ts
            else:
                expired_ips.append(ip)
        for ip in expired_ips:
            IP_REQUEST_TIMESTAMPS.pop(ip, None)

        if expired or expired_ips:
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
async def create_export_job(payload: ExportRequest, request: Request):
    """Yeni aktarma görevi oluşturur ve FIFO kuyruğuna ekler."""
    raw_user = (payload.username or "").strip()
    if raw_user.startswith("@"):
        raw_user = raw_user[1:]
    if "1000kitap.com/" in raw_user:
        try:
            raw_user = raw_user.split("1000kitap.com/")[1].split("/")[0].split("?")[0]
        except Exception:
            pass
    username = raw_user.strip().lower()

    # 1. Kullanıcı adı kuralı (Geçersizse IP kotası harcanmaz)
    is_valid, error_msg = validate_username(username)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # 2. IP Bazlı İstek Sınırı (Rate Limiting)
    client_ip = get_client_ip(request)
    now = time.time()
    timestamps = IP_REQUEST_TIMESTAMPS.get(client_ip, [])
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
    if len(timestamps) >= MAX_REQUESTS_PER_WINDOW:
        wait_seconds = max(1, int(RATE_LIMIT_WINDOW - (now - timestamps[0])))
        raise HTTPException(
            status_code=429,
            detail=f"Çok sık istek gönderdiniz. Lütfen {wait_seconds} saniye bekledikten sonra tekrar deneyin."
        )
    timestamps.append(now)
    IP_REQUEST_TIMESTAMPS[client_ip] = timestamps

    # 3. Mükerrer Kuyruk Koruması (Mahremiyet korumalı genel mesaj)
    for q_id in list(ACTIVE_QUEUE_LIST):
        existing_job = JOBS.get(q_id)
        if existing_job and existing_job.username == username:
            raise HTTPException(
                status_code=409,
                detail="Bu kullanıcı hesabı için zaten sırada bekleyen bir işlem var. Lütfen sıranın tamamlanmasını bekleyin."
            )

    for jid, j in list(JOBS.items()):
        if j.username == username and is_job_running_active(jid):
            raise HTTPException(
                status_code=409,
                detail="Bu kullanıcı hesabı şu anda taranıyor. Lütfen mevcut işlemin tamamlanmasını bekleyin."
            )

    job_id = uuid.uuid4().hex
    job = JobState(job_id=job_id, username=username, shelf=payload.shelf or "hepsi", include_reviews=bool(payload.include_reviews))
    JOBS[job_id] = job

    running_active = is_job_running_active(CURRENT_RUNNING_JOB_ID)
    people_ahead = (1 if running_active else 0) + len(ACTIVE_QUEUE_LIST)
    total_position = people_ahead + 1

    ACTIVE_QUEUE_LIST.append(job_id)
    job.queue_position = total_position

    if people_ahead == 0:
        job.message = "Sıranız: #1 (Aktarım başlatılıyor)..."
        job.estimated_seconds = 3
    else:
        job.message = f"Kuyruktasınız (Önünüzde {people_ahead} kişi var)..."
        if running_active:
            act = JOBS.get(CURRENT_RUNNING_JOB_ID)
            rem_act = max(10, act.total_count - act.current_count) if (act and act.total_count > 0) else 180
            act_sec = math.ceil(rem_act / 9) + 1
        else:
            act_sec = 0
        job.estimated_seconds = act_sec + (max(0, people_ahead - 1) * 22)

    await JOB_QUEUE.put(job_id)

    return {
        "job_id": job_id,
        "queue_position": job.queue_position,
        "people_ahead": people_ahead,
        "estimated_seconds": job.estimated_seconds,
        "message": job.message
    }

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Kullanıcının sıradan çıkmasını veya devam eden aktarımı iptal etmesini sağlar."""
    global CURRENT_RUNNING_JOB_ID
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="İşlem bulunamadı veya süresi doldu.")

    job.status = "cancelled"
    job.cancelled = True
    job.message = "İşlem iptal edildi."

    if job_id in ACTIVE_QUEUE_LIST:
        ACTIVE_QUEUE_LIST.remove(job_id)

    if CURRENT_RUNNING_JOB_ID == job_id:
        CURRENT_RUNNING_JOB_ID = None

    # Arkadaki kuyruktaki işlerin sıralarını ve sürelerini güncelle
    running_active = is_job_running_active(CURRENT_RUNNING_JOB_ID)
    for idx, q_id in enumerate(ACTIVE_QUEUE_LIST):
        waiting_job = JOBS.get(q_id)
        if waiting_job and waiting_job.status == "queued":
            people_ahead = (1 if running_active else 0) + idx
            waiting_job.queue_position = people_ahead + 1
            if people_ahead == 0:
                waiting_job.message = "Sıranız: #1 (Aktarım başlatılıyor)..."
            else:
                waiting_job.message = f"Kuyruktasınız (Önünüzde {people_ahead} kişi var)..."

            # Dinamik ETA
            if running_active:
                act = JOBS.get(CURRENT_RUNNING_JOB_ID)
                rem_act = max(0, act.total_count - act.current_count) if (act and act.total_count > 0) else 180
                act_sec = math.ceil(rem_act / 9) + 1
            else:
                act_sec = 0
            waiting_job.estimated_seconds = act_sec + (idx * 21)

            await waiting_job.broadcast({
                "type": "queued",
                "status": "queued",
                "position": waiting_job.queue_position,
                "queue_position": waiting_job.queue_position,
                "people_ahead": people_ahead,
                "estimated_seconds": waiting_job.estimated_seconds,
                "message": waiting_job.message
            })

    await job.broadcast({
        "type": "cancelled",
        "status": "cancelled",
        "message": "İşlem iptal edildi."
    })

    return {"status": "ok", "message": "İşlem başarıyla iptal edildi."}

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
        running_active = is_job_running_active(CURRENT_RUNNING_JOB_ID, job.job_id)
        idx = ACTIVE_QUEUE_LIST.index(job.job_id) if job.job_id in ACTIVE_QUEUE_LIST else 0
        people_ahead = ((1 if running_active else 0) + idx) if job.status == "queued" else 0
        job_info = job.to_dict()

        if job.status == "queued":
            current_pos = people_ahead + 1
            msg = f"Kuyruktasınız (Önünüzde {people_ahead} kişi var)..." if people_ahead > 0 else "Sıranız: #1 (Aktarım başlatılıyor)..."
        else:
            current_pos = job.queue_position
            msg = job.message

        initial_payload = {
            "type": "queued" if job.status == "queued" else "status",
            "status": job.status,
            "message": msg,
            "position": current_pos,
            "queue_position": current_pos,
            "people_ahead": people_ahead,
            "estimated_seconds": job_info.get("estimated_seconds"),
            "current": job.current_count,
            "total": job.total_count,
            "percent": job.percent,
            "last_book": job.last_book,
            "recent_books": job.recent_books,
            "preview_books": job.preview_books,
            "error": job.error_message
        }
        yield f"data: {json.dumps(initial_payload)}\n\n"

        if job.status == "completed":
            yield f"data: {json.dumps({'type': 'completed', 'status': 'completed', 'current': job.current_count, 'total': job.total_count, 'percent': 100, 'preview_books': job.preview_books, 'download_url': f'/api/jobs/{job.job_id}/download'})}\n\n"
            return

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(event_queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                    if payload.get("type") in ("completed", "error", "cancelled"):
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
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.get("/api/resolve-isbn")
async def resolve_isbn_api(title: str, author: str = ""): 
    """ISBN arama servisi."""
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

