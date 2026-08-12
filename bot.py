import re
import json
import os
import time
import sqlite3
import logging
import requests
import feedparser
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN")
CHANNEL_ID  = os.environ.get("CHANNEL_ID")
CHECK_EVERY = 2
DB_PATH     = "seen.db"

FIRST_RUN = True   # ← saat True, send_telegram() tidak akan mengirim apa pun (mode diam untuk baseline)

if not BOT_TOKEN or not CHANNEL_ID:
    raise ValueError("BOT_TOKEN dan CHANNEL_ID harus diisi di Railway Variables!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── KEYWORDS ──────────────────────────────────────────────────────────────────
KEYWORDS = [
    # Delisting
    "delist", "delisting", "will delist", "to delist",
    "removal", "remove trading pair", "trading pair removal",
    "cease trading", "suspend trading", "discontinue", "cease support", "temporarily closed", "monitoring tag", "ST",
    # Migration & Contract
    "migration", "migrate", "token migration",
    "contract change", "contract address", "new contract",
    "token swap", "token rebranding", "rebrand",
    # Ticker & Symbol
    "ticker change", "ticker symbol", "symbol change",
    "rename", "rebranding", "Tick Size",
    # Network & Upgrade
    "network upgrade", "network support termination",
    "mainnet upgrade", "mainnet launch",
    "hard fork", "hardfork", "hard-fork",
    "chain upgrade", "protocol upgrade",
    "software upgrade", "node upgrade", "suspending", "resuming", "suspend", "resume",
    # Deposit/withdrawal
    "disable", "disabled", "suspend deposit", "suspend withdrawal", "Completes Integration", "maintenance", "wallet maintenance",
    # Snapshot
    "snapshot", "airdrop snapshot",
    # Notice
    "notice of removal", "important notice", "Investment Warning",
]

# ─── CEX SOURCES ───────────────────────────────────────────────────────────────
SOURCES = [
    # ── Binance ──
    {
        "name": "Binance",
        "type": "binance_api",
        "catalog_id": 161,
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=161",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "Binance",
        "type": "binance_api",
        "catalog_id": 157,
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=157",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    {
        "name": "Binance",
        "type": "binance_api",
        "catalog_id": 49,
        "url": "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=49",
        "logo": "🟡",
        "base_link": "https://www.binance.com/en/support/announcement/",
    },
    # ── Bybit (RSS) ──
    {
        "name": "Bybit",
        "type": "rss",
        "url": "https://announcements.bybit.com/en-US/rss/?category=delistings&page=1",
        "logo": "🟠",
    },
    {
        "name": "Bybit",
        "type": "rss",
        "url": "https://announcements.bybit.com/en-US/rss/?category=maintenance_updates&page=1",
        "logo": "🟠",
    },
    # ── OKX ──
    {
        "name": "OKX",
        "type": "scrape",
        "url": "https://www.okx.com/help/section/announcements-latest-announcements",
        "logo": "⚫",
    },
    # ── KuCoin ──
    {
        "name": "KuCoin",
        "type": "kucoin_api",
        "url": "https://api.kucoin.com/api/ua/v1/market/announcement?annType=latest-announcements&lang=en_US&page=1&pageSize=20",
        "logo": "🟢",
    },
    # ── Gate.io ──
    {
        "name": "Gate.io",
        "type": "gate_scrape",
        "url": "https://www.gate.com/announcements/lastest",
        "logo": "🔵",
    },
    # ── MEXC ──
    {
        "name": "MEXC",
        "type": "scrape",
        "url": "https://www.mexc.com/announcements/all",
        "logo": "🔷",
    },
    # ── Bitget ──
    {
        "name": "Bitget",
        "type": "bitget_scrape",
        "url": "https://www.bitget.com/support/sections/12508313443483",
        "logo": "🟣",
        "base_link": "https://www.bitget.com",
    },
    # ── Poloniex ──
    {
        "name": "Poloniex",
        "type": "scrape",
        "url": "https://support.poloniex.com/hc/en-us/sections/360006455114-Latest-Announcements",
        "logo": "🔴",
    },
    # ── HTX ──
    {
        "name": "HTX",
        "type": "scrape",
        "url": "https://www.htx.com/support/",
        "logo": "🟤",
    },
        # ── Upbit ──
    {
        "name": "Upbit",
        "type": "upbit_api",
        "url": "https://pub-info.upbit.com/api/v1/announcements",
        "logo": "♦️",
        "base_link": "https://upbit.com/service_center/notice?id=",
    },
]

# ─── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            id      TEXT PRIMARY KEY,
            seen_at TEXT
        )
    """)
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

# ─── KEYWORD CHECK ─────────────────────────────────────────────────────────────
def is_relevant(text):
    return any(kw in text.lower() for kw in KEYWORDS)

# ─── TELEGRAM SENDER ───────────────────────────────────────────────────────────
def send_telegram(message):
    if FIRST_RUN:
        return   # masih baseline, jangan kirim notifikasi apa pun
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
    return f"{logo} <b>[{cex}]</b>\n{title}\n🔗 <a href='{link}'>Announcement</a>"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API Binance: {e}")


def fetch_rss(source: dict):
    log.info(f"📶 Cek RSS: {source['name']}")
    try:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries:
            title = entry.get("title", "")
            link  = entry.get("link", "")
            uid   = entry.get("id", link)
            if not uid or not is_relevant(title):
                continue
            if is_seen(uid):
                continue
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error RSS {source['name']}: {e}")


def fetch_gate_scrape(source):
    log.info(f"🕷️  Scrape: Gate.io")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            r.text, re.DOTALL
        )
        if not match:
            log.error("❌ Gate.io: __NEXT_DATA__ tidak ditemukan")
            return

        data = json.loads(match.group(1))
        articles = (
            data.get("props", {})
                .get("pageProps", {})
                .get("listData", {})
                .get("list", [])
        )
        log.info(f"   → {len(articles)} artikel ditemukan")

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
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape Gate.io: {e}")


def fetch_kucoin_api(source):
    log.info("🔌 Cek API: KuCoin")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        data = r.json()
        items = data.get("data", {}).get("list", [])
        log.info(f"   → {len(items)} artikel ditemukan")
        for item in items:
            title = item.get("title", "")
            uid   = str(item.get("id", ""))
            url   = item.get("url", f"https://www.kucoin.com/announcement/{uid}")
            if not uid or not is_relevant(title):
                continue
            uid_key = f"kucoin_{uid}"
            if is_seen(uid_key):
                continue
            mark_seen(uid_key)
            send_telegram(format_message(source["logo"], source["name"], title, url))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API KuCoin: {e}")


def fetch_scrape(source):
    log.info(f"🕷️  Scrape: {source['name']}")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        seen_uids = set()
        for a in soup.find_all("a", href=True):
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
            uid = href
            if uid in seen_uids or is_seen(uid):
                continue
            seen_uids.add(uid)
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, href))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape {source['name']}: {e}")


def fetch_upbit_api(source):
    log.info("🔌 Cek API: Upbit")
    try:
        headers = {
            **HEADERS,
            "Referer": "https://www.upbit.com/",
            "Accept": "application/json",
        }
        r = requests.get(
            source["url"],
            params={"os": "web", "page": 1, "per_page": 20, "category": "all"},
            headers=headers, timeout=15
        )
        data = r.json()
        if not data.get("success"):
            log.error(f"❌ Upbit API success=false: {data}")
            return

        notices = data.get("data", {}).get("notices", [])
        log.info(f"   → {len(notices)} artikel ditemukan")

        for n in notices:
            title = n.get("title", "")
            nid = n.get("id", "")
            if not nid:
                continue

            # Fokus khusus: hanya "Investment Warning"
            if "investment warning" not in title.lower():
                continue

            uid = f"upbit_{nid}"
            if is_seen(uid):
                continue
            mark_seen(uid)
            link = f"{source['base_link']}{nid}"
            send_telegram(format_message(source["logo"], source["name"], title, link))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error API Upbit: {e}")


def fetch_bitget_scrape(source):
    """Scrape halaman Announcements Bitget, filter suspending/resuming."""
    log.info(f"🕷️  Scrape: Bitget")
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        seen_uids = set()
        links = soup.find_all("a", href=True)
        log.info(f"   → total <a>={len(links)}")

        for a in links:
            href = a["href"]
            if "/support/articles/" not in href:
                continue

            title = a.get_text(strip=True)
            if len(title) < 10 or not is_relevant(title):
                continue

            if href.startswith("/"):
                href = source["base_link"] + href

            uid = f"bitget_{href.rstrip('/').split('/')[-1]}"
            if uid in seen_uids or is_seen(uid):
                continue

            seen_uids.add(uid)
            mark_seen(uid)
            send_telegram(format_message(source["logo"], source["name"], title, href))
            time.sleep(1)
    except Exception as e:
        log.error(f"❌ Error scrape Bitget: {e}")

# ─── MAIN JOB ──────────────────────────────────────────────────────────────────
def check_all():
    log.info("🔄 Mulai pengecekan semua CEX...")
    for source in SOURCES:
        t = source["type"]
        if t == "binance_api":
            fetch_binance_api(source)
        elif t == "rss":
            fetch_rss(source)
        elif t == "gate_scrape":
            fetch_gate_scrape(source)
        elif t == "kucoin_api":
            fetch_kucoin_api(source)
        elif t == "bitget_scrape":
            fetch_bitget_scrape(source)
        elif t == "scrape":
            fetch_scrape(source)
        elif t == "upbit_api":
            fetch_upbit_api(source)
    log.info("✅ Selesai pengecekan.")

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    log.info("🚀 Bot dimulai!")

    log.info("📋 Merekam pengumuman yang sudah ada (tanpa kirim)...")
    check_all()          # FIRST_RUN masih True → semua send_telegram() di-skip, tapi mark_seen() tetap jalan

    FIRST_RUN = False    # baseline selesai, mulai kirim notifikasi normal
    log.info("✅ Baseline selesai, mulai monitoring normal.")
    send_telegram("🤖 <b>Crypto CEX Alarm Bot aktif!</b> 🚀")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(check_all, "interval", minutes=CHECK_EVERY, max_instances=1, coalesce=True)
    scheduler.start()
