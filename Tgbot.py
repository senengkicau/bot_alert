import re
import json
import os
import sys
import time
import sqlite3
import logging
import requests
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ.get("BOT_TOKEN")
CHANNEL_ID    = os.environ.get("LISTING_CHANNEL_ID")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY")
CHECK_EVERY   = 2
DB_PATH       = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".") + "/seen_listing.db"

if not BOT_TOKEN or not CHANNEL_ID:
    raise ValueError("BOT_TOKEN dan LISTING_CHANNEL_ID harus diisi di Railway Variables!")

DEEPL_API_URL = (
    "https://api-free.deepl.com/v2/translate"
    if DEEPL_API_KEY and DEEPL_API_KEY.endswith(":fx")
    else "https://api.deepl.com/v2/translate"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger(__name__)

if not DEEPL_API_KEY:
    log.warning("⚠️ DEEPL_API_KEY tidak diisi — translation akan dilewati (pakai teks asli).")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ─── KEYWORDS ──────────────────────────────────────────────────────────────────
KEYWORDS = [
    "new listing", "will list", "to list", "world premiere",
    "seed tag", "trading pairs", "tokenized stocks", "convert trading",
    "spot trading", "kucoin spot", "binance spot",
]

def is_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in KEYWORDS)

def normalize_uid(href: str) -> str:
    m = re.match(r'^(.*)-\d{8,}$', href.rstrip('/'))
    return m.group(1) if m else href

# ─── TRANSLATION HELPER (DeepL, sama seperti bot lama) ─────────────────────────
BAD_TRANSLATION_MARKERS = [
    "that's an error", "that’s an error",
    "error 500", "error 404", "error 403",
    "that's all we know", "that’s all we know",
    "<html", "<!doctype", "<body",
]

def _is_bad_translation(result: str, original: str) -> bool:
    if not result:
        return True
    if len(result) > max(200, len(original) * 5):
        return True
    low = result.lower()
    return any(marker in low for marker in BAD_TRANSLATION_MARKERS)

def _deepl_translate(text: str, target_lang: str) -> str:
    if not text or not DEEPL_API_KEY:
        return text
    try:
        r = requests.post(
            DEEPL_API_URL,
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"},
            data={"text": text, "target_lang": target_lang},
            timeout=(5, 15),
        )
        r.raise_for_status()
        translations = r.json().get("translations", [])
        if not translations:
            log.warning("⚠️ Respons DeepL tanpa hasil translation, pakai teks asli.")
            return text
        result = translations[0].get("text", text)
        if _is_bad_translation(result, text):
            log.warning(f"⚠️ Gagal translate, pakai judul asli. Raw: {str(result)[:80]}")
            return text
        return result
    except Exception as e:
        log.error(f"⚠️ Gagal translate ke {target_lang} (DeepL): {e}")
        return text

def translate_to_zh(text: str) -> str:
    return _deepl_translate(text, "ZH")

# ─── SOURCES ───────────────────────────────────────────────────────────────────
SOURCES = [
    {
        "name": "Binance",
        "type": "binance_api",
        "catalog_id": 48,
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=48",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "OKX",
        "type": "scrape",
        "url": "https://www.okx.com/help/section/announcements-new-listings",
        "logo": "⚫",
    },
    {
        "name": "Bybit",
        "type": "scrape",
        "url": "https://announcements.bybit.com/en/?category=new_crypto&page=1",
        "logo": "🟠",
    },
    {
        "name": "Gate-io",
        "type": "gate_scrape",
        "url": "https://www.gate.com/announcements/newspotlistings",
        "category": "newspotlistings",
        "logo": "🔵",
    },
    {
        "name": "KuCoin",
        "type": "kucoin_api",
        "url": "https://api.kucoin.com/api/ua/v1/market/announcement?annType=new-listings&lang=en_US&page=1&pageSize=20",
        "logo": "🟢",
    },
]

# ─── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, seen_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    con.close()

def is_seen(uid):
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT 1 FROM seen WHERE id=?", (uid,)).fetchone()
    con.close()
    return row is not None

def mark_seen(uid):
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (uid, datetime.now(timezone.utc).isoformat()))
    con.commit()
    con.close()

def is_baseline_done():
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT value FROM meta WHERE key='baseline_done'").fetchone()
    con.close()
    return row is not None and row[0] == "1"

def set_baseline_done():
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('baseline_done', '1')")
    con.commit()
    con.close()

# ─── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_telegram(message, force=False):
    if not force and not is_baseline_done():
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("✅ Pesan terkirim ke channel")
    except Exception as e:
        log.error(f"❌ Gagal kirim ke Telegram: {e}")

def format_message(logo, cex, title, link):
    title_cn = translate_to_zh(title)
    return (
        f"{logo} <b>[{cex}]</b>\n"
        f"{title}\n"
        f"\n"
        f"[{cex}]\n"
        f"{title_cn}\n"
        f"🔗 <a href='{link}'>Announcement</a>"
    )

def notify(source, title, link):
    """Kirim notif. Saat baseline, lewati (tidak panggil DeepL sama sekali)."""
    if not is_baseline_done():
        return
    send_telegram(format_message(source["logo"], source["name"], title, link))
    time.sleep(1)

# ─── FETCHERS ──────────────────────────────────────────────────────────────────
def fetch_binance_api(source):
    cat = source.get("catalog_id", "?")
    log.info(f"🔌 Cek API: Binance (catalog {cat})")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        catalogs = data.get("data", {}).get("catalogs", [])
        articles = []
        if catalogs:
            for cat_data in catalogs:
                if str(cat_data.get("catalogId")) == str(source.get("catalog_id")):
                    articles = cat_data.get("articles", [])
                    break
            if not articles:
                for cat_data in catalogs:
                    articles.extend(cat_data.get("articles", []))
        else:
            articles = data.get("data", {}).get("articles", [])

        log.info(f"   → {len(articles)} artikel ditemukan")
        for article in articles:
            title = article.get("title", "")
            code  = article.get("code", "")
            if not code or not is_relevant(title):
                continue
            uid = f"binance_{code}"
            if is_seen(uid):
                continue
            mark_seen(uid)
            link = f"{source['base_link']}{code}"
            notify(source, title, link)
    except Exception as e:
        log.error(f"❌ Error API Binance: {e}")


def fetch_kucoin_api(source):
    log.info("🔌 Cek API: KuCoin")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        items = data.get("data", {}).get("list", [])
        log.info(f"   → {len(items)} artikel ditemukan")
        for item in items:
            title       = item.get("title", "")
            description = item.get("description", "")
            uid         = str(item.get("id", ""))
            url         = item.get("url") or f"https://www.kucoin.com/announcement/{uid}"
            if url.startswith("/"):
                url = "https://www.kucoin.com" + url
            combined_text = f"{title} {description}"
            if not uid or not is_relevant(combined_text):
                continue
            uid_key = f"kucoin_{uid}"
            if is_seen(uid_key):
                continue
            mark_seen(uid_key)
            notify(source, title, url)
    except Exception as e:
        log.error(f"❌ Error API KuCoin: {e}")


def fetch_scrape(source):
    """Scraper umum (dipakai OKX & Bybit)."""
    log.info(f"🕷️  Scrape: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=True)
        log.info(f"   → status:{r.status_code} | total <a>={len(links)}")

        seen_uids = set()
        matched = 0
        for a in links:
            href  = a["href"]
            title = a.get_text(separator=" ", strip=True)
            if len(title) < 15 or len(title) > 200:
                continue
            if not is_relevant(title):
                continue
            if href.startswith("/"):
                base = "/".join(source["url"].split("/")[:3])
                href = base + href
            elif not href.startswith("http"):
                continue
            uid = normalize_uid(href)
            if uid in seen_uids or is_seen(uid):
                continue
            seen_uids.add(uid)
            matched += 1
            mark_seen(uid)
            notify(source, title, href)
        log.info(f"   → {matched} artikel baru cocok keyword")
    except Exception as e:
        log.error(f"❌ Error scrape {source['name']}: {e}")


# ── Gate.io ──
_gate_build_id_cache = {"id": None}

HEADERS_GATE = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.gate.com/announcements/newspotlistings",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

def get_gate_build_id(page_url: str, force_refresh: bool = False):
    if _gate_build_id_cache["id"] and not force_refresh:
        return _gate_build_id_cache["id"]
    try:
        r = requests.get(page_url, headers={**HEADERS_GATE, "Accept": "text/html"}, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        build_id = None
        if tag and tag.string:
            try:
                build_id = json.loads(tag.string).get("buildId")
            except json.JSONDecodeError:
                build_id = None
        if not build_id:
            m = re.search(r'"buildId"\s*:\s*"([^"]+)"', r.text)
            build_id = m.group(1) if m else None
        if build_id:
            _gate_build_id_cache["id"] = build_id
            log.info(f"   → Gate.io build ID terdeteksi: {build_id}")
        else:
            log.error("❌ Gate.io: tidak menemukan buildId di halaman (struktur mungkin berubah)")
        return build_id
    except Exception as e:
        log.error(f"❌ Gagal ambil Gate.io build ID: {e}")
        return None

def _gate_process_articles(source, articles):
    for a in articles:
        title = a.get("title", "")
        aid = a.get("id", "")
        url_path = a.get("url", "")
        if not aid or len(title) < 10 or not is_relevant(title):
            continue
        uid = f"gate_{aid}"
        if is_seen(uid):
            continue
        mark_seen(uid)
        link = f"https://www.gate.com{url_path}" if url_path else f"https://www.gate.com/announcements/article/{aid}"
        notify(source, title, link)

def fetch_gate_scrape(source):
    log.info("🕷️  Scrape: Gate.io")
    category = source.get("category", "newspotlistings")
    try:
        build_id = get_gate_build_id(source["url"])
        if not build_id:
            log.error("❌ Gate.io: tidak bisa lanjut tanpa build ID")
            return

        def build_url(bid):
            return f"https://www.gate.com/announcements/_next/data/{bid}/en/announcements/{category}.json?category={category}"

        r = requests.get(build_url(build_id), headers=HEADERS_GATE, timeout=15)
        log.info(f"   → status: {r.status_code} | len: {len(r.text)}")

        if r.status_code == 404:
            log.warning("⚠️ Gate.io: build ID expired, mengambil ulang otomatis...")
            build_id = get_gate_build_id(source["url"], force_refresh=True)
            if not build_id:
                log.error("❌ Gate.io: gagal refresh build ID")
                return
            r = requests.get(build_url(build_id), headers=HEADERS_GATE, timeout=15)
            log.info(f"   → status (setelah refresh): {r.status_code} | len: {len(r.text)}")

        if r.status_code != 200:
            log.error(f"❌ Gate.io: status code {r.status_code}")
            log.error(f"   cuplikan: {r.text[:300]}")
            return

        data = r.json()
        articles = data.get("pageProps", {}).get("listData", {}).get("list", [])
        log.info(f"   → {len(articles)} artikel ditemukan")
        _gate_process_articles(source, articles)
    except Exception as e:
        log.error(f"❌ Error scrape Gate.io: {e}")

# ─── MAIN JOB ──────────────────────────────────────────────────────────────────
FETCHERS = {
    "binance_api": fetch_binance_api,
    "kucoin_api": fetch_kucoin_api,
    "gate_scrape": fetch_gate_scrape,
    "scrape": fetch_scrape,
}

def check_all():
    log.info("🔄 Mulai pengecekan new listing semua CEX...")
    for source in SOURCES:
        fn = FETCHERS.get(source["type"])
        if fn:
            fn(source)
    log.info("✅ Selesai pengecekan.")

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    log.info("🚀 Bot New Listing dimulai!")

    if not is_baseline_done():
        log.info("📋 Merekam pengumuman yang sudah ada (tanpa kirim)...")
        check_all()
        set_baseline_done()
        log.info("✅ Baseline selesai, mulai monitoring normal.")
    else:
        log.info("ℹ️ Baseline sudah pernah dijalankan sebelumnya, langsung mode normal.")

    send_telegram("🤖 <b>Crypto New Listing Bot aktif!</b> 🚀")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(check_all, "interval", minutes=CHECK_EVERY, max_instances=1, coalesce=True)
    scheduler.start()
