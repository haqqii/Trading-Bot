"""
Registry ticker yang tidak punya data harga.

Ticker yang benar-benar tidak punya candles di semua sumber data (mis.
ticker yang sudah dinonaktifkan di bursa) akan dicatat di sini, lalu
dilewati jalur background (scan sinyal & prefetch) sampai batas waktu
tertentu. Kalau tidak:
  - request ke yfinance sia-sia tiap 5 menit,
  - yfinance menulis "possibly delisted" sebagai ERROR, dan error itu naik
    jadi alert ke admin.

Catatan: ticker yang jalur utamanya gagal tapi sumber lain (v8 /
TradingView / Finnhub) masih bisa menjawab — misalnya BIKE — bukan ticker
mati. Yang begitu tetap dipakai, cuma log yfinance-nya yang dibuang di
stock_service.

Jalur yang dipicu user (/analisa, /favorit) tetap mencoba ticker yang
ditandai, supaya ticker yang tadinya mati bisa hidup lagi tanpa perlu
restart bot.

Statusnya disimpan ke file supaya tidak dicek ulang setiap restart.
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
STATE_FILE = os.path.join(DATA_DIR, 'ticker_unavailable.json')

# Berapa lama ticker yang gagal dilewati sebelum dicoba lagi.
DEFAULT_TTL_SECONDS = 24 * 60 * 60

# Pengaman: kalau banyak ticker sekaligus tidak ada datanya, itu hampir
# pasti masalah sumber data (Yahoo berubah format, dsb), bukan dozens
# ticker mati. Kalau dibiarkan, semua ticker ditandai dan bot diam
# selama 24 jam. Jadi setelah ambang ini, penandaan dihentikan dan
# kejadiannya dilaporkan sebagai error.
MAX_MARKS_PER_HOUR = 10

_lock = threading.Lock()
_entries: Dict[str, dict] = {}
_loaded = False
_warned_bulk_at = 0.0


def _normalize(ticker: Optional[str]) -> str:
    """Kunci registry: huruf besar tanpa suffix .JK."""
    if not ticker:
        return ''
    return str(ticker).upper().replace('.JK', '').strip()


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
    for ticker, info in data.items():
        if isinstance(info, dict) and 'until' in info:
            _entries[_normalize(ticker)] = info
    logger.info(f"Memuat {len(_entries)} ticker tanpa data dari {STATE_FILE}")


def _save() -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_entries, f, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.warning(f"Gagal tulis {STATE_FILE}: {e}")


def is_unavailable(ticker: str, now: Optional[float] = None) -> bool:
    """True kalau ticker sedang ditandai tidak punya data harga."""
    key = _normalize(ticker)
    if not key:
        return False
    with _lock:
        _load()
        info = _entries.get(key)
        if not info:
            return False
        if float(info.get('until', 0)) <= (now if now is not None else time.time()):
            # Kedaluwarsa: ticker dicoba lagi, dan dibuang dari daftar.
            del _entries[key]
            _save()
            return False
        return True


def _marks_in_last_hour(now: float) -> int:
    """Berapa ticker yang ditandai dalam satu jam terakhir."""
    return sum(1 for info in _entries.values()
               if float(info.get('at', 0)) > now - 3600)


def mark_unavailable(ticker: str, reason: str = '',
                     ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Tandai ticker sebagai tidak punya data harga.

    Dipanggil hanya kalau sumber data memang tidak punya data untuk ticker
    itu — bukan kalau jaringannya lagi putus. Kalau jaringan putus, semua
    ticker akan ditandai dan bot berhenti bekerja selama 24 jam.

    Returns True kalau ticker's ditandai.
    """
    global _warned_bulk_at
    key = _normalize(ticker)
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
            # Terlalu banyak sekaligus: kemungkinan besar sumber datanya
            # yang bermasalah, bukan tickernya. Bot harus tetap mencoba
            # semua ticker, jadi yang ditandai tidak ditambah.
            if now - _warned_bulk_at > 3600:
                _warned_bulk_at = now
                logger.error(
                    f"{MAX_MARKS_PER_HOUR}+ ticker sekaligus tidak punya data "
                    f"harga (terakhir: {key}). Kemungkinan masalah sumber data, "
                    f"bukan ticker mati. Penandaan dihentikan sementara supaya "
                    f"bot tetap mencoba semua ticker.")
            return False

        _entries[key] = {'until': until, 'reason': reason, 'at': now}
        _save()
    logger.info(
        f"{key}: tidak ada data harga, dilewati sampai "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}"
        + (f" ({reason})" if reason else ''))
    return True


def clear(ticker: str) -> None:
    """Hapus tanda — dipakai kalau ticker ternyata punya data lagi."""
    key = _normalize(ticker)
    if not key:
        return
    with _lock:
        _load()
        if key in _entries:
            del _entries[key]
            _save()
            logger.info(f"{key}: data harga tersedia lagi, tanda dihapus")


def list_unavailable() -> Dict[str, dict]:
    """Salinan daftar ticker yang sedang dilewati."""
    with _lock:
        _load()
        return dict(_entries)
