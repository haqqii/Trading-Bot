"""
Registry ticker crypto yang tidak ada di Yahoo Finance.

Daftar pair crypto diambil dari CoinGecko, tapi harga diambil dari Yahoo
Finance dulu. Sebagian ticker crypto tidak ada di Yahoo — Yahoo balas 404
dan tidak punya candle sama sekali (contoh yang terlihat: UNI-USD,
MATIC-USD, HYPE-USD, RAIN-USD).

Tanpa registry ini, tiap ticker seperti itu dicoba berulang: 3 request per
scan (scan jalan tiap 15 menit), boros rate limit Yahoo, dan menghasilkan
log ERROR dari yfinance yang naik jadi alert ke admin. Jadi ticker yang
sudah diketahui tidak ada di Yahoo dicatat di sini, dan jalur Yahoo
dilewati sampai batas waktu tertentu — datanya tetap diambil dari
CoinGecko seperti biasa, jadi sinyalnya tetap terbit.

Kalau ticker-nya sewaktu-waktu muncul di Yahoo (Yahoo kadang 404 sesaat),
TTL-nya habis dan Yahoo dicoba lagi.

Kunci registry memakai interval juga: ticker bisa punya candle harian tapi
tidak punya intraday, jadi "tidak ada di Yahoo untuk 15m" tidak sama dengan
"tidak ada di Yahoo untuk 1d".
"""
import json
import logging
import os
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
STATE_FILE = os.path.join(DATA_DIR, 'crypto_yahoo_missing.json')

# Berapa lama Yahoo dilewati untuk ticker yang diketahui tidak ada di sana.
# Cukup lama untuk hemat request (scan tiap 15 menit), tapi tidak sampai
# seharian - Yahoo bisa saja 404 sesaat lalu normal lagi.
DEFAULT_TTL_SECONDS = 6 * 60 * 60

# Pengaman: kalau banyak ticker sekaligus hilang dari Yahoo, kemungkinan
# besar Yahoo yang bermasalah (format respons berubah), bukan puluhan ticker
# yang hilang. Kalau dibiarkan, semua ticker ditandai dan jalur Yahoo mati
# selama berjam-jam. Jadi setelah ambang ini penandaan dihentikan.
# Tick yang memang hilang saja yang dicatat (hanya kalau Yahoo sendiri yang
# bilang "symbol may be delisted"), dan dalam satu jam bisa ada puluhan
# ticker crypto yang memang tidak ada di Yahoo - jadi ambangnya longgar.
MAX_MARKS_PER_HOUR = 60

_lock = threading.Lock()
_entries: Dict[str, dict] = {}
_loaded = False
_warned_bulk_at = 0.0


def _normalize(ticker: Optional[str], interval: str = '15m') -> str:
    """Kunci registry: TICKER@INTERVAL, huruf besar."""
    if not ticker:
        return ''
    return f"{str(ticker).upper().strip()}@{interval}"


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        logger.warning(f"Gagal baca {STATE_FILE}: {e}")
        return

    if not isinstance(data, dict):
        return
    for key, info in data.items():
        if isinstance(info, dict) and 'until' in info:
            _entries[key] = info
    logger.info(f"Memuat {len(_entries)} ticker crypto tanpa data Yahoo dari {STATE_FILE}")


def _save() -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_entries, f, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.warning(f"Gagal tulis {STATE_FILE}: {e}")


def is_missing(ticker: str, interval: str = '15m',
               now: Optional[float] = None) -> bool:
    """True kalau Yahoo tidak punya data untuk ticker + interval ini.

    Kalau sudah kedaluwarsa, tandanya dibuang dan ticker dicoba lagi.
    """
    key = _normalize(ticker, interval)
    if not key:
        return False
    with _lock:
        _load()
        info = _entries.get(key)
        if not info:
            return False
        if float(info.get('until', 0)) <= (now if now is not None else time.time()):
            del _entries[key]
            _save()
            return False
        return True


def _marks_in_last_hour(now: float) -> int:
    return sum(1 for info in _entries.values()
               if float(info.get('at', 0)) > now - 3600)


def mark_missing(ticker: str, interval: str = '15m', reason: str = '',
                 ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Tandai ticker crypto sebagai tidak punya data di Yahoo.

    Dipanggil hanya kalau Yahoo memang tidak punya candle untuk ticker itu
    (respons 404 / data kosong), bukan kalau jaringannya yang putus. Kalau
    jaringan putus, semua ticker akan ditandai dan jalur Yahoo mati
    berbulan-bulan.

    Returns True kalau ticker ditandai.
    """
    global _warned_bulk_at
    key = _normalize(ticker, interval)
    if not key:
        return False
    until = time.time() + max(1, int(ttl))
    with _lock:
        _load()
        existing = _entries.get(key)
        if existing and float(existing.get('until', 0)) > until:
            return True  # Sudah lebih lama, jangan pendekkan.

        now = time.time()
        if _marks_in_last_hour(now) >= MAX_MARKS_PER_HOUR:
            # Terlalu banyak sekaligus: kemungkinan besar Yahoo yang
            # bermasalah, bukan ticker-nya. Yang ditandai tidak ditambah,
            # supaya jalur Yahoo tetap dicoba.
            if now - _warned_bulk_at > 3600:
                _warned_bulk_at = now
                logger.error(
                    f"{MAX_MARKS_PER_HOUR}+ ticker crypto sekaligus hilang dari "
                    f"Yahoo (terakhir: {key}). Kemungkinan masalah Yahoo, "
                    f"bukan ticker-nya. Penandaan dihentikan sementara supaya "
                    f"jalur Yahoo tetap dicoba.")
            return False

        _entries[key] = {'until': until, 'reason': reason, 'at': now}
        _save()
    logger.info(
        f"{key}: tidak ada data di Yahoo, jalur Yahoo dilewati sampai "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}"
        + (f" ({reason})" if reason else ''))
    return True


def clear(ticker: str, interval: str = '15m') -> None:
    """Hapus tanda — dipakai kalau ticker ternyata ada di Yahoo lagi."""
    key = _normalize(ticker, interval)
    if not key:
        return
    with _lock:
        _load()
        if key in _entries:
            del _entries[key]
            _save()
            logger.info(f"{key}: data Yahoo tersedia lagi, tanda dihapus")


def list_missing() -> Dict[str, dict]:
    """Salinan daftar ticker yang jalurnya dilewati."""
    with _lock:
        _load()
        return dict(_entries)
