"""
Unit tests for utils/log_filters.py

yfinance menulis "ticker ini tidak punya datanya" sebagai ERROR. Kalau record
itu lolos ke root logger, error-nya naik jadi alert ke admin - padahal bukan
bug, cuma informasi bahwa ticker tersebut tidak ada di Yahoo.
"""
import sys
import os
import logging

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_record(message, name='yfinance', level=logging.ERROR):
    return logging.LogRecord(name, level, 'file.py', 1, message, None, None)


class TestDropNoPriceData:
    """Filter untuk log 'ticker tidak punya data'."""

    def test_drops_yfinance_1_7_message(self):
        """Pesan yfinance versi sekarang harus dibuang.

        yfinance 1.7 menulis '$HYPE-USD: No data found, symbol may be delisted'.
        Filter lama hanya tahu 'no price data found' dan 'possibly delisted',
        jadi pesan ini bocor ke admin.
        """
        from utils.log_filters import DropNoPriceData
        rec = make_record('$HYPE-USD: No data found, symbol may be delisted')
        assert DropNoPriceData().filter(rec) is False

    def test_drops_older_message(self):
        """Pesan lama (yfinance < 1.7) tetap dibuang."""
        from utils.log_filters import DropNoPriceData
        rec = make_record('$BIKE.JK: possibly delisted; no price data found  (period=3d)')
        assert DropNoPriceData().filter(rec) is False

    def test_drops_quote_not_found(self):
        """404 quote not found juga dibuang."""
        from utils.log_filters import DropNoPriceData
        rec = make_record('HTTP Error 404: Quote not found for symbol ZZZ.JK')
        assert DropNoPriceData().filter(rec) is False

    def test_keeps_rate_limit(self):
        """Rate limit dari yfinance harus tetap terlihat."""
        from utils.log_filters import DropNoPriceData
        rec = make_record('Too Many Requests. Rate limited. Try after a while.')
        assert DropNoPriceData().filter(rec) is True

    def test_keeps_server_error(self):
        """Server error provider juga harus tetap terlihat."""
        from utils.log_filters import DropNoPriceData
        rec = make_record('HTTP Error 500: Internal Server Error')
        assert DropNoPriceData().filter(rec) is True

    def test_works_on_any_level(self):
        """Filter berlaku pada pesannya, bukan pada level log."""
        from utils.log_filters import DropNoPriceData
        rec = make_record('$BBCA.JK: no price data found', level=logging.INFO)
        assert DropNoPriceData().filter(rec) is False


class TestInstallProviderFilters:
    """Filter harus terpasang di logger yfinance yang asli."""

    def test_attaches_to_yfinance_logger(self):
        """Logger 'yfinance' dapat filter."""
        from utils.log_filters import DropNoPriceData, install_provider_filters
        logger = logging.getLogger('yfinance')
        install_provider_filters()
        assert any(isinstance(f, DropNoPriceData) for f in logger.filters)

    def test_idempotent(self):
        """Pemasangan berulang tidak menambah filter berulang."""
        from utils.log_filters import DropNoPriceData, install_provider_filters
        logger = logging.getLogger('yfinance')
        install_provider_filters()
        install_provider_filters()
        count = sum(1 for f in logger.filters if isinstance(f, DropNoPriceData))
        assert count == 1

    def test_filter_blocks_record_for_yfinance_logger(self):
        """Record yfinance yang di-'no data' tidak boleh sampai ke handler."""
        from utils.log_filters import install_provider_filters
        install_provider_filters()
        logger = logging.getLogger('yfinance')
        caught = []

        class Collector(logging.Handler):
            def emit(self, record):
                caught.append(record)

        handler = Collector()
        logger.addHandler(handler)
        try:
            logger.error('$HYPE-USD: No data found, symbol may be delisted')
            logger.error('Too Many Requests. Rate limited.')
        finally:
            logger.removeHandler(handler)
        assert len(caught) == 1
        assert 'Rate limited' in caught[0].getMessage()


class TestStockServiceUsesSharedFilter:
    """stock_service harus pakai filter yang sama."""

    def test_stock_service_uses_shared_filter(self):
        """stock_service tidak punya filter sendiri lagi.

        Filter dipasang di startup (utils/log_filters.py) untuk semua jalur
        yang pakai yfinance - saham, crypto, chart.
        """
        import services.stock_service as ss
        assert hasattr(ss, 'install_provider_filters')

    def test_no_data_message_is_dropped_for_stock_path(self):
        """Pesan 'tidak ada datanya' tetap dibuang di jalur saham."""
        from utils.log_filters import DropNoPriceData
        rec = make_record('$HYPE-USD: No data found, symbol may be delisted')
        assert DropNoPriceData().filter(rec) is False


class TestNoDataReported:
    """Pencatatan pesan 'ticker ini tidak ada di Yahoo'.

    Crypto service butuh ini untuk bedakan:
      - ticker memang tidak ada di Yahoo -> boleh dicatat, dilewati nanti
      - request yang gagal (timeout, rate limit) -> bukan ticker hilang
    """

    def _emit(self, message):
        """Pemicu pesan error dari logger yfinance."""
        from utils.log_filters import install_provider_filters
        install_provider_filters()
        logging.getLogger('yfinance').error(message)

    def test_explicit_absence_is_reported(self):
        """Yahoo bilang 'symbol may be delisted' -> dilaporkan."""
        import time
        from utils.log_filters import no_data_reported
        since = time.time()
        self._emit('$SUI-USD: No data found, symbol may be delisted')
        assert no_data_reported('SUI-USD', since) is True

    def test_generic_no_price_data_is_not_reported(self):
        """'no price data found' generik belum tentu ticker hilang.

        Pesan yang sama muncul kalau request-nya gagal (timeout, koneksi
        putus) - itu bukan bukti ticker hilang, jadi tidak boleh dilaporkan.
        """
        import time
        from utils.log_filters import no_data_reported
        since = time.time()
        self._emit('$SUI-USD: possibly delisted; no price data found  (period=1mo)')
        assert no_data_reported('SUI-USD', since) is False

    def test_other_ticker_not_counted(self):
        """Laporan ticker lain tidak boleh ikut dihitung."""
        import time
        from utils.log_filters import no_data_reported
        since = time.time()
        self._emit('$PEPE-USD: No data found, symbol may be delisted')
        assert no_data_reported('SUI-USD', since) is False

    def test_message_before_window_ignored(self):
        """Pesan lama tidak boleh dihitung untuk request baru."""
        import time
        from utils.log_filters import no_data_reported
        self._emit('$SUI-USD: No data found, symbol may be delisted')
        assert no_data_reported('SUI-USD', time.time() + 60) is False
