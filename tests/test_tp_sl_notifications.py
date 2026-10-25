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

    def fake_fetch(ticker, interval, period, skip_if_missing=False):
        return {'price': state['price'], 'candles': 50}

    def fake_save_outcome(key, outcome, **kwargs):
        state['outcomes'].append((key, outcome))

    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_crypto_data_combined', fake_fetch)
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
