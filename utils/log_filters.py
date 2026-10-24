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
import logging

# Pesan-pesan yang artinya "ticker ini tidak punya data", bukan "provider rusak".
NO_DATA_MARKERS = (
    'no price data found',
    'no data found, symbol may be delisted',
    'possibly delisted',
    'quote not found for symbol',
    'no earnings dates found',
)


class DropNoPriceData(logging.Filter):
    """Buang log yfinance yang cuma mengatakan "ticker ini tidak ada datanya"."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage().lower()
        except Exception:
            return True
        return not any(marker in message for marker in NO_DATA_MARKERS)


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
