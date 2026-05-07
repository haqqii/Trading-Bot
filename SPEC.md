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
- Analisis teknikal untuk saham IDX (contoh: BBCA, BBRI, BMRI, TLKM, UNVR, dll)
- Indikator: RSI, Moving Average, Support/Resistance
- Sinyal: BUY ketika oversold + MA crossover, SELL ketika overbought

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
