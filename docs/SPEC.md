# Bot Sinyal Saham Indonesia - Specification

## 1. Project Overview

**Nama Proyek:** IDX Stock Signal Bot (Bot Sinyal Saham Indonesia)
**Tipe:** Telegram Bot
**Fitur Utama:** Memberikan sinyal beli/jual untuk saham-saham Indonesia berdasarkan analisis teknikal sederhana
**Target User:** Trader saham Indonesia yang ingin mendapatkan sinyal trading otomatis

## 2. UI/UX Specification

### Layout Pesan
- Format pesan menggunakan Markdown untuk keterbacaan
- Setiap sinyal mencakup: nama saham, harga, arah sinyal (BUY/SELL), level entry, take profit, stop loss

### Komponen UI
1. **Main Menu** - Tombol keyboard inline untuk navigasi
2. **Sinyal Card** - Format menampilkan sinyal dengan warna/warning emoji
3. **Price List** - Daftar harga saham pilihan
4. **Settings** - Pengaturan notifikasi

### Visual Style
- Emoji untuk indikasi: 📈 (naik/BUY), 📉 (turun/SELL), ⚠️ (peringatan)
- Format code untuk harga dan angka
- Pembatas antar section

## 3. Functionality Specification

### Core Features

#### 3.1 Sinyal Trading Otomatis
- Analisis teknikal untuk saham IDX dan crypto (CoinGecko + Yahoo)
- Indikator: RSI (Wilder), MA crossover, MACD, Bollinger Bands, VWAP,
  ADX, Stochastic, Ichimoku, Support/Resistance (pivot + fibonacci +
  volume profile)
- Scoring multi-indikator berbobot; sinyal BUY/SELL/REVERSAL dengan
  kualitas WEAK/MODERATE/STRONG
- Mesin v2 (akurasi):
  - Indikator yang hilang di-skip dari scoring dan menurunkan kualitas
    sinyal (data fallback yang tidak lengkap tidak bisa jadi STRONG)
  - Konfirmasi trend besar (HTF): sinyal melawan arah besar kehilangan
    poin ke sisi lawan
  - TP/SL sadar S/R: TP diklem di depan resistance, SL dipindah ke
    balik support/resistance yang masuk akal (0.5-3x ATR)
  - Gate volatilitas ekstrem (ATR% > 6% saham / 10% crypto) menurunkan
    kualitas sinyal
- Regime gate (ADX): aturan yang dipakai menyesuaikan kondisi pasar
  - ADX > 25 (tren): MA/MACD penuh, BB dikurangi, RSI ekstrem yang
    berlawanan arah dengan tren (mis. RSI 25 saat harga turun) dikurangi
  - ADX < 20 (sideways): MA/MACD dikurangi (saling berbalik = noise),
    BB diperkuat (sentuh band = mean reversion)
  - Tanpa ADX (data fallback) skor dihitung seperti biasa
  - Sudah diukur (lihat 3.1.1): tidak membaik, jadi default-nya MATI.
    Nyalakan lagi: `FEATURES['regime_gate'] = True` di
    `services/signal_service.py`
- Aturan keluar (exit management) - SL bergerak, bukan diam:
  - TP1 kena -> SL digeser ke harga entry (breakeven)
  - TP2 kena -> SL trailing (1.5x ATR dari harga, minimal di level TP1)
  - SL yang sudah digeser tidak pernah turun lagi, jadi profit terkunci
    tidak hilang saat harga berbalik
  - SL yang sudah digeser langsung ditulis ke DB, jadi restart tidak
    membuat SL dihitung ulang (lalu turun lagi)
  - Exit dicatat dengan jenisnya: `sl` (cut loss), `beakeven` (SL di
    entry), `trailing` (SL bergerak). Exit beakeven/trailing hanya
    dihitung sebagai win kalau benar-benar menutup di sisi profit -
    harga bisa meloncat melewati level SL
  - Harga exit = harga pasar saat posisi ditutup (bukan level SL), dan
    P/L realisasinya ditampilkan di pesan
- Win rate diukur dengan backtest walk-forward (`scripts/backtest.py`),
  termasuk mode A/B klasik vs v2, A/B regime gate, dan A/B aturan keluar
  - `--compare features` (klasik vs v2), `--compare regime` (ADX on/off),
    `--compare exit` (SL diam vs SL bergerak)
  - Model exit: `tp1` (keluar di TP1), `signal` (tahan sampai TP3/SL),
    `ladder` (partial profit 1/3 di TP1, 1/3 di TP2 - paling dekat
    dengan pemakaian bot)
  - Expectancy juga dihitung dalam R (1R = jarak entry ke SL awal)
  - Asumsi harness (harus sama dengan bot live, kalau tidak hasil ukur
    tidak berlaku untuk produksi):
    - SL dicek sebelum TP di bar yang sama (pesimis)
    - Exit SL diisi di harga close kalau bar ditutup di sisi buruk dari
      stop, di level stop kalau bar ditutup di sisi baik - bot live
      menutup di harga yang terlihat saat polling
    - Win/loss ditentukan dari hasil P/L, bukan dari jenis exit, supaya
      dua lengan A/B bisa dibandingkan
    - Trailing di-backtest diikat ke ekstrem bar, bot live mengikatnya
      ke harga polling terakhir
  - Contoh ukur aturan keluar (saham IDX):
    `python scripts/backtest.py --market stock --tickers BBCA.JK,BBRI.JK,TLKM.JK --interval 1h --period 6mo --compare exit`
  - Contoh ukur regime gate (crypto):
    `python scripts/backtest.py --market crypto --tickers BTC-USD,ETH-USD --interval 1h --period 90d --compare regime`
  - Cara menjalankan tiap fitur bisa dimatikan lewat flag `FEATURES` di
    `services/signal_service.py` (`regime_gate`, `exit_mgmt`), jadi A/B
    bisa dilakukan tanpa mengubah kode lain

#### 3.1.1 Hasil ukur (30 Oktober 2026)

Semua angka dari `scripts/backtest.py`, fee 0,30%/putaran untuk saham IDX
dan 0,16% untuk crypto. Expectancy = rata-rata P/L per trade.

Aturan keluar (model exit 'ladder'):

| Pasar | Metrik | SL diam | SL bergerak |
|---|---|---|---|
| Saham IDX (BBCA/BBRI/TLKM, 1h, 6 bulan) | expectancy | -0,50 %/trade | -0,47 %/trade |
| | total PnL | -38,7% | -37,0% |
| | win rate (trade yang sudah diputuskan) | 20% | 47% |
| | masih menggantung (timeout) | 28% | 6% |
| Crypto (BTC/ETH/SOL/ADA/DOGE, 1h, 90 hari) | expectancy | -0,31 %/trade | -0,26 %/trade |
| | total PnL | -74,1% | -63,4% |
| | win rate (trade yang sudah diputuskan) | 19% | 46% |
| | masih menggantung (timeout) | 33% | 15% |

Yang berubah jelas: posisi yang sudah kena TP1 tidak lagi menggantung
sampai akhir horizon, dan lebih banyak trade selesai di sisi profit.
Peningkatan expectancy-nya kecil - jadi fitur ini memperbaiki risiko
(posisi yang sudah profit tidak balik jadi loss penuh), bukan Expectancy
Bot secara dramatis.

Regime gate (model exit 'tp1'):

| Pasar | Metrik | Gate mati | Gate hidup |
|---|---|---|---|
| Saham IDX (BBCA/BBRI/TLKM, 1h, 6 bulan) | expectancy | -0,07 %/trade | -0,11 %/trade |
| | total PnL | -5,4% | -8,6% |
| | win rate | 53% | 51% |
| Crypto (BTC/ETH/SOL/ADA/DOGE, 1h, 90 hari) | expectancy | -0,12 %/trade | -0,12 %/trade |
| | total PnL | -29,3% | -30,1% |
| | win rate | 52% | 51% |

Regime gate tidak membawa perbaikan di jendela yang diukur - di crypto
netral, di saham IDX justru sedikit lebih buruk. Karena itu tidak
dinyalakan default untuk trading sungguhan; kodenya tetap ada dan bisa
diukur ulang kapan saja.


#### 3.2 Daftar Harga Saham
- Menampilkan harga real-time dari saham-saham populer
- Perubahan harga hari ini (%)
- Status: naik/hurun

#### 3.3 Watchlist Pribadi
- User bisa menambah/menghapus saham ke watchlist
- Notifikasi ketika ada sinyal untuk saham di watchlist

#### 3.4 Portfolio Tracker (Sederhana)
- Catat harga beli dan jumlah lot
- Hitung unrealized P/L

### Commands
- `/start` - Memulai bot dan menampilkan menu
- `/signal` - Dapatkan sinyal terbaru
- `/price` - Daftar harga saham populer
- `/watchlist` - Lihat watchlist pribadi
- `/add [kode]` - Tambah saham ke watchlist
- `/remove [kode]` - Hapus dari watchlist
- `/portfolio` - Lihat portfolio tracker
- `/help` - Bantuan

### Data Source
- Yahoo Finance API untuk data harga saham IDX
- Format kode: ^JKL (untuk indeks) atau JSX (contoh: BBCA.JK)

## 4. Technical Stack

- **Language:** Python 3.10+
- **Library:** python-telegram-bot
- **Data:** yfinance (Yahoo Finance)
- **Database:** SQLite (untuk user data)
- **Hosting:** Cloud run / VPS

## 5. Acceptance Criteria

1. ✅ Bot dapat menerima command /start dan menampilkan menu
2. ✅ Command /signal menampilkan minimal 5 sinyal saham
3. ✅ Command /price menampilkan harga minimal 10 saham populer IDX
4. ✅ User dapat menambah/menghapus watchlist
5. ✅ Format pesan mudah dibaca dengan emoji
6. ✅ Bot menangani error dengan baik (saham tidak ditemukan, API error)
