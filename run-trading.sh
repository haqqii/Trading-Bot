#!/usr/bin/env bash
# Bot trading (Telegram) jadi proses utama container ini.
#
# Keluarannya (stdout + stderr) ikut ke stdout container, jadi kelihatan di
# "docker logs v-ochobot". Bot Aimapps (Zulip) jalan di container lain
# ("ochobot").
#
# Prefix logger untuk field "Object" diatur dari env LOG_TAG (dibaca
# di utils/log_tag.py). Kalau env-nya kosong, defaultnya "trading".
set -u

TRADING_DIR=/trading
TRADING_LOGS="$TRADING_DIR/logs"
mkdir -p "$TRADING_LOGS" "$TRADING_DIR/data"

# Kunci "bot sudah jalan" disimpan di volume data. Kalau container mati
# mendadak, PID lama masih tertulis di sana, dan PID yang sama bisa dipakai
# proses lain di container baru -> bot trading menolak start. Hapus dulu.
rm -f "$TRADING_DIR/data/bot.lock"

# Seed daftar saham IDX kalau volume-nya belum punya. Sumbernya /trading-seed
# (disalin ke image saat build) - BUKAN dari /trading/data, karena folder
# itu ditimpa named volume, jadi file seed di repo tidak terlihat di sini.
if [ ! -f "$TRADING_DIR/data/idx_stocks.py" ] && [ -f /trading-seed/idx_stocks.py ]; then
    cp /trading-seed/idx_stocks.py "$TRADING_DIR/data/idx_stocks.py"
fi

exec python "$TRADING_DIR/main.py"
