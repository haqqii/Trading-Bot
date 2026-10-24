"""
Unit tests for utils/telegram_alert.py
Tests the noise filter that decides which errors are pushed to the admin.
"""
import sys
import os
import logging

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.telegram_alert import _is_noise


def make_record(msg, exc=None, name='jobs_stocks', level=logging.ERROR):
    """Buat LogRecord dengan exception info, seperti yang dihasilkan logging."""
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except Exception:
            exc_info = sys.exc_info()
    return logging.LogRecord(name, level, 'file.py', 1, msg, None, exc_info)


class TestIsNoise:
    """Tests for _is_noise filter."""

    def test_network_error_is_noise(self):
        """Masalah transport tidak boleh dikirimi alert."""
        rec = make_record(
            "[STOCK] Failed to send message for SAPX: Unknown error in HTTP "
            "implementation: RuntimeError('Event loop is closed')",
            NetworkError("Unknown error in HTTP implementation: "
                         "RuntimeError('Event loop is closed')"),
        )
        assert _is_noise(rec) is True

    def test_timed_out_is_noise(self):
        """Timeout juga noise."""
        rec = make_record(
            "[STOCK] Failed to send message for BBCA: Timed out",
            TimedOut(),
        )
        assert _is_noise(rec) is True

    def test_failed_to_send_message_is_noise(self):
        """Kegagalan kirim pesan tidak bocor ke admin."""
        rec = make_record("[CRYPTO] Failed to send message for BTCUSDT: boom")
        assert _is_noise(rec) is True

    def test_benign_bad_request_is_noise(self):
        """'Message is not modified' itu no-op, tidak perlu dilaporkan."""
        rec = make_record(
            "Unhandled exception in handler:",
            BadRequest("Message is not modified"),
        )
        assert _is_noise(rec) is True

    def test_parse_entities_bad_request_is_reported(self):
        """Bug parse Markdown itu bug sungguhan, harus tetap dilaporkan.

        Di PTB, BadRequest anak dari NetworkError. Kalau NetworkError dicek
        lebih dulu, semua BadRequest ikut tersaring dan bug ini hilang
        tanpa jejak.
        """
        rec = make_record(
            "Unhandled exception in handler:",
            BadRequest("Can't parse entities: can't find end of the entity"),
        )
        assert _is_noise(rec) is False

    def test_plain_error_is_reported(self):
        """Error biasa harus tetap sampai ke admin."""
        rec = make_record("Telegram bot token not found!")
        assert _is_noise(rec) is False

    def test_handler_error_without_text_is_reported(self):
        """Error tanpa pesan tetap dilaporkan."""
        rec = make_record("Unhandled exception in handler:")
        assert _is_noise(rec) is False


class TestTransportNoiseAsPlainText:
    """Error transport yang di-log sebagai teks (tanpa exc_info).

    Filter berdasarkan tipe hanya bisa menangkap exception PTB. Service
    me-log error Yahoo/CoinGecko/TradingView dengan f-string tanpa exc_info,
    jadi record.exc_info kosong — error seperti ini pernah bocor ke alert admin.
    """

    def test_yahoo_read_timeout_is_noise(self):
        """Read timeout dari Yahoo v8 tidak boleh dikirimi alert."""
        rec = make_record(
            "Yahoo v8 error for RDTX.JK: HTTPSConnectionPool(host='query1."
            "finance.yahoo.com', port=443): Read timed out. (read timeout=15)"
        )
        assert _is_noise(rec) is True

    def test_yahoo_connection_error_is_noise(self):
        """Connection error dari Yahoo tidak boleh dikirimi alert."""
        rec = make_record(
            "Yahoo v8 error for BBCA.JK: HTTPSConnectionPool(host='query1."
            "finance.yahoo.com', port=443): Max retries exceeded with url: "
            "/v8/finance/chart/BBCA.JK (Caused by NewConnectionError(...))"
        )
        assert _is_noise(rec) is True

    def test_coingecko_failure_is_noise(self):
        """Kegagalan CoinGecko karena timeout tidak boleh dikirimi alert."""
        rec = make_record(
            "CoinGecko failed for BTC-USD after 3 attempts: "
            "HTTPSConnectionPool(host='api.coingecko.com', port=443): Read timed out."
        )
        assert _is_noise(rec) is True

    def test_tradingview_connection_error_is_noise(self):
        """Error koneksi TradingView tidak boleh dikirimi alert."""
        rec = make_record(
            "TradingView error for BBCA.JK: Connection reset by peer"
        )
        assert _is_noise(rec) is True

    def test_finnhub_max_retries_is_noise(self):
        """Max retries exceeded dari Finnhub tidak boleh dikirimi alert."""
        rec = make_record(
            "Finnhub error for BBCA.JK: Max retries exceeded"
        )
        assert _is_noise(rec) is True

    def test_analisa_fetch_timeout_is_noise(self):
        """Timeout saat ambil data di /analisa tidak boleh dikirimi alert."""
        rec = make_record("[ANALISA] Crypto fetch timeout for BTC-USD")
        assert _is_noise(rec) is True

    def test_real_bug_with_transport_word_is_reported(self):
        """Bug sungguhan tetap dilaporkan, walau pesannya mirip transport.

        'connection' di sini bagian dari pesan bug, bukan error jaringan —
        jadi tidak boleh ikut tersaring.
        """
        rec = make_record(
            "[CRYPTO_SIGNAL] analyze failure for FIGR_HELOC-USD: "
            "'>=' not supported between instances of 'NoneType' and 'int'",
            TypeError("'>=' not supported between instances of 'NoneType' and 'int'"),
        )
        assert _is_noise(rec) is False

    def test_data_shape_bug_is_reported(self):
        """Error bentuk data (bukan transport) tetap dilaporkan."""
        rec = make_record(
            "Chart error for BBCA.JK: 'Close' is a required column",
            KeyError("'Close' is a required column"),
        )
        assert _is_noise(rec) is False


class TestThirdPartyLoggers:
    """Error dari library pihak ketiga bukan bug bot - jangan dikirimi alert.

    yfinance menulis '$HYPE-USD: No data found, symbol may be delisted' sebagai
    ERROR. Itu cuma informasi bahwa ticker tidak ada di Yahoo, dan bot sudah
    tahu cara menanganinya. Kalau bocor, admin dapat alert yang tidak ada
    yang bisa diperbaiki.
    """

    def test_yfinance_no_data_is_noise(self):
        """Pesan yfinance versi sekarang harus tersaring."""
        rec = make_record(
            '$HYPE-USD: No data found, symbol may be delisted',
            name='yfinance',
        )
        assert _is_noise(rec) is True

    def test_yfinance_prefixed_is_noise(self):
        """Di container nama loggernya ber-prefix: 'trading.yfinance'."""
        rec = make_record(
            '$HYPE-USD: No data found, symbol may be delisted',
            name='trading.yfinance',
        )
        assert _is_noise(rec) is True

    def test_any_third_party_error_is_noise(self):
        """Error library pihak ketiga lain juga tidak dikirimi alert."""
        rec = make_record('something exploded', name='trading.urllib3.connectionpool')
        assert _is_noise(rec) is True

    def test_our_own_error_is_reported(self):
        """Error dari kode kita sendiri tetap dilaporkan."""
        rec = make_record(
            '[CRYPTO_SIGNAL] analyze failure for FIGR_HELOC-USD: boom',
            name='trading.handlers.scheduler.jobs_crypto',
        )
        assert _is_noise(rec) is False
