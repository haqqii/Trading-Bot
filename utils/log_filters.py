"""
Filter log untuk provider data (yfinance).

yfinance menulis ticker yang tidak punya data di Yahoo sebagai ERROR:

    $HYPE-USD: No data found, symbol may be delisted
    $BIKE.JK: possibly delisted; no price data found  (period=3d)
    HTTP Error 404 ... Quote not found for symbol

Itu bukan bug bot: ticker memang tidak ada datanya di Yahoo, dan bot sudah
tahu cara menanganinya (tandai di ticker_health lalu lewati, atau fallback ke
CoinGecko/TradingView). Kalau record-nya lolos ke root logger, error ini naik
jadi alert ke admin - padahal tidak ada yang bisa diperbaiki dari sisi bot.

Log lain dari yfinance (rate limit, server error, HTML error) tetap diteruskan
supaya masalah provider yang benar-benar mengganggu tetap terlihat.

Catatan: nama logger yfinance di dalam container adalah 'trading.yfinance'
(prefix bot), bukan 'yfinance' - makanya filter dipasang lewat
logging.getLogger('yfinance') (nama aslinya), yang tetap berlaku.
"""
import collections
import logging
import re
import time

# Pesan-pesan yang artinya "ticker ini tidak punya data", bukan "provider rusak".
NO_DATA_MARKERS = (
    'no price data found',
    'no data found, symbol may be delisted',
    'possibly delisted',
    'quote not found for symbol',
    'no earnings dates found',
)

# Pesan yang berarti Yahoo SENDIRI yang bilang ticker itu tidak ada datanya.
# Cuma pesan ini yang boleh dipakai untuk menandai ticker "tidak ada di Yahoo".
# 'no price data found' yang generik juga muncul kalau request-nya gagal
# (timeout, koneksi putus) - itu bukan bukti ticker hilang.
EXPLICIT_ABSENCE_MARKERS = (
    'no data found, symbol may be delisted',
    'symbol may be delisted',
    'symbol is delisted',
    'invalid symbol',
)

_TICKER_RE = re.compile(r'\$([A-Za-z0-9._-]+)')


class DropNoPriceData(logging.Filter):
    """Buang log yfinance yang cuma mengatakan "ticker ini tidak ada datanya".

    Sekalian catat pesannya, supaya pemanggil bisa tahu ticker mana yang
    memang tidak ada di Yahoo - bedanya dengan request yang gagal sesaat
    (timeout, rate limit), yang juga balik frame kosong.
    """

    def __init__(self):
        super().__init__()
        self.hits = collections.deque(maxlen=200)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        low = message.lower()
        if any(marker in low for marker in NO_DATA_MARKERS):
            match = _TICKER_RE.search(message)
            ticker = match.group(1).upper() if match else None
            self.hits.append((ticker, time.time(), low))
            return False
        return True


def _filter_instance() -> DropNoPriceData | None:
    """Filter yang terpasang di logger yfinance (kalau ada)."""
    logger = logging.getLogger('yfinance')
    for existing in logger.filters:
        if isinstance(existing, DropNoPriceData):
            return existing
    return None


def install_provider_filters() -> None:
    """Pasang filter di logger yfinance. Aman dipanggil berulang.

    Dipasang sekali di startup (tidak pernah dilepas), jadi tidak ada balapan
    antar-thread seperti kalau filter dipasang-dilepas di setiap request.
    """
    logger = logging.getLogger('yfinance')
    for existing in logger.filters:
        if isinstance(existing, DropNoPriceData):
            return
    logger.addFilter(DropNoPriceData())


def no_data_reported(ticker: str, since_ts: float) -> bool:
    """True kalau Yahoo sendiri yang bilang ticker ini tidak ada datanya.

    since_ts: batas waktu (time.time()) - pesan yang lebih lama diabaikan,
    supaya tidak salah hitung dari request sebelumnya.
    """
    install_provider_filters()
    instance = _filter_instance()
    if instance is None:
        return False
    upper = ticker.upper()
    for hit_ticker, hit_ts, low in instance.hits:
        if hit_ts < since_ts or hit_ticker != upper:
            continue
        if any(marker in low for marker in EXPLICIT_ABSENCE_MARKERS):
            return True
    return False
