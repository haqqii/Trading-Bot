"""Test untuk command /analisa - jalur yang dipicu user.

Fokusnya dua hal yang dulu bikin /analisa kelihatan "gabisa":
  1. event loop dibekukan selama menunggu provider (bot tidak bisa
     membalas perintah lain selama analysis jalan),
  2. request user antre di belakang scan crypto di limiter Yahoo.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeMessage:
    """Minimal telegram Message."""

    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)
        return text


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMessage()
        self.effective_user = None


class _FakeCtx:
    def __init__(self, args):
        self.args = args


@pytest.fixture
def analisa_env(monkeypatch):
    """Siapkan lingkungan /analisa untuk ticker yang tidak dikenal."""
    from handlers.commands import analisa as analisa_mod
    from services.stock_service import StockDataResult

    state = {'priority_calls': [], 'news_calls': 0}

    # Tidak ada saham IDX dan tidak ada pair crypto: ticker-nya harus
    # lolos ke jalur "tidak ditemukan".
    monkeypatch.setattr(analisa_mod, 'ALL_STOCKS', {})
    monkeypatch.setattr(analisa_mod.crypto_service, 'crypto_pairs',
                        {'BTC-USD': 'Bitcoin'})

    def fake_probe(ticker, interval='15m', period='3d', retry=3,
                   use_cache=True, skip_if_missing=False, priority=False):
        state['priority_calls'].append(('probe', ticker, priority))
        return None

    def fake_stock_fetch(ticker, interval='5m', period='5d', priority=False):
        state['priority_calls'].append(('stock', ticker, priority))
        # Blocking dan cukup lama - kalau dipanggil langsung di event loop,
        # loop ikut beku selama ini.
        time.sleep(1.5)
        return StockDataResult(success=False, error='ticker tidak ada',
                               source='all', retry_after=30)

    def fake_news(ticker, *args, **kwargs):
        state['news_calls'] += 1
        return ([], None)

    monkeypatch.setattr(analisa_mod.crypto_service, 'get_crypto_data', fake_probe)
    monkeypatch.setattr(analisa_mod.stock_service, 'get_stock_data_combined',
                        fake_stock_fetch)
    monkeypatch.setattr(analisa_mod.news_service, 'get_stock_news', fake_news)
    monkeypatch.setattr(analisa_mod._price_cache, 'get', lambda *a, **k: None)
    monkeypatch.setattr(analisa_mod._price_cache, 'get_stale',
                        lambda *a, **k: None)

    return state


class TestAnalisaDoesNotBlockEventLoop:
    """Analisis tidak boleh membekukan bot yang lain."""

    @pytest.mark.asyncio
    async def test_event_loop_stays_free_during_fetch(self, analisa_env):
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['SKYAI'])

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        try:
            await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)
        finally:
            hb.cancel()

        # Selama fetch 1.5 detik, loop harus tetap hidup - kalau tidak,
        # tidak ada perintah lain yang bisa dilayani (dan balasan
        # "Menganalisis..." juga gagal terkirim).
        assert ticks > 20, f"event loop beku selama analysis (ticks={ticks})"

    @pytest.mark.asyncio
    async def test_unknown_ticker_gets_clear_message(self, analisa_env):
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['SKYAI'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        texts = ' '.join(update.message.sent)
        assert 'tidak ditemukan' in texts


class TestAnalisaDoesNotBlockOnPairLoading:
    """Pemuatan daftar pair tidak boleh membekukan event loop."""

    @pytest.mark.asyncio
    async def test_slow_pair_load_does_not_freeze_loop(self, monkeypatch):
        from handlers.commands import analisa as analisa_mod
        from handlers.commands.analisa import analisa_cmd

        monkeypatch.setattr(analisa_mod, 'ALL_STOCKS', {})
        # Daftar pair belum termuat (misal baru restart) dan pemuatannya
        # lambat - kalau dipanggil blocking, loop beku.
        monkeypatch.setattr(analisa_mod.crypto_service, 'crypto_pairs', {})

        def slow_load(*args, **kwargs):
            time.sleep(1.5)
            return {}

        def fake_probe(*args, **kwargs):
            return None

        monkeypatch.setattr(analisa_mod.crypto_service, 'load_crypto_pairs',
                            slow_load)
        monkeypatch.setattr(analisa_mod.crypto_service, 'get_crypto_data',
                            fake_probe)

        update = _FakeUpdate()
        ctx = _FakeCtx(['SKYAI'])

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        try:
            await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)
        finally:
            hb.cancel()

        assert ticks > 20, f"event loop beku saat load pair (ticks={ticks})"
        assert 'tidak ditemukan' in ' '.join(update.message.sent)


class TestAnalisaUsesPriorityQuota:
    """Request user harus pakai kuota limiter yang dipesan untuk user."""

    @pytest.mark.asyncio
    async def test_provider_calls_use_priority(self, analisa_env):
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['SKYAI'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        calls = analisa_env['priority_calls']
        assert calls, "tidak ada request provider yang tercatat"
        for kind, ticker, priority in calls:
            assert priority is True, (
                f"{kind} request untuk {ticker} tidak pakai priority - "
                f"bakal antre di belakang scan crypto")
