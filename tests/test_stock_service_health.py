"""
Unit tests for the "ticker tanpa data" handling in services/stock_service.py

Ticker yang tidak punya candles harus dibedakan dari ticker yang halamannya
tidak bisa diambil karena jaringan putus. Kalau salah, satu gangguan
jaringan akan menandai semua ticker dan bot berhenti bekerja selama 24 jam.
"""
import sys
import os
import logging

import pandas as pd
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeTicker:
    """Ticker palsu: history() mengembalikan apa yang diminta test."""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.info = {}

    def history(self, **kwargs):
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Registry ticker_health dengan file status di folder sementara."""
    from services import ticker_health
    monkeypatch.setattr(ticker_health, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ticker_health, 'STATE_FILE', str(tmp_path / 'ticker_unavailable.json'))
    monkeypatch.setattr(ticker_health, '_entries', {})
    monkeypatch.setattr(ticker_health, '_loaded', False)
    return ticker_health


@pytest.fixture
def service(monkeypatch):
    """StockService tanpa sumber data lain (semua fallback gagal)."""
    from services import stock_service
    svc = stock_service.StockService()
    monkeypatch.setattr(svc, 'get_stock_data_v8', lambda *a, **k: None)
    monkeypatch.setattr(svc, 'get_stock_data_tradingview', lambda *a, **k: None)
    monkeypatch.setattr(svc, 'get_stock_data_finnhub', lambda *a, **k: None)
    return svc


class TestDropNoPriceDataFilter:
    """Tests for the yfinance log filter."""

    def _record(self, message):
        return logging.LogRecord('yfinance', logging.ERROR, 'f.py', 1, message, None, None)

    def test_drops_no_price_data(self):
        """Log 'no price data found' dibuang."""
        from utils.log_filters import DropNoPriceData
        rec = self._record('$BIKE.JK: possibly delisted; no price data found  (period=3d)')
        assert DropNoPriceData().filter(rec) is False

    def test_keeps_other_yfinance_errors(self):
        """Error lain dari yfinance tetap diteruskan."""
        from utils.log_filters import DropNoPriceData
        rec = self._record('Too Many Requests. Rate limited. Try after a while.')
        assert DropNoPriceData().filter(rec) is True

    def test_drops_message_at_any_level(self):
        """Filter berlaku pada pesannya, bukan pada level log."""
        from utils.log_filters import DropNoPriceData
        rec = self._record('$BBCA.JK: no price data found')
        rec.levelno = logging.INFO
        assert DropNoPriceData().filter(rec) is False


class TestFetchState:
    """Tests for the "Yahoo tidak punya data" flag."""

    def test_empty_history_sets_flag(self, service, monkeypatch):
        """History kosong berarti ticker tidak punya data."""
        from services import stock_service
        monkeypatch.setattr(stock_service.yf, 'Ticker',
                            lambda *a, **k: FakeTicker(result=pd.DataFrame()))
        assert service.get_stock_data('NOSUATEST.JK', '5m', '3d') is None
        assert stock_service._yahoo_had_no_data() is True

    def test_exception_does_not_set_flag(self, service, monkeypatch):
        """Error jaringan tidak boleh dianggap 'ticker tidak ada datanya'."""
        from services import stock_service
        monkeypatch.setattr(stock_service.yf, 'Ticker',
                            lambda *a, **k: FakeTicker(error=RuntimeError('timeout')))
        assert service.get_stock_data('JARINGAN.JK', '5m', '3d') is None
        assert stock_service._yahoo_had_no_data() is False

    def test_flag_resets_between_calls(self, service, monkeypatch):
        """Status dari fetch sebelumnya tidak boleh bocor ke ticker berikutnya."""
        from services import stock_service
        monkeypatch.setattr(stock_service.yf, 'Ticker',
                            lambda *a, **k: FakeTicker(result=pd.DataFrame()))
        service.get_stock_data('KOSONG1.JK', '5m', '3d')
        assert stock_service._yahoo_had_no_data() is True
        service.get_stock_data('KOSONG2.JK', '5m', '3d')
        assert stock_service._yahoo_had_no_data() is True


class TestMarking:
    """Tests that the ticker gets marked only when it truly has no data."""

    def test_marks_when_all_sources_fail(self, service, registry, monkeypatch):
        """Semua sumber gagal dan Yahoo tidak punya data -> ditandai."""
        from services import stock_service
        monkeypatch.setattr(stock_service.yf, 'Ticker',
                            lambda *a, **k: FakeTicker(result=pd.DataFrame()))
        service.get_stock_data_combined('BIKE.JK', '5m', '3d')
        assert registry.is_unavailable('BIKE') is True

    def test_not_marked_on_network_error(self, service, registry, monkeypatch):
        """Jaringan putus -> ticker TIDAK boleh ditandai."""
        from services import stock_service
        monkeypatch.setattr(stock_service.yf, 'Ticker',
                            lambda *a, **k: FakeTicker(error=RuntimeError('timeout')))
        service.get_stock_data_combined('JARINGAN.JK', '5m', '3d')
        assert registry.is_unavailable('JARINGAN') is False

    def test_not_marked_when_other_source_has_data(self, service, registry, monkeypatch):
        """Kalau sumber lain berhasil, ticker tetap dipakai."""
        from services import stock_service
        monkeypatch.setattr(stock_service.yf, 'Ticker',
                            lambda *a, **k: FakeTicker(result=pd.DataFrame()))
        monkeypatch.setattr(service, 'get_stock_data_tradingview',
                            lambda *a, **k: {'candles': 10, 'price': 100})
        result = service.get_stock_data_combined('ADAJADANYA.JK', '5m', '3d')
        assert result.success is True
        assert registry.is_unavailable('ADAJADANYA') is False
