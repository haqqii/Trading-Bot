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

    def fake_resolve(symbol):
        # Ticker tidak dikenal sebagai crypto - tidak ada yang ketemu.
        state['priority_calls'].append(('resolve', symbol, True))
        return None

    monkeypatch.setattr(analisa_mod.crypto_service, 'get_crypto_data', fake_probe)
    monkeypatch.setattr(analisa_mod.crypto_service, 'resolve_symbol', fake_resolve)
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

        def fake_resolve(*args, **kwargs):
            return None

        monkeypatch.setattr(analisa_mod.crypto_service, 'load_crypto_pairs',
                            slow_load)
        monkeypatch.setattr(analisa_mod.crypto_service, 'get_crypto_data',
                            fake_probe)
        # Resolusi simbol CoinGecko dimock: test tidak boleh pakai jaringan.
        monkeypatch.setattr(analisa_mod.crypto_service, 'resolve_symbol',
                            fake_resolve)

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


# --- Resolusi simbol CoinGecko --------------------------------------------
#
# Universe crypto bot = 250 koin teratas by market cap. Token yang lebih
# kecil (mis. OVERTAKE, rank ~670) tidak pernah masuk daftar itu, dan tanpa
# resolusi simbol /analisa untuk token seperti itu selalu berakhir dengan
# pesan "saham tidak ditemukan" - padahal itu bukan saham.

_RESOLVED = {
    'ticker': 'TAKE-USD',
    'id': 'overtake',
    'name': 'OVERTAKE',
    'symbol': 'TAKE',
    'rank': 668,
}


@pytest.fixture
def resolve_env(monkeypatch):
    """Lingkungan di mana ticker asing bisa diresolusi jadi crypto."""
    from handlers.commands import analisa as analisa_mod
    from services.stock_service import StockDataResult

    state = {'resolved': [], 'cg_calls': [], 'stock_calls': []}

    monkeypatch.setattr(analisa_mod, 'ALL_STOCKS', {'BBCA': 'Bank Central Asia'})
    monkeypatch.setattr(analisa_mod.crypto_service, 'crypto_pairs',
                        {'BTC-USD': 'Bitcoin'})

    def fake_resolve(symbol):
        state['resolved'].append(symbol)
        return dict(_RESOLVED)

    def fake_cg_fetch(ticker, priority=False, **kwargs):
        state['cg_calls'].append((ticker, priority))
        return {
            'ticker': ticker,
            'name': 'OVERTAKE',
            'price': 0.0625,
            'change': 1.2,
            'ma_fast': 0.061,
            'ma_slow': 0.059,
            'macd': 0.0004,
            'macd_signal': 0.0003,
            'macd_hist': 0.0001,
            'bb_upper': 0.07,
            'bb_lower': 0.05,
            'rsi': 55.0,
            'atr': 0.003,
            'volume': 5_000_000,
            'volume_ratio': 1.0,
            'vwap': 0.062,
            'htf_trend': 'up',
            'candles': 168,
            'source': 'coingecko',
            'sr': {},
        }

    def fake_probe(*args, **kwargs):
        return None

    def fake_stock_fetch(ticker, interval='5m', period='5d', priority=False):
        state['stock_calls'].append(ticker)
        return StockDataResult(success=False, error='ticker tidak ada',
                               source='all', retry_after=30)

    def fake_news(*args, **kwargs):
        return ([], None)

    monkeypatch.setattr(analisa_mod.crypto_service, 'resolve_symbol', fake_resolve)
    monkeypatch.setattr(analisa_mod.crypto_service, 'get_crypto_data_coingecko',
                        fake_cg_fetch)
    monkeypatch.setattr(analisa_mod.crypto_service, 'get_crypto_data', fake_probe)
    monkeypatch.setattr(analisa_mod.stock_service, 'get_stock_data_combined',
                        fake_stock_fetch)
    monkeypatch.setattr(analisa_mod.news_service, 'get_stock_news', fake_news)
    monkeypatch.setattr(analisa_mod.news_service, 'get_crypto_news', fake_news)
    monkeypatch.setattr(analisa_mod._price_cache, 'get', lambda *a, **k: None)
    monkeypatch.setattr(analisa_mod._price_cache, 'get_stale',
                        lambda *a, **k: None)

    return state


class TestAnalisaResolvesUnknownCrypto:
    """Ticker crypto di luar daftar pair harus bisa dianalisis."""

    @pytest.mark.asyncio
    async def test_unknown_ticker_resolved_to_crypto(self, resolve_env):
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['TAKE'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        texts = ' '.join(update.message.sent)
        assert resolve_env['resolved'] == ['TAKE']
        assert 'TAKE' in texts
        assert 'OVERTAKE' in texts
        assert 'tidak ditemukan' not in texts

    @pytest.mark.asyncio
    async def test_resolved_crypto_data_from_coingecko(self, resolve_env):
        """Data diambil dari CoinGecko, bukan Yahoo."""
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['TAKE'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        assert resolve_env['cg_calls'], "data tidak diambil dari CoinGecko"
        ticker, priority = resolve_env['cg_calls'][0]
        assert ticker == 'TAKE-USD'
        assert priority is True

    @pytest.mark.asyncio
    async def test_idr_suffix_ticker_resolved(self, resolve_env):
        """TAKEIDR harus tetap ketemu (user menambahkan IDR karena harga
        di bot ditampilkan dalam Rupiah)."""
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['TAKEIDR'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        texts = ' '.join(update.message.sent)
        assert resolve_env['resolved'] == ['TAKEIDR']
        assert 'OVERTAKE' in texts
        assert 'tidak ditemukan' not in texts

    @pytest.mark.asyncio
    async def test_known_stock_not_resolved_as_crypto(self, resolve_env):
        """Saham IDX tidak boleh dialihkan ke crypto, meski simbolnya
        kebetulan ada di CoinGecko."""
        from handlers.commands.analisa import analisa_cmd

        update = _FakeUpdate()
        ctx = _FakeCtx(['BBCA'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        assert resolve_env['resolved'] == [], (
            "saham IDX seharusnya tidak pernah masuk resolusi simbol crypto")

    @pytest.mark.asyncio
    async def test_stock_wins_over_crypto_symbol(self, resolve_env, monkeypatch):
        """Kalau tickernya saham IDX, data saham yang dipakai."""
        from handlers.commands import analisa as analisa_mod
        from handlers.commands.analisa import analisa_cmd
        from services.stock_service import StockDataResult

        def stock_ok(ticker, interval='5m', period='5d', priority=False):
            return StockDataResult(success=True, data={
                'ticker': ticker, 'name': 'Bank Central Asia', 'price': 9500,
                'change': 1.0, 'ma_fast': 9400, 'ma_slow': 9300,
                'macd_hist': 5.0, 'rsi': 55.0, 'volume_ratio': 1.1,
                'candles': 100, 'source': 'yahoo', 'sr': {},
            }, source='yahoo')

        monkeypatch.setattr(analisa_mod.stock_service,
                            'get_stock_data_combined', stock_ok)

        update = _FakeUpdate()
        ctx = _FakeCtx(['BBCA'])
        await asyncio.wait_for(analisa_cmd(update, ctx), timeout=30)

        texts = ' '.join(update.message.sent)
        assert 'Bank Central Asia' in texts
        assert resolve_env['resolved'] == []
