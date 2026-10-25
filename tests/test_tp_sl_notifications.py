"""
Unit tests untuk notifikasi TP1/TP2/TP3/SL.

Dua hal yang dijaga di sini:
1. Notifikasi benar-benar terkirim ke user.
2. State "sudah kena TP" hanya ditandai SETELAH pesan terkirim, jadi
   satu timeout tidak bisa membuat notifikasinya hilang permanen.
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))


class FakeBot:
    """Bot palsu: mencatat pesan, atau melempar error kalau diminta."""

    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def send_message(self, chat_id, text, **kwargs):
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))
        return None


class FakeApp:
    def __init__(self, error=None):
        self.bot = FakeBot(error)
        self.job_queue = FakeJobQueue()


class FakeJobQueue:
    """Job queue palsu - cuma supaya scheduler tidak error."""

    def __init__(self):
        self.scheduled = []

    def run_once(self, target, when=0):
        self.scheduled.append((target, when))


def make_signal(**overrides):
    """Sinyal crypto BUY: entry 100, ATR 1, TF 60 (swing).

    TP/SL dihitung ulang dari ATR saat cek: sl=97, tp1=102, tp2=104,
    tp3=107.
    """
    signal = {
        'name': 'Bitcoin',
        'entry': 100.0,
        'tp1': 102.0, 'tp2': 104.0, 'tp3': 107.0, 'sl': 97.0,
        'time': None,
        'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
        'type': 'crypto',
        'direction': 'LONG',
        'ticker_raw': 'BTC-USD',
        'buy_score': 70, 'sell_score': 0,
        'quality': 'STRONG',
        'signal_type': 'BUY',
        'is_reversal': False,
        'atr': 1.0,
        'user_id': '1',
        'timeframe': '60',
    }
    signal.update(overrides)
    return signal


@pytest.fixture
def crypto_loop(monkeypatch):
    """Loop TP/SL crypto dengan harga & DB palsu."""
    from handlers.scheduler import jobs_crypto

    state = {
        'signals': {},
        'outcomes': [],
        'price': 103.0,
    }

    def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
        return {'price': state['price'], 'candles': 50, 'rsi': 45.0}

    def fake_save_outcome(key, outcome, **kwargs):
        state['outcomes'].append((key, outcome))

    # Job cek TP/SL memakai fetch_crypto_prices: Yahoo per ticker, lalu
    # CoinGecko bulk untuk yang tidak ada di Yahoo.
    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_crypto_data', fake_fetch)
    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_crypto_data_coingecko_bulk',
        lambda tickers, retry=3, use_cache=True: {})
    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_usd_idr_rate', lambda: 16000)
    monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome', fake_save_outcome)
    monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
        '1': {'notif_crypto': True, 'timeframe': '60'}})
    monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)
    return state


class TestTpNotificationDelivery:
    """TP1/TP2/TP3 harus terkirim dan baru THEN ditandai kena."""

    def test_tp1_is_sent(self, crypto_loop):
        """TP1 kena -> pesan terkirim ke user."""
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_tp1_marked_only_after_send(self, crypto_loop):
        """Outcome TP1 baru dicatat setelah pesan terkirim."""
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert crypto_loop['outcomes'] == [('CRYPTO_BTC-USD_1', 'tp1')]

    def test_failed_send_does_not_mark_tp_as_hit(self, crypto_loop):
        """Kalau kirim gagal, sinyal TIDAK boleh ditandai sudah kena.

        Kalau ditandai sebelum kirim, notifikasi hilang permanen: state
        bilang "sudah di-notifikasi" padahal user tidak pernah
        menerimanya, dan siklus berikutnya tidak mengirim lagi.
        """
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}
        app = FakeApp(error=TimeoutError('jaringan putus'))

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert app.bot.sent == []
        assert crypto_loop['outcomes'] == []
        signal = crypto_loop['signals']['CRYPTO_BTC-USD_1']
        assert signal['tp_hit']['tp1'] is False

    def test_failed_send_is_retried_next_cycle(self, crypto_loop):
        """Siklus berikutnya masih mengirim notifikasi yang gagal tadi."""
        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}
        from handlers.scheduler import jobs_crypto

        asyncio.run(jobs_crypto.check_crypto_tp_sl(
            FakeApp(error=TimeoutError('jaringan putus'))))
        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert crypto_loop['outcomes'] == [('CRYPTO_BTC-USD_1', 'tp1')]

    def test_tp3_not_sent_twice(self, crypto_loop):
        """TP3 menutup posisi; siklus berikutnya tidak mengirim lagi."""
        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}
        crypto_loop['price'] = 108.0
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))
        sent_first = len(app.bot.sent)
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        # Harga 108 > tp3, jadi TP1+TP2+TP3 semuanya kena dalam satu
        # siklus. Yang penting: siklus kedua tidak mengirim apa-apa.
        assert sent_first == 3
        assert len(app.bot.sent) == sent_first
        assert any('TP3' in text for _, text in app.bot.sent)


class TestShortDirection:
    """Sinyal SHORT harus tetap SHORT setelah dipulihkan dari DB."""

    def test_short_signal_hits_tp_when_price_falls(self, crypto_loop):
        """Sinyal SHORT: TP1 kena saat harga turun, bukan naik."""
        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal(
            direction='SHORT', signal_type='SELL', entry=100.0, atr=1.0)}
        crypto_loop['price'] = 97.0
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_short_signal_sl_when_price_rises(self, crypto_loop):
        """Sinyal SHORT: SL kena saat harga naik (di atas entry+3*ATR)."""
        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal(
            direction='SHORT', signal_type='SELL', entry=100.0, atr=1.0)}
        crypto_loop['price'] = 104.0
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert 'SL' in app.bot.sent[0][1]


class TestTpSlFetchesPricesInParallel:
    """Harga semua sinyal yang dipantau diambil sekaligus.

    Kalau satu per satu, tiap sinyal antre di limiter provider. Saat scan
    crypto sedang jalan limiter itu penuh, jadi satu siklus cek TP/SL bisa
    lewat 2 menit - job berikutnya lalu kena "maximum number of running
    instances reached" dan notifikasi TP/SL telat.

    Karena itu harga diambil lewat satu request bulk CoinGecko, bukan satu
    request Yahoo per ticker. Yahoo hanya dipakai untuk ticker yang tidak
    ada di CoinGecko atau yang harganya beda jauh dari harga scan terakhir.
    """

    @pytest.fixture(autouse=True)
    def _no_cached_yahoo_price(self, monkeypatch):
        """Default: cache harga provider kosong.

        Kalau tidak, test bisa ikut terpengaruh sisa cache dari test lain
        (cache-nya global untuk satu proses).
        """
        from handlers.scheduler import jobs_crypto
        monkeypatch.setattr(jobs_crypto, '_get_cached_yahoo_data',
                            lambda t, i, p: None)

    def test_prices_are_fetched_together(self, monkeypatch):
        import time
        from handlers.scheduler import jobs_crypto

        state = {'inflight': 0, 'max_inflight': 0, 'calls': []}

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
            state['inflight'] += 1
            state['max_inflight'] = max(state['max_inflight'],
                                        state['inflight'])
            state['calls'].append(ticker)
            time.sleep(0.15)
            state['inflight'] -= 1
            return {'price': 103.0, 'candles': 50, 'rsi': 45.0}

        signals = {
            'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD'),
            'CRYPTO_ETH-USD_1': make_signal(ticker_raw='ETH-USD'),
            'CRYPTO_SOL-USD_1': make_signal(ticker_raw='SOL-USD'),
        }

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk',
                            lambda tickers, retry=3, use_cache=True: {})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        asyncio.run(jobs_crypto.check_crypto_tp_sl(FakeApp()))

        assert sorted(state['calls']) == ['BTC-USD', 'ETH-USD', 'SOL-USD']
        assert state['max_inflight'] > 1, \
            "harga harus diambil paralel, bukan satu per satu"

    def test_all_tickers_go_in_one_bulk_request(self, monkeypatch):
        """Semua ticker yang dipantau masuk satu request bulk CoinGecko."""
        from handlers.scheduler import jobs_crypto

        bulk_calls = []

        def fake_bulk(tickers, retry=3, use_cache=True):
            bulk_calls.append((list(tickers), use_cache))
            return {}

        signals = {
            'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD'),
            'CRYPTO_ETH-USD_1': make_signal(ticker_raw='ETH-USD'),
        }

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data',
                            lambda t, i, p, skip_if_missing=False:
                            {'price': 103.0, 'candles': 50, 'rsi': 45.0})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        asyncio.run(jobs_crypto.check_crypto_tp_sl(FakeApp()))

        assert len(bulk_calls) == 1, "semua ticker harus 1 request bulk"
        assert sorted(bulk_calls[0][0]) == ['BTC-USD', 'ETH-USD']
        assert bulk_calls[0][1] is False, \
            "harga TP/SL harus segar, bukan dari cache CoinGecko"

    def test_coingecko_price_is_used_when_it_matches_the_scan_price(
            self, monkeypatch):
        """Harga CoinGecko yang cocok dengan data scan dipakai langsung."""
        from handlers.scheduler import jobs_crypto

        yahoo_calls = []

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
            yahoo_calls.append(ticker)
            return {'price': 999.0, 'candles': 50, 'rsi': 45.0}

        def fake_bulk(tickers, retry=3, use_cache=True):
            return {'BTC-USD': {'price': 103.0, 'candles': 168, 'rsi': 45.0}}

        signals = {'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD')}

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: {'price': 102.0, 'candles': 50})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert yahoo_calls == [], \
            "harga CoinGecko sudah cocok, tidak perlu request Yahoo"
        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_mismatched_price_falls_back_to_yahoo(self, monkeypatch):
        """Kalau harga CoinGecko beda jauh, pakai harga Yahoo.

        Simbol yang sama bisa menunjuk aset berbeda di CoinGecko dan
        Yahoo. Kalau harga CoinGecko dipakai, level TP/SL dihitung dari
        aset yang salah.
        """
        from handlers.scheduler import jobs_crypto

        yahoo_calls = []

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
            yahoo_calls.append(ticker)
            return {'price': 103.0, 'candles': 50, 'rsi': 45.0}

        def fake_bulk(tickers, retry=3, use_cache=True):
            return {'BTC-USD': {'price': 0.3, 'candles': 168, 'rsi': 45.0}}

        signals = {'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD')}

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: {'price': 102.0, 'candles': 50})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert yahoo_calls == ['BTC-USD'], \
            "harga yang tidak cocok harus diambil dari Yahoo"
        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_cached_yahoo_price_avoids_new_request(self, monkeypatch):
        """Harga Yahoo dari cache dipakai, tanpa request baru.

        Request baru harus antre di limiter Yahoo yang sedang dipakai
        penuh oleh scan crypto. Satu siklus yang tertunda >2 menit bikin
        siklus berikutnya kena skip ("maximum number of running
        instances"), jadi notifikasi TP/SL telat.
        """
        from handlers.scheduler import jobs_crypto

        yahoo_calls = []

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
            yahoo_calls.append(ticker)
            return {'price': 103.0, 'candles': 50, 'rsi': 45.0}

        def fake_bulk(tickers, retry=3, use_cache=True):
            # Harga CoinGecko menunjuk aset lain (0.3 vs 102).
            return {'BTC-USD': {'price': 0.3, 'candles': 168, 'rsi': 45.0}}

        signals = {'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD')}

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: {'price': 102.0, 'candles': 50})
        # Cache harga provider ada isinya (dari scan terakhir).
        monkeypatch.setattr(
            jobs_crypto, '_get_cached_yahoo_data',
            lambda t, i, p: {'price': 103.0, 'candles': 50, 'rsi': 45.0})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert yahoo_calls == [], \
            "harga Yahoo sudah ada di cache, tidak perlu request baru"
        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_cached_coingecko_used_when_limiter_is_full(self, monkeypatch):
        """Kalau limiter CoinGecko penuh, pakai harga cache, jangan tunggu.

        Menunggu slot di limiter bisa memakan hampir semenit. Itu sendiri
        sudah cukup untuk bikin siklus cek TP/SL lewat batas 2 menit,
        sehingga siklus berikutnya kena skip.
        """
        from handlers.scheduler import jobs_crypto

        bulk_calls = []

        def fake_bulk(tickers, retry=3, use_cache=True):
            bulk_calls.append(list(tickers))
            return {}

        def fake_cached(tickers):
            return {'BTC-USD': {'price': 103.0, 'candles': 168, 'rsi': 45.0}}

        signals = {'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD')}

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_cached', fake_cached)
        monkeypatch.setattr(jobs_crypto, 'coingecko_can_call_now',
                            lambda: False)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: {'price': 102.0, 'candles': 50})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert bulk_calls == [], \
            "limiter penuh harus pakai cache, bukan menunggu request baru"
        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_waits_for_slot_when_cache_is_empty(self, monkeypatch):
        """Limiter penuh tapi cache belum terisi -> tunggu slot.

        Tepat setelah restart cache CoinGecko masih kosong. Kalau job
        tidak menunggu, satu siklus cek TP/SL berjalan tanpa harga
        sama sekali - tidak ada sinyal yang dicek.
        """
        from handlers.scheduler import jobs_crypto

        bulk_calls = []

        def fake_bulk(tickers, retry=3, use_cache=True):
            bulk_calls.append(list(tickers))
            return {'BTC-USD': {'price': 103.0, 'candles': 168, 'rsi': 45.0}}

        def fake_cached(tickers):
            return {}

        signals = {'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD')}

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_cached', fake_cached)
        monkeypatch.setattr(jobs_crypto, 'coingecko_can_call_now',
                            lambda: False)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: {'price': 102.0, 'candles': 50})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(bulk_calls) == 1, \
            "cache kosong harus menunggu slot, bukan jalan tanpa harga"
        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]

    def test_ticker_missing_from_coingecko_falls_back_to_yahoo(self,
                                                                monkeypatch):
        """Ticker yang tidak dikenal CoinGecko diambil dari Yahoo."""
        from handlers.scheduler import jobs_crypto

        yahoo_calls = []

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
            yahoo_calls.append(ticker)
            return {'price': 103.0, 'candles': 50, 'rsi': 45.0}

        def fake_bulk(tickers, retry=3, use_cache=True):
            return {}

        signals = {
            'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD'),
            'CRYPTO_ETH-USD_1': make_signal(ticker_raw='ETH-USD'),
        }

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: None)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        asyncio.run(jobs_crypto.check_crypto_tp_sl(FakeApp()))

        assert sorted(yahoo_calls) == ['BTC-USD', 'ETH-USD']

    def test_coingecko_price_used_when_yahoo_has_no_data(self, monkeypatch):
        """Ticker yang tidak ada di Yahoo tetap punya harga dari CoinGecko.

        Kalau tidak, sinyal ticker itu tidak pernah dicek TP/SL sama sekali.
        """
        from handlers.scheduler import jobs_crypto

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                     priority=False):
            return None

        def fake_bulk(tickers, retry=3, use_cache=True):
            return {'BTC-USD': {'price': 103.0, 'candles': 168, 'rsi': 45.0}}

        signals = {'CRYPTO_BTC-USD_1': make_signal(ticker_raw='BTC-USD')}

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk', fake_bulk)
        monkeypatch.setattr(jobs_crypto, 'get_crypto_scan_data',
                            lambda t, i, p: None)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: signals)
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 1, \
            "sinyal harus tetap dicek TP/SL walau Yahoo tidak punya data"
        assert 'TP1' in app.bot.sent[0][1]


class TestStockLoop:
    """Loop TP/SL saham punya aturan yang sama."""

    @pytest.fixture
    def stock_loop(self, monkeypatch):
        from handlers.scheduler import jobs_stocks

        state = {'signals': {}, 'outcomes': [], 'price': 103.0}

        def fake_fetch(ticker, interval, period):
            return {'price': state['price'], 'candles': 50}

        def fake_save_outcome(key, outcome, **kwargs):
            state['outcomes'].append((key, outcome))

        # Rabu, 10:00 WIB - hari kerja dan jam pasar.
        rabu = datetime(2026, 10, 21, 10, 0, tzinfo=WIB)
        monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: rabu)
        monkeypatch.setattr(
            jobs_stocks.stock_service, 'get_stock_data_combined', fake_fetch)
        monkeypatch.setattr(jobs_stocks.db, 'save_signal_outcome', fake_save_outcome)
        monkeypatch.setattr(jobs_stocks, '_get_user_db', lambda: {
            '1': {'notif_saham': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_stocks, '_get_last_buy_signals',
                            lambda: state['signals'])
        monkeypatch.setattr(jobs_stocks, '_remove_signal', lambda key: None)
        return state

    def test_stock_tp1_is_sent(self, stock_loop):
        """TP1 saham terkirim."""
        from handlers.scheduler import jobs_stocks

        stock_loop['signals'] = {'STOCK_BBCA_1': make_signal(
            type='stock', ticker_raw='BBCA')}
        app = FakeApp()
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert stock_loop['outcomes'] == [('STOCK_BBCA_1', 'tp1')]

    def test_stock_failed_send_does_not_mark_tp_as_hit(self, stock_loop):
        """Kirim gagal -> TP1 belum ditandai, akan dicoba lagi."""
        from handlers.scheduler import jobs_stocks

        stock_loop['signals'] = {'STOCK_BBCA_1': make_signal(
            type='stock', ticker_raw='BBCA')}
        app = FakeApp(error=TimeoutError('jaringan putus'))
        asyncio.run(jobs_stocks.check_stock_tp_sl(app))

        assert app.bot.sent == []
        assert stock_loop['outcomes'] == []
        signal = stock_loop['signals']['STOCK_BBCA_1']
        assert signal['tp_hit']['tp1'] is False
