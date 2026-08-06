# 🚨 Crypto CEX Alert Bot

Bot Telegram yang otomatis memantau announcement **delisting, migration, contract change, ticker change** dari berbagai CEX besar.

## CEX yang Dipantau
| CEX | Metode |
|-----|--------|
| Binance | RSS Feed |
| Bybit | RSS Feed |
| OKX | RSS Feed |
| KuCoin | RSS Feed |
| Gate.io | RSS Feed |
| MEXC | Web Scraping |
| BingX | Web Scraping |

---

## 🛠️ Setup Awal

### 1. Buat Telegram Bot
1. Buka Telegram, cari **@BotFather**
2. Ketik `/newbot` → ikuti instruksi
3. Salin **BOT_TOKEN** yang diberikan
4. Buat channel Telegram kamu
5. Tambahkan bot sebagai **Admin** di channel
6. Salin **username channel** (contoh: `@cryptoalertku`)

### 2. Deploy ke Render.com (Gratis)

1. **Push ke GitHub**
   ```
   git init
   git add .
   git commit -m "first commit"
   git remote add origin https://github.com/USERNAME/REPO.git
   git push -u origin main
   ```

2. **Buka [render.com](https://render.com)** → Sign up / Login

3. Klik **"New +"** → pilih **"Web Service"** (atau **Blueprint** jika pakai render.yaml)

4. Connect ke GitHub repo kamu

5. Isi pengaturan:
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: `Free`

6. Tambahkan **Environment Variables**:
   | Key | Value |
   |-----|-------|
   | `BOT_TOKEN` | token dari BotFather |
   | `CHANNEL_ID` | `@nama_channel_kamu` |

7. Klik **Deploy** ✅

---

## 📁 Struktur File
```
crypto-alert-bot/
├── bot.py           # Script utama
├── requirements.txt # Library Python
├── render.yaml      # Config deployment Render
└── README.md        # Panduan ini
```

---

## ⚙️ Kustomisasi

### Tambah/kurangi keyword
Edit bagian `KEYWORDS` di `bot.py`:
```python
KEYWORDS = [
    "delist", "delisting",
    "migration", "token swap",
    # tambahkan keyword lain di sini
]
```

### Ganti interval cek
```python
CHECK_EVERY = 5   # ubah angka ini (dalam menit)
```

### Tambah CEX baru
Tambahkan entry baru di list `SOURCES`:
```python
{
    "name": "NamaCEX",
    "type": "rss",          # atau "scrape"
    "url": "URL_RSS_ATAU_HALAMAN",
    "logo": "🔴",
},
```

---

## 📨 Contoh Pesan di Channel

```
🟡 [Binance]
Binance Will Delist XXXTOKEN on 2025-07-01
🔗 Lihat Announcement
```

---

## ⚠️ Catatan Penting
- **Render Free** akan sleep jika tidak ada aktivitas — gunakan tipe **Worker** (bukan Web Service) agar bot tetap jalan terus
- Database `seen.db` tersimpan lokal di server; jika Render restart, bot mungkin kirim ulang beberapa item lama (tidak berbahaya, hanya duplikat sesekali)
- Untuk menghindari duplikat permanen, pertimbangkan upgrade ke **PostgreSQL** gratis dari Render
