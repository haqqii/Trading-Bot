"""
Unit tests for ticker skipping in handlers/scheduler/jobs_stocks.py

Ticker tanpa data harga harus dilewati oleh jalur background (scan sinyal
& prefetch), supaya tidak ada request sia-sia dan tidak ada error yang
naik jadi alert.
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


WIB = timezone(timedelta(hours=7))


@pytest.fixture
def rabu():
    """Rabu, 10:00 WIB - hari kerja dan jam pasar."""
    return datetime(2026, 10, 21, 10, 0, tzinfo=WIB)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Registry ticker_health dengan file status di folder sementara."""
    from services import ticker_health
    monkeypatch.setattr(ticker_health, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ticker_health, 'STATE_FILE', str(tmp_path / 'ticker_unavailable.json'))
    monkeypatch.setattr(ticker_health, '_entries', {})
    monkeypatch.setattr(ticker_health, '_loaded', False)
    return ticker_health


class TestPrefetchSkipsUnavailable:
    """Tests for prefetch_stock_cache."""

    def test_unavailable_ticker_is_skipped(self, registry, monkeypatch, rabu):
        """Ticker tanpa data tidak boleh di-fetch."""
        from handlers.scheduler import jobs_stocks

        registry.mark_unavailable('BIKE')

        fetched = []

        class FakeStockService:
            def get_stock_data_combined(self, ticker, interval, period):
                fetched.append(ticker)
                return None

        monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: rabu)
        monkeypatch.setattr(jobs_stocks, 'ALL_STOCKS', {'BIKE': 'Bike', 'AAA': 'Aaa'})
        monkeypatch.setattr(jobs_stocks, 'stock_service', FakeStockService())
        monkeypatch.setattr(jobs_stocks, '_price_cache', type('C', (), {
            'get': staticmethod(lambda key: None)})())

        asyncio.run(jobs_stocks.prefetch_stock_cache(None))

        assert 'BIKE.JK' not in fetched
        assert 'AAA.JK' in fetched

    def test_backfills_with_next_ticker(self, registry, monkeypatch, rabu):
        """Ticker yang dilewati digantikan, jumlah prefetch tetap 30."""
        from handlers.scheduler import jobs_stocks

        registry.mark_unavailable('BIKE')

        fetched = []

        class FakeStockService:
            def get_stock_data_combined(self, ticker, interval, period):
                fetched.append(ticker)
                return None

        monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: rabu)
        stocks = {'BIKE': 'Bike'}
        stocks.update({f'T{i}': f'T{i}' for i in range(30)})
        monkeypatch.setattr(jobs_stocks, 'ALL_STOCKS', stocks)
        monkeypatch.setattr(jobs_stocks, 'stock_service', FakeStockService())
        monkeypatch.setattr(jobs_stocks, '_price_cache', type('C', (), {
            'get': staticmethod(lambda key: None)})())

        asyncio.run(jobs_stocks.prefetch_stock_cache(None))

        assert len(fetched) == 30
        assert 'BIKE.JK' not in fetched


class TestScanSkipsUnavailable:
    """Tests for check_stock_signals."""

    def test_unavailable_ticker_is_not_scanned(self, registry, monkeypatch, rabu):
        """Ticker tanpa data tidak boleh dipindai."""
        from handlers.scheduler import jobs_stocks

        registry.mark_unavailable('BIKE')

        scanned = []

        def fake_fetch(ticker, interval, period):
            scanned.append(ticker)
            return None, False

        monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: rabu)
        monkeypatch.setattr(jobs_stocks, 'ALL_STOCKS', {'BIKE': 'Bike', 'AAA': 'Aaa'})
        monkeypatch.setattr(jobs_stocks, 'get_stock_data_with_fallback', fake_fetch)
        monkeypatch.setattr(jobs_stocks, '_get_user_db', lambda: {
            '1': {'notif_saham': True, 'timeframe': '5'}})
        monkeypatch.setattr(jobs_stocks, '_get_last_buy_signals', lambda: {})
        monkeypatch.setattr(jobs_stocks, '_yahoo_stock_cooldown', 0)

        asyncio.run(jobs_stocks.check_stock_signals(None))

        assert 'BIKE.JK' not in scanned
        assert 'AAA.JK' in scanned
