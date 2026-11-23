"""Test notifikasi crypto per arah (LONG / SHORT).

Notifikasi crypto dipecah per arah:
- notif_crypto_long  -> rekomendasi BELI (sinyal LONG / REVERSAL)
- notif_crypto_short -> rekomendasi JUAL (sinyal SHORT)

Yang dijaga di sini:
1. User hanya dapat notifikasi untuk arah yang dia aktifkan.
2. Posisi yang arahnya tidak diaktifkan ditutup tanpa notifikasi, jadi
   tidak ada notifikasi exit (SL / breakeven / trailing) untuk posisi
   yang user sudah tidak mau dilihat.
3. User lama yang punya notif_crypto=1 tetap dapat rekomendasi beli.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=WIB)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class FakeJobQueue:
    def run_once(self, target, when=0):
        pass


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()


class FakeSignalService:
    """Engine palsu: arah sinyal ditentukan per ticker."""

    def __init__(self, direction_by_ticker):
        self.direction_by_ticker = direction_by_ticker

    def generate_crypto_signal(self, d):
        direction = self.direction_by_ticker.get(d['ticker'], 'BUY')
        return {
            'signal': direction,
            'entry': d['price'],
            'buy_score': 80,
            'sell_score': 80,
            'quality': 'STRONG',
            'reason': 'test',
            'macd_hist': 0.5,
            'is_reversal': False,
        }


def make_data(ticker, price=100.0):
    """Payload crypto seperti hasil get_crypto_data*()."""
    return {
        'ticker': ticker,
        'price': price,
        'range_24h': 3.0,
        'candles': 250,
        'atr': price * 0.02,
        'rsi': 55.0,
        'ma_fast': price * 1.01,
        'ma_slow': price * 0.99,
        'volume_ratio': 1.2,
        'adx': 30.0,
        'stoch_k': 55.0,
        'change': 1.0,
        'sr': {},
        'support': price * 0.95,
        'resistance': price * 1.05,
        'bb_middle': price,
        'bb_upper': price * 1.01,
        'bb_lower': price * 0.99,
        'htf_trend': 'up',
        'source': 'yahoo',
    }


class FakeCryptoService:
    crypto_pairs = {'LONGCOIN-USD': 'Longcoin', 'SHORTCOIN-USD': 'Shortcoin'}
    used_fallback_pairs = False

    def get_usd_idr_rate(self):
        return 16000

    def refresh_price_reference(self, priority=False):
        return {}

    def get_crypto_data(self, ticker, interval, period,
                        skip_if_missing=False, priority=False):
        return make_data(ticker)

    def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                       use_cache=True, priority=False):
        return {t: make_data(t) for t in tickers}


@pytest.fixture
def scan(monkeypatch):
    """Scan crypto dengan dua ticker: satu sinyal BUY, satu SELL."""
    from handlers.scheduler import jobs_crypto

    state = {'saved': [], 'signals': {}}

    monkeypatch.setattr(jobs_crypto, 'now_wib', lambda: NOW)
    monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeCryptoService())
    monkeypatch.setattr(jobs_crypto, 'signal_service', FakeSignalService({
        'LONGCOIN-USD': 'BUY',
        'SHORTCOIN-USD': 'SELL',
    }))
    # Data segar saat mengirim: pakai data palsu, bukan provider sungguhan.
    monkeypatch.setattr(
        jobs_crypto, 'get_crypto_data_with_fallback',
        lambda ticker, interval='1h', period='1d': (make_data(ticker), False))
    monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_crypto, '_schedule_followup_scan',
                        lambda *a, **k: None)
    monkeypatch.setattr(jobs_crypto.db, 'get_all_signals', lambda: {})
    monkeypatch.setattr(jobs_crypto.db, 'save_active_signal',
                        lambda **kwargs: state['saved'].append(kwargs))
    jobs_crypto._last_scan_by_tf.clear()
    jobs_crypto._force_next_scan = False
    return state


def _run_scan(user_flags):
    """Jalankan scan crypto untuk user dengan setelan tertentu."""
    from handlers.scheduler import jobs_crypto

    jobs_crypto._get_user_db = lambda: {'1': dict(user_flags, timeframe='60')}
    app = FakeApp()
    asyncio.run(jobs_crypto.check_crypto_signals(app))
    return app


class TestScanDirectionGating:
    """Sinyal hanya dikirim kalau arahnya diaktifkan user."""

    def test_long_blocked_when_long_disabled(self, scan):
        app = _run_scan({'notif_crypto_long': False, 'notif_crypto_short': True})
        texts = [t for _, t in app.bot.sent]
        assert not any('LONGCOIN' in t for t in texts), \
            'sinyal LONG terkirim padahal notifikasi long mati'

    def test_short_blocked_when_short_disabled(self, scan):
        app = _run_scan({'notif_crypto_long': True, 'notif_crypto_short': False})
        texts = [t for _, t in app.bot.sent]
        assert not any('SHORTCOIN' in t for t in texts), \
            'sinyal SHORT terkirim padahal notifikasi short mati'
        assert any('LONGCOIN' in t for t in texts), \
            'sinyal LONG ikut hilang - filter short jangan sampai kena long'

    def test_both_sent_when_both_enabled(self, scan):
        app = _run_scan({'notif_crypto_long': True, 'notif_crypto_short': True})
        texts = [t for _, t in app.bot.sent]
        assert any('LONGCOIN' in t for t in texts)
        assert any('SHORTCOIN' in t for t in texts)

    def test_blocked_signal_not_saved_to_db(self, scan):
        """Sinyal yang diblokir tidak boleh jadi posisi aktif - kalau
        disimpan, user tetap dapat notifikasi exit-nya nanti."""
        _run_scan({'notif_crypto_long': True, 'notif_crypto_short': False})
        saved = [s['ticker'] for s in scan['saved']]
        assert 'SHORTCOIN-USD' not in saved
        assert 'LONGCOIN-USD' in saved


def make_signal(**overrides):
    """Sinyal crypto yang sudah dilacak (posisi aktif)."""
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
def tp_sl_loop(monkeypatch):
    """Loop TP/SL crypto dengan harga & DB palsu."""
    from handlers.scheduler import jobs_crypto

    state = {'signals': {}, 'outcomes': [], 'removed': [], 'price': 103.0}

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
    monkeypatch.setattr(
        jobs_crypto.crypto_service, 'get_usd_idr_rate', lambda: 16000)
    monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome', fake_save_outcome)
    monkeypatch.setattr(jobs_crypto.db, 'update_signal_extra',
                        lambda key, extra: True)
    monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_crypto, '_remove_signal',
                        lambda key: state['removed'].append(key))
    return state


def _run_tp_sl(user_flags):
    from handlers.scheduler import jobs_crypto

    jobs_crypto._get_user_db = lambda: {'1': dict(user_flags, timeframe='60')}
    app = FakeApp()
    asyncio.run(jobs_crypto.check_crypto_tp_sl(app))
    return app


class TestTpSlDirectionGating:
    """Posisi yang arahnya tidak diaktifkan ditutup tanpa notifikasi."""

    def test_short_position_closed_silently(self, tp_sl_loop):
        tp_sl_loop['signals'] = {
            'CRYPTO_BTC-USD_1': make_signal(direction='SHORT',
                                             signal_type='SELL')}

        app = _run_tp_sl({'notif_crypto_long': True,
                          'notif_crypto_short': False})

        assert not app.bot.sent, 'notifikasi exit terkirim padahal short mati'
        assert tp_sl_loop['outcomes'], 'posisi tidak ditutup'
        assert tp_sl_loop['removed'] == ['CRYPTO_BTC-USD_1']

    def test_long_position_closed_silently(self, tp_sl_loop):
        tp_sl_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}

        app = _run_tp_sl({'notif_crypto_long': False,
                          'notif_crypto_short': True})

        assert not app.bot.sent, 'notifikasi exit terkirim padahal long mati'
        assert tp_sl_loop['removed'] == ['CRYPTO_BTC-USD_1']

    def test_position_still_notified_when_enabled(self, tp_sl_loop):
        tp_sl_loop['signals'] = {'CRYPTO_BTC-USD_1': make_signal()}

        app = _run_tp_sl({'notif_crypto_long': True,
                          'notif_crypto_short': True})

        assert len(app.bot.sent) == 1
        assert 'TP1' in app.bot.sent[0][1]
        assert not tp_sl_loop['removed']


class TestMigrationInheritsLong:
    """User lama: notif_crypto=1 jadi notif_crypto_long=1, short tetap 0."""

    def test_existing_user_keeps_long_signals(self):
        from db import Database

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.db')

            # DB versi lama: belum ada kolom per arah.
            conn = sqlite3.connect(path)
            conn.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    timeframe TEXT DEFAULT '5',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    notif_saham INTEGER DEFAULT 1,
                    notif_crypto INTEGER DEFAULT 0,
                    notif_bsjp INTEGER DEFAULT 0,
                    notif_morning INTEGER DEFAULT 0,
                    notif_alert_favorit INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "INSERT INTO users (user_id, notif_crypto) VALUES (1, 1)")
            conn.execute(
                "INSERT INTO users (user_id, notif_crypto) VALUES (2, 0)")
            conn.commit()
            conn.close()

            database = Database(db_path=path)
            database.initialize()

            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            rows = {r['user_id']: dict(r)
                    for r in conn.execute("SELECT * FROM users").fetchall()}
            conn.close()

            # User lama yang punya notif_crypto=1 tetap dapat rekomendasi beli.
            assert rows[1]['notif_crypto_long'] == 1
            # Rekomendasi jual harus opt-in.
            assert rows[1]['notif_crypto_short'] == 0
            # User yang tidak punya notifikasi crypto tetap tidak punya.
            assert rows[2]['notif_crypto_long'] == 0
            assert rows[2]['notif_crypto_short'] == 0

            # Windows tidak bisa menghapus file yang masih dibuka.
            database.close()

    def test_migration_is_not_repeated(self):
        """Migrasi data harus jalan sekali. Kalau diulang, user yang
        mematikan notifikasi long akan dinyalakan lagi saat restart."""
        from db import Database

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.db')
            database = Database(db_path=path)
            database.initialize()

            conn = sqlite3.connect(path)
            conn.execute(
                "INSERT INTO users (user_id, notif_crypto, "
                "notif_crypto_long) VALUES (1, 1, 0)")
            conn.commit()
            conn.close()

            # Restart: migrasi jalan lagi, tapi kolom sudah ada.
            database2 = Database(db_path=path)
            database2.initialize()

            conn = sqlite3.connect(path)
            value = conn.execute(
                "SELECT notif_crypto_long FROM users WHERE user_id = 1"
            ).fetchone()[0]
            conn.close()

            assert value == 0, 'notifikasi long dinyalakan lagi sendiri'

            database.close()
            database2.close()


class TestNotificationMenu:
    """Menu notifikasi harus punya tombol untuk kedua arah."""

    def test_menu_has_both_direction_toggles(self):
        import inspect
        from handlers.commands import notifications

        source = inspect.getsource(notifications)
        assert 'notif_crypto_long' in source
        assert 'notif_crypto_short' in source
