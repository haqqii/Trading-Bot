"""Aturan keluar harus berlaku di bot live, bukan cuma di backtest.

Tiga hal yang dijaga:
1. Setelah TP1 kena, SL digeser ke entry - posisi ditutup break even,
   bukan cut loss.
2. Setelah TP2 kena, SL trailing - posisi ditutup di profit.
3. Dengan flag dimatikan, perilaku lama (SL diam) harus tetap berlaku.
"""
import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_tp_sl_notifications import FakeApp, make_signal  # noqa: E402


@pytest.fixture
def temp_db():
    """Database sementara - jangan sentuh DB bot yang sedang jalan."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    from db import Database
    database = Database(db_path)
    database.initialize()
    yield database, db_path
    database.close()
    try:
        os.unlink(db_path)
    except OSError:
        pass


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

    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_crypto_data', fake_fetch)
    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_crypto_data_coingecko_bulk',
        lambda tickers, retry=3, use_cache=True, priority=False: {})
    monkeypatch.setattr(jobs_crypto.crypto_service, 'get_usd_idr_rate', lambda: 16000)
    monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome', fake_save_outcome)
    monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
        '1': {'notif_crypto': True, 'timeframe': '60'}})
    monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)
    return state


def _signal(tp1=False, tp2=False, **overrides):
    return make_signal(tp_hit={'tp1': tp1, 'tp2': tp2, 'tp3': False},
                       **overrides)


class TestBreakevenExit:
    """Setelah TP1 kena, SL pindah ke harga entry."""

    def test_position_closed_at_breakeven(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True)}
        crypto_loop['price'] = 99.5          # di bawah entry, di atas SL lama (97)
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert crypto_loop['outcomes'] == [('CRYPTO_BTC-USD_1', 'beakeven')]
        assert len(app.bot.sent) == 1
        assert 'STOP DI ENTRY' in app.bot.sent[0][1]

    def test_without_flag_position_stays_open(self, crypto_loop, monkeypatch):
        from handlers.scheduler import jobs_crypto
        from services.signal_service import set_features

        saved = set_features(exit_mgmt=False)
        try:
            crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True)}
            crypto_loop['price'] = 99.5
            app = FakeApp()

            asyncio.run(jobs_crypto.check_crypto_tp_sl(app))
        finally:
            set_features(**saved)

        # SL lama (97) belum kena -> posisi tetap hidup.
        assert crypto_loop['outcomes'] == []
        assert app.bot.sent == []

    def test_tp1_message_mentions_breakeven(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal()}
        crypto_loop['price'] = 103.0
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 1
        assert 'dipindah ke harga entry' in app.bot.sent[0][1].lower()


class TestTrailingExit:
    """Setelah TP2 kena, SL trailing dan exit terjadi di profit."""

    def test_trailing_closes_position_in_profit(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True, tp2=True)}
        crypto_loop['price'] = 101.5         # di bawah SL trailing (TP1 = 102)
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert crypto_loop['outcomes'] == [('CRYPTO_BTC-USD_1', 'trailing')]
        assert len(app.bot.sent) == 1
        assert 'TRAILING' in app.bot.sent[0][1]

    def test_trailing_exit_is_not_labelled_breakeven(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True, tp2=True)}
        crypto_loop['price'] = 101.5
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert 'STOP DI ENTRY' not in app.bot.sent[0][1]

    def test_without_flag_no_trailing_exit(self, crypto_loop, monkeypatch):
        from handlers.scheduler import jobs_crypto
        from services.signal_service import set_features

        saved = set_features(exit_mgmt=False)
        try:
            crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True, tp2=True)}
            crypto_loop['price'] = 101.5
            app = FakeApp()

            asyncio.run(jobs_crypto.check_crypto_tp_sl(app))
        finally:
            set_features(**saved)

        assert crypto_loop['outcomes'] == []
        assert app.bot.sent == []

    def test_trailing_sl_never_moves_down(self, crypto_loop):
        """SL trailing yang sudah naik tidak boleh turun lagi.

        Kalau SL ikut turun saat harga berbalik, profit yang sudah
        terkunci hilang - ini persis kebalikan dari tujuan trailing stop.
        """
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True, tp2=True)}

        # Siklus 1: harga 106 -> trailing = 106 - 1.5*ATR = 104.5
        crypto_loop['price'] = 106.0
        asyncio.run(jobs_crypto.check_crypto_tp_sl(FakeApp()))
        assert crypto_loop['outcomes'] == []
        assert crypto_loop['signals']['CRYPTO_BTC-USD_1']['sl_lock'] == 104.5

        # Siklus 2: harga turun ke 103. SL harus tetap di 104.5, jadi
        # posisi ditutup di profit. Tanpa lock, SL turun ke 102 dan
        # posisi menggantung.
        crypto_loop['price'] = 103.0
        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert crypto_loop['outcomes'] == [('CRYPTO_BTC-USD_1', 'trailing')]
        assert 'TRAILING' in app.bot.sent[0][1]


class TestSlLockPersistence:
    """Level SL yang sudah digeser harus bertahan setelah restart."""

    def test_sl_lock_is_restored_from_db(self, temp_db):
        db, _ = temp_db
        key = 'CRYPTO_TESTSL_1'
        db.save_active_signal(
            key=key, ticker='TESTSL', asset_type='crypto',
            signal_type='BUY', price=100.0, tp1=102.0, tp2=104.0, tp3=107.0,
            sl=97.0, score=70, quality='STRONG', reason='test',
            extra_data={'direction': 'LONG', 'atr': 1.0, 'user_id': '1',
                        'timeframe': '60', 'sl_lock': 104.5},
            name='Test SL')

        restored = db.load_active_signals()
        assert key in restored, "sinyal aktif harus dimuat ulang"
        assert restored[key]['sl_lock'] == 104.5, \
            "SL trailing yang sudah dikunci harus dipulihkan"


class TestTrailingWinCount:
    """Trailing dihitung sebagai win hanya kalau benar-benar tutup profit.

    Label 'trailing' menandai exit-nya SL yang bergerak. Tapi harga bisa
    melompat melewati level SL, jadi exit-nya tidak selalu di sisi profit.
    Kalau tidak dicek, win rate ikut dipangar.
    """

    @staticmethod
    def _add(db, key, entry, closed, direction='LONG'):
        db.save_active_signal(
            key=key, ticker='TESTW', asset_type='crypto',
            signal_type='BUY' if direction == 'LONG' else 'SELL',
            price=entry, tp1=entry * 1.02, tp2=entry * 1.04, tp3=entry * 1.07,
            sl=entry * 0.97, score=70, quality='STRONG', reason='test',
            extra_data={'direction': direction, 'atr': entry * 0.01,
                        'user_id': '1', 'timeframe': '60'},
            name='Test Win')
        db.save_signal_outcome(key, 'trailing', closed_price=closed)

    def test_trailing_in_profit_is_a_win(self, temp_db):
        db, _ = temp_db
        self._add(db, 'CRYPTO_W1', 100.0, 104.0, 'LONG')

        stats = db.get_signal_stats('crypto')

        assert stats['trailing_count'] == 1
        assert stats['tp_rate'] == 100.0

    def test_trailing_below_entry_is_not_a_win(self, temp_db):
        db, _ = temp_db
        # Harga melompat ke bawah entry - exit-nya loss, meski exit-nya
        # dipicu trailing stop.
        self._add(db, 'CRYPTO_W2', 100.0, 96.0, 'LONG')

        stats = db.get_signal_stats('crypto')

        assert stats['trailing_count'] == 1
        assert stats['tp_rate'] == 0.0

    def test_short_trailing_in_profit_is_a_win(self, temp_db):
        db, _ = temp_db
        self._add(db, 'CRYPTO_W3', 100.0, 96.0, 'SHORT')

        stats = db.get_signal_stats('crypto')

        assert stats['trailing_count'] == 1
        assert stats['tp_rate'] == 100.0

    def test_beakeven_is_not_counted_as_a_win(self, temp_db):
        db, _ = temp_db
        db.save_active_signal(
            key='CRYPTO_W4', ticker='TESTW', asset_type='crypto',
            signal_type='BUY', price=100.0, tp1=102.0, tp2=104.0, tp3=107.0,
            sl=97.0, score=70, quality='STRONG', reason='test',
            extra_data={'direction': 'LONG', 'atr': 1.0, 'user_id': '1',
                        'timeframe': '60'},
            name='Test Win')
        db.save_signal_outcome('CRYPTO_W4', 'beakeven', closed_price=100.0)

        stats = db.get_signal_stats('crypto')

        assert stats['beakeven_count'] == 1
        assert stats['tp_rate'] == 0.0




class TestExitNotificationPL:
    """Pesan exit harus menampilkan P/L yang benar-benar terjadi.

    Sebelumnya pesan exit hanya menampilkan rentang rencana SL->TP3
    ("Gain: -3.0% -> +7.0%"), jadi user tidak pernah tahu hasil
    akhirnya - padahal bisa loss meski exit-nya triggered breakeven
    atau trailing.
    """

    def test_breakeven_exit_shows_realized_pl(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True)}
        crypto_loop['price'] = 99.5          # entry 100 -> -0.5%
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert 'P/L: -0.50%' in app.bot.sent[0][1]

    def test_trailing_exit_shows_profit_pl(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True, tp2=True)}
        crypto_loop['price'] = 101.5         # entry 100 -> +1.5%
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert 'P/L: +1.50%' in app.bot.sent[0][1]

    def test_exit_message_drops_plan_range(self, crypto_loop):
        """Rencana SL->TP3 tidak relevan lagi setelah posisi tutup."""
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(tp1=True)}
        crypto_loop['price'] = 99.5
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert 'Gain:' not in app.bot.sent[0][1]


class TestShortBreakeven:
    """Sinyal SHORT: breakeven juga berlaku (SL turun ke entry)."""

    def test_short_closed_at_entry(self, crypto_loop):
        from handlers.scheduler import jobs_crypto

        crypto_loop['signals'] = {'CRYPTO_BTC-USD_1': _signal(
            tp1=True, direction='SHORT', signal_type='SELL', entry=100.0, atr=1.0)}
        crypto_loop['price'] = 100.5         # di atas entry, di bawah SL lama (103)
        app = FakeApp()

        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert crypto_loop['outcomes'] == [('CRYPTO_BTC-USD_1', 'beakeven')]
        assert 'STOP DI ENTRY' in app.bot.sent[0][1]
