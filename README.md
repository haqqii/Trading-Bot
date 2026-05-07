# 🤖 IDX Stock & Crypto Signal Bot

Bot Telegram untuk mendapatkan sinyal trading saham Indonesia dan crypto berbasis analisis teknikal.

## 📁 Struktur Project

```
Bot Saham 2/
├── main.py                 # Entry point
├── config/                 # Konfigurasi
│   ├── settings.py         # Settings & TIMEFRAMES
│   └── __init__.py
├── handlers/               # Command handlers
│   ├── command_handlers.py # Semua command handler
│   ├── scheduler.py        # Background jobs
│   └── __init__.py
├── services/               # Business logic
│   ├── stock_service.py    # Data saham
│   ├── crypto_service.py    # Data crypto
│   ├── signal_service.py    # Signal generation
│   ├── chart_service.py     # Chart generation
│   └── __init__.py
├── utils/                  # Utilities
│   ├── formatters.py       # Message formatting
│   ├── cache.py            # In-memory cache
│   ├── indicators.py       # Technical indicators
│   ├── rate_limiter.py     # API rate limiting
│   └── __init__.py
└── README.md
```

## 📋 Fitur

### Saham (IDX)
- **📊 Harga Saham** - Lihat harga real-time saham IDX populer
- **🎯 Sinyal Trading** - Sinyal BUY/SELL berbasis multi-indikator (RSI, MACD, Bollinger Bands, Volume)
- **⭐ Quality Rating** - Signal strength: STRONG (⭐), MODERATE (✨), WEAK (💫)
- **🌙 BSJP (Beli Sore Jual Pagi)** - Rekomendasi intraday: buy sore, sell pagi
- **☀️ Morning Watchlist** - Rekomendasi saham sebelum market open (via notifikasi)
- **📈 Price Alert** - Notifikasi real-time jika harga naik/turun signifikan
- **🎯 TP/SL Tracking** - Notifikasi otomatis saat TP atau SL tercapai
- **⭐ Favorit** - Pantau saham/crypto favorit Anda (manual add)
- **💼 Portfolio Tracker** - Catat dan lacak posisi Anda

### Crypto (24/7)
- **₿ Sinyal Crypto** - Sinyal untuk 250+ crypto pairs
- **💰 Dual Currency** - Tampilan harga dalam USD dan IDR
- **📈 Price Alert** - Notifikasi real-time pergerakan harga crypto
- **🎯 TP/SL Tracking** - Auto-notifikasi saat target tercapai (TP1, TP2, TP3, SL)

### Analisis Teknikal (Multi-Indicator Scoring)
- **RSI (Relative Strength Index)** - Overbought/Oversold detection
- **MACD (Moving Average Convergence Divergence)** - Trend confirmation
- **Bollinger Bands** - Volatility bands & price channel
- **Volume Analysis** - Volume spike detection
- **MA Crossover** - Golden cross / Death cross
- **Stochastic Oscillator** - Momentum dengan %K dan %D crossover
- **VWAP (Volume Weighted Average Price)** - Average price berdasarkan volume
- **ADX (Average Directional Index)** - Trend strength & direction
- **Ichimoku Cloud** - Cloud analysis (Tenkan, Kijun, Senkou)
- **Fibonacci Retracement** - Support/resistance levels (23.6%, 38.2%, 50%, 61.8%)
- **Weighted Scoring** - Kombinasi semua indikator dengan bobot
- **Interactive Charts** - Chart harga dengan RSI, MACD, Volume (via `/chart`)

### Notifikasi
- **Notifikasi Saham** - TP/SL hit (09:00-15:00)
- **Notifikasi Favorit** - Alert jika harga berubah ≥1% (saham & crypto)
- **Notifikasi Morning** - Sinyal saham sebelum market open (07:15)
- **Notifikasi Crypto** - Aktif 24/7
- **Notifikasi BSJP** - Otomatis jam 14:25 (sebelum market close)

## 🚀 Cara Install

1. **Clone atau download repositori ini**

2. **Install dependencies:**
```bash
pip install -r requirements.txt
```

3. **Dapatkan Token Bot:**
   - Buka Telegram
   - Cari @BotFather
   - Ketik `/newbot`
   - Ikuti instruksi dan simpan token bot Anda

4. **Buat file `.env`:**
```bash
TELEGRAM_BOT_TOKEN=token_anda_disini
```

## ▶️ Cara Menjalankan

```bash
python main.py
```

atau klik `start.bat` (Windows)

## 📱 Cara Menggunakan

### Button Menu

| Button | Fungsi |
|--------|--------|
| `📊 Harga` | Daftar harga saham |
| `🎯 Sinyal` | Sinyal saham BUY terbaik hari ini |
| `⭐ Favorit` | Saham/crypto favorit + toggle notifikasi |
| `🌙 BSJP` | Beli Sore Jual Pagi |
| `⏱️ TF` | Pilih timeframe |
| `💼 Portfolio` | Portfolio user |
| `🔔 Notifikasi` | Setting notifikasi (menu toggle) |
| `₿ Crypto` | Sinyal crypto |

### Commands

| Command | Deskripsi |
|---------|-----------|
| `/start` | Mulai bot dan lihat menu utama |
| `/harga` | Lihat daftar harga saham |
| `/sinyal` atau `/pagi` atau `/morning` | Morning Watchlist |
| `/favorit` atau `/fav` | Lihat favorit |
| `/bsjp` | Rekomendasi BSJP (Beli Sore Jual Pagi) |
| `/crypto` | Sinyal crypto |
| `/tf` atau `/timeframe` | Pilih timeframe (1m, 5m, 15m, 60m) |
| `/add [KODE]` | Tambah saham ke favorit |
| `/remove [KODE]` | Hapus dari favorit |
| `/portfolio` atau `/pf` | Lihat portfolio |
| `/buy [KODE] [HARGA] [LOT]` | Catat posisi buy |
| `/sell [KODE] [LOT]` | Catat posisi sell |
| `/notifikasi` atau `/notif` | Menu pengaturan notifikasi |
| `/help` atau `/bantuan` | Daftar command |
| `/chart [KODE] [TF] [PERIOD]` atau `/c` | Generate chart |

### Menu Notifikasi (🔔)

Klik tombol **🔔 Notifikasi** untuk toggle:

| Setting | Deskripsi |
|---------|-----------|
| Sinyal Saham | Notifikasi TP/SL saham |
| Sinyal Crypto | Notifikasi sinyal crypto 24/7 |
| BSJP | Notifikasi Beli Sore Jual Pagi |
| Alert Favorit | Alert harga di favorit |
| Sinyal Pagi | Notifikasi morning watchlist |

### Price Alerts

| Command | Deskripsi |
|---------|-----------|
| `/alertbuy [KODE] [HARGA]` | Alert jika harga turun ke target |
| `/alertsell [KODE] [HARGA]` | Alert jika harga naik ke target |
| `/alerts` | Lihat semua alerts |
| `/alertdel [KODE]` | Hapus alert |

### Chart Commands

| Command | Deskripsi |
|---------|-----------|
| `/chart [KODE] [TF] [PERIOD]` | Generate price chart |
| `/chart BTC-USD` | Chart BTC default (1h, 5d) |
| `/chart BBCA 15m 5d` | Chart BBCA 15 menit, 5 hari |
| `/c` | Alias untuk `/chart` |

### Contoh Penggunaan

```
/start              → Mulai bot
/favorit            → Lihat favorit
/add BBCA BREN     → Tambah saham ke favorit
/tf 15              → Ganti timeframe ke 15 menit
/crypto             → Lihat sinyal crypto
/bsjp               → Rekomendasi BSJP
/notifikasi         → Menu pengaturan notifikasi
/alertbuy BBCA 9000 → Alert jika BBCA turun ke 9000
/chart BBCA 1h 5d   → Generate chart
```

## 📊 Data Source

Bot menggunakan multiple data source sebagai fallback:

1. **Yahoo Finance** - Primary untuk saham dan crypto
2. **TradingView** - Fallback untuk data saham
3. **CoinGecko** - Fallback untuk data crypto dan kurs USD/IDR

### API Protection
- **Rate Limiting**: Batasan request per menit
- **Circuit Breaker**: Auto-stop saat API terlalu banyak error
- **Exponential Backoff**: Retry dengan delay yang increasing
- **In-Memory Caching**: Cache data untuk kurangi API calls

## ⏰ Jadwal Notifikasi

| Notifikasi | Jadwal | Jam Aktif |
|------------|--------|-----------|
| Morning | Sekali sehari | 07:15 (weekdays) |
| Alert Favorit Saham | Setiap 2 menit | 08:00-16:00 (weekdays) |
| Alert Favorit Crypto | Setiap 2 menit | 24/7 |
| Crypto Signals | Setiap 5 menit | 24/7 |
| Crypto TP/SL | Setiap 2 menit | 24/7 |
| BSJP | Sekali sehari | 14:25 (weekdays) |
| Price Alerts | Setiap 1 menit | 08:00-16:00 (weekdays) |

## 📈 Contoh Output

### Sinyal Crypto Notification
```
━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 *₿ CRYPTO SIGNAL - REKOMENDASI BELI*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *Bitcoin (BTC)*
💸 Gain: -1.0% → +6.0% ⚡️

🤖 CRYPTO SIGNAL DETECTED
📅 Detected: 25 Apr 2026 pukul 10:30 WIB 🔍

📈 Chart Pattern: UPTREND
📊 Reliability: 72%
📊 Leverage: 5x
💰 Entry: $82,000 | Rp 1,312,000,000
📐 ATR14: $400 (0.5% vol)
🛡️ SL: $80,000 (+1.0%)

━━━ ₿ MARKET SNAPSHOT ━━━
₿ BTC Dominance: 58.3%
😱 Fear & Greed: 45 - 🔴 Fear

━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 25 Apr 2026 pukul 10:30 WIB
₿ IDX Crypto Bot
```

### TP/SL Notifications
```
🏆 *₿ TP1 TERKUNCI! +2%*
💰 *₿ TP2 TERKUNCI! +4%*
💰 *₿ TP3 TERCAPAI!*
🔴 *₿ CUT LOSS - SL TERKENA!*
```

## 📊 Signal Quality

| Quality | Badge | Score | Description |
|---------|-------|-------|-------------|
| STRONG | ⭐ | ≥60 | 4+ indikator konfirmasi |
| MODERATE | ✨ | 45-59 | 3 indikator konfirmasi |
| WEAK | 💫 | 35-44 | 2 indikator konfirmasi |
| EARLY | 🟡 | 25-34 | 1 indikator konfirmasi (early signal) |

**Minimum BUY/SELL threshold: 35**

## 📐 Indicator Scoring (Weighted)

### Saham (IDX)

| Indikator | Bobot | BUY Signal | SELL Signal |
|-----------|-------|------------|-------------|
| RSI | 25% | <30 oversold | >70 overbought |
| MA Crossover | 20% | Golden cross | Death cross |
| MACD | 25% | MACD > Signal + hist (+) | MACD < Signal + hist (-) |
| Bollinger Bands | 15% | Near lower band | Near upper band |
| Volume | 15% | Vol spike + price up | Vol spike + price down |

### Crypto

| Indikator | Bobot | BUY Signal | SELL Signal |
|-----------|-------|------------|-------------|
| RSI | 15% | <35 oversold | >70 overbought |
| MA Crossover | 10% | Golden cross | Death cross |
| MACD | 15% | MACD > Signal + hist (+) | MACD < Signal + hist (-) |
| Bollinger Bands | 10% | Near lower band | Near upper band |
| Stochastic | 10% | %K < 20 oversold | %K > 80 overbought |
| VWAP | 10% | Price > VWAP | Price < VWAP |
| ADX | 10% | ADX > 25 + +DI > -DI | ADX > 25 + -DI > +DI |
| Ichimoku | 10% | Tenkan > Kijun + cloud above | Tenkan < Kijun + cloud below |
| Volume + Momentum | 10% | Vol spike + momentum | Vol spike + downtrend |

## ⚠️ Disclaimer

Bot ini hanya alat bantu analisis. Keputusan trading adalah tanggung jawab Anda sendiri. Selalu lakukan riset sebelum trading.

## 📝 Lisensi

MIT License
