"""
Test anti-duplicate notifikasi sinyal beli.

Satu aset yang skornya tinggi lolos scan tiap 15 menit. Kalau tidak ada
filter, user menerima "beli lagi" untuk ticker yang sama berulang-ulang -
satu aset bisa terkirim 20x dalam sehari (terbukti: BDX-USD terkirim 21x
dalam 25 jam).

Yang dijaga di sini:
1. Sinyal yang sama tidak dikirim dua kali ke user yang sama.
2. Filter bersifat per-user: sinyal user A tidak memblokir user B.
3. BUY dan SELL untuk ticker yang sama tidak saling memblokir.
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))

# 25 Okt 2026, 17:08 WIB (= 10:08 UTC)
NOW = datetime(2026, 10, 25, 17, 8, tzinfo=WIB)

# Rabu 21 Okt 2026, 10:00 WIB (= 03:00 UTC) - jam bursa, buat test saham
NOW_STOCK = datetime(2026, 10, 21, 10, 0, tzinfo=WIB)


class FakeBot:
    """Bot palsu: mencatat pesan."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return None


class FakeJobQueue:
    def run_once(self, target, when=0):
        pass


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.job_queue = FakeJobQueue()


def fake_crypto_data(price=100.0):
    return {
        'price': price,
        'candles': 50,
        'atr': 1.0,
        'rsi': 45.0,
        'ma_fast': 101.0,
        'ma_slow': 99.0,
        'volume_ratio': 1.2,
        'adx': 30.0,
        'stoch_k': 55.0,
        'change': 1.0,
        'sr': {},
        'support': 95.0,
        'resistance': 105.0,
    }


def fake_crypto_signal(signal='BUY', entry=100.0):
    return {
        'signal': signal,
        'entry': entry,
        'buy_score': 70,
        'sell_score': 60,
        'quality': 'STRONG',
        'reason': 'test',
        'macd_hist': 0.5,
        'is_reversal': False,
    }


def fake_stock_data(price=100.0):
    return {
        'price': price,
        'candles': 50,
        'atr': 1.5,
        'rsi': 45.0,
        'ma_fast': 101.0,
        'ma_slow': 99.0,
        'volume_ratio': 1.2,
        'adx': 30.0,
        'stoch_k': 55.0,
        'change': 1.0,
        'sr': {},
        'support': 95.0,
        'resistance': 105.0,
    }


def fake_stock_signal(signal='BUY', entry=100.0):
    return {
        'signal': signal,
        'entry': entry,
        'buy_score': 70,
        'sell_score': 60,
        'quality': 'STRONG',
        'reason': 'test',
        'macd_hist': 0.5,
        'is_reversal': False,
    }


def db_row(key, ticker, price, created_at, direction='LONG',
           timeframe='240', outcome='open'):
    """Bentuk baris tabel `signals` seperti yang dikembalikan db.get_all_signals()."""
    return {
        'key': key,
        'ticker': ticker,
        'asset_type': 'crypto',
        'signal_type': 'BUY' if direction == 'LONG' else 'SELL',
        'price': price,
        'outcome': outcome,
        'created_at': created_at,
        'extra_data': {'direction': direction, 'timeframe': timeframe},
    }


def active_signal(ticker, uid='1', timeframe='240', direction='LONG'):
    """Sinyal yang sedang aktif di state in-memory."""
    return {
        'name': 'Beldex',
        'entry': 100.0,
        'tp1': 102.0, 'tp2': 104.0, 'tp3': 107.0, 'sl': 97.0,
        'time': NOW,
        'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
        'type': 'crypto',
        'direction': direction,
        'ticker_raw': ticker,
        'signal_type': 'BUY',
        'user_id': uid,
        'timeframe': timeframe,
    }


@pytest.fixture
def crypto_scan(monkeypatch):
    """Scan sinyal crypto dengan data & DB palsu."""
    from handlers.scheduler import jobs_crypto

    state = {
        'signals': {},    # last_buy_signals
        'db_rows': {},    # baris tabel signals
        'saved': [],
        'price': 100.0,
        'signal': 'BUY',
    }

    class FakeCryptoService:
        crypto_pairs = {'BDX-USD': 'Beldex'}

        def get_usd_idr_rate(self):
            return 16000

    class FakeSignalService:
        def generate_crypto_signal(self, d):
            return fake_crypto_signal(state['signal'], d['price'])

    def fake_save_active_signal(**kwargs):
        state['saved'].append(kwargs)
        # Baris DB ditulis setelah pesan terkirim - sama seperti produksi.
        state['db_rows'][kwargs['key']] = db_row(
            kwargs['key'], kwargs['ticker'], kwargs['price'],
            '2026-10-25 10:08:00',
            direction=kwargs.get('extra_data', {}).get('direction', 'LONG'),
            timeframe=kwargs.get('extra_data', {}).get('timeframe', '240'),
        )
        return True

    monkeypatch.setattr(jobs_crypto, 'now_wib', lambda: NOW)
    monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeCryptoService())
    monkeypatch.setattr(jobs_crypto, 'signal_service', FakeSignalService())
    monkeypatch.setattr(
        jobs_crypto, 'get_crypto_data_with_fallback',
        lambda ticker, interval, period: (fake_crypto_data(state['price']), False))
    monkeypatch.setattr(jobs_crypto.crypto_service, 'get_usd_idr_rate',
                        lambda: 16000, raising=False)
    monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
        '1': {'notif_crypto': True, 'timeframe': '240'}})
    monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                        lambda: state['signals'])
    monkeypatch.setattr(jobs_crypto, '_schedule_followup_scan',
                        lambda *a, **k: None)
    monkeypatch.setattr(jobs_crypto.db, 'get_all_signals',
                        lambda: state['db_rows'])
    monkeypatch.setattr(jobs_crypto.db, 'save_active_signal',
                        fake_save_active_signal)
    return state


class TestCryptoNoDuplicate:
    """Sinyal crypto tidak boleh dikirim berulang ke user yang sama."""

    def test_first_scan_sends_signal(self, crypto_scan):
        """Scan pertama tetap mengirim sinyal."""
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 1
        assert 'BDX' in app.bot.sent[0][1]

    def test_second_scan_does_not_resend_same_ticker(self, crypto_scan):
        """Scan berikutnya tidak boleh mengirim ticker yang sama lagi.

        Ini inti masalahnya: setiap scan 15 menit mengirim ulang "beli BDX"
        padahal sinyalnya masih dipantau.
        """
        from handlers.scheduler import jobs_crypto

        first = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(first))
        assert len(first.bot.sent) == 1

        second = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(second))
        assert len(second.bot.sent) == 0

    def test_active_signal_blocks_resend_without_db_row(self, crypto_scan):
        """Posisi yang masih terbuka memblokir, walau baris DB belum ada."""
        from handlers.scheduler import jobs_crypto

        crypto_scan['signals']['CRYPTO_BDX-USD_1'] = active_signal('BDX-USD')

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 0

    def test_closed_signal_still_blocks_within_cooldown(self, crypto_scan):
        """Sinyal yang sudah ditutup (SL/TP3) tetap di-block selama cooldown.

        Kalau tidak, user dapat "beli lagi" 5 menit setelah SL.
        """
        from handlers.scheduler import jobs_crypto

        crypto_scan['db_rows']['CRYPTO_BDX-USD_1'] = db_row(
            'CRYPTO_BDX-USD_1', 'BDX-USD', 100.0,
            '2026-10-25 09:00:00', outcome='sl')

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 0

    def test_other_direction_is_not_blocked(self, crypto_scan):
        """Sinyal SELL tidak diblokir oleh sinyal BUY untuk ticker yang sama."""
        from handlers.scheduler import jobs_crypto

        crypto_scan['db_rows']['CRYPTO_BDX-USD_1'] = db_row(
            'CRYPTO_BDX-USD_1', 'BDX-USD', 100.0,
            '2026-10-25 09:00:00', direction='SHORT')

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 1

    def test_other_user_is_not_blocked(self, crypto_scan, monkeypatch):
        """Sinyal user lain tidak memblokir user ini."""
        from handlers.scheduler import jobs_crypto

        crypto_scan['signals']['CRYPTO_BDX-USD_99'] = active_signal(
            'BDX-USD', uid='99')

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 1


class TestStockNoDuplicate:
    """Sinyal saham punya aturan yang sama."""

    @pytest.fixture
    def stock_scan(self, monkeypatch):
        from handlers.scheduler import jobs_stocks
        from services import signal_service as signal_module
        from services import ticker_health

        state = {
            'signals': {},
            'db_rows': {},
            'saved': [],
            'price': 100.0,
        }

        class FakeSignalService:
            def generate_stock_signal(self, d):
                return fake_stock_signal('BUY', d['price'])

        def fake_save_active_signal(**kwargs):
            state['saved'].append(kwargs)
            state['db_rows'][kwargs['key']] = db_row(
                kwargs['key'], kwargs['ticker'], kwargs['price'],
                '2026-10-21 03:08:00',
                direction=kwargs.get('extra_data', {}).get('direction', 'LONG'),
                timeframe=kwargs.get('extra_data', {}).get('timeframe', '240'),
            )
            return True

        monkeypatch.setattr(jobs_stocks, 'now_wib', lambda: NOW_STOCK)
        monkeypatch.setattr(jobs_stocks, 'ALL_STOCKS', {'AAA': 'Aaa'})
        monkeypatch.setattr(jobs_stocks, '_yahoo_stock_cooldown', 0)
        monkeypatch.setattr(ticker_health, 'is_unavailable', lambda t: False)
        monkeypatch.setattr(
            jobs_stocks, 'get_stock_data_with_fallback',
            lambda ticker, interval, period: (fake_stock_data(state['price']), False))
        monkeypatch.setattr(signal_module.signal_service, 'generate_stock_signal',
                            lambda d: fake_stock_signal('BUY', d['price']))
        monkeypatch.setattr(jobs_stocks, '_get_user_db', lambda: {
            '1': {'notif_saham': True, 'timeframe': '240'}})
        monkeypatch.setattr(jobs_stocks, '_get_last_buy_signals',
                            lambda: state['signals'])
        monkeypatch.setattr(jobs_stocks, '_schedule_followup_scan',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_stocks.db, 'get_all_signals',
                            lambda: state['db_rows'])
        monkeypatch.setattr(jobs_stocks.db, 'save_active_signal',
                            fake_save_active_signal)
        return state

    def test_second_scan_does_not_resend_same_ticker(self, stock_scan, monkeypatch):
        """Scan saham berikutnya tidak mengirim ticker yang sama lagi."""
        from handlers.scheduler import jobs_stocks

        first = FakeApp()
        asyncio.run(jobs_stocks.check_stock_signals(first))
        assert len(first.bot.sent) == 1

        # Scan pertama bisa menyalakan cooldown Yahoo (kalau ticker-nya cuma
        # sedikit, ambang timeout-nya jadi 0). Reset supaya scan kedua
        # benar-benar jalan dan yang diuji adalah filter duplikatnya.
        monkeypatch.setattr(jobs_stocks, '_yahoo_stock_cooldown', 0)

        second = FakeApp()
        asyncio.run(jobs_stocks.check_stock_signals(second))
        assert len(second.bot.sent) == 0


class TestCooldownHelpers:
    """Unit test untuk helper cooldown."""

    def test_find_user_signal_matches_by_content_not_key(self):
        """Sinyal dicari lewat isinya, bukan lewat kunci dict.

        Kunci penyimpanan `CRYPTO_{ticker}_{uid}` != kunci lookup lama
        `CRYPTO_{ticker}_{tf}_{arah}` - itu penyebab notifikasi dobel.
        """
        from handlers.scheduler._common import find_user_signal

        signals = {'CRYPTO_BDX-USD_1': active_signal('BDX-USD')}

        found = find_user_signal(signals, '1', 'BDX-USD', '240', 'LONG')
        assert found is not None
        assert found['ticker_raw'] == 'BDX-USD'

        # timeframe, arah, dan user yang beda tidak boleh ketemu
        assert find_user_signal(signals, '1', 'BDX-USD', '60', 'LONG') is None
        assert find_user_signal(signals, '1', 'BDX-USD', '240', 'SHORT') is None
        assert find_user_signal(signals, '2', 'BDX-USD', '240', 'LONG') is None
        assert find_user_signal(signals, '1', 'OTHER-USD', '240', 'LONG') is None

    def test_no_row_means_no_cooldown(self):
        """Ticker yang belum pernah dikirim tidak di-cooldown."""
        from handlers.scheduler._common import signal_in_cooldown

        assert signal_in_cooldown(
            NOW, {}, 'CRYPTO_BDX-USD_1', '240', 'LONG', 100.0) is False

    def test_recent_signal_is_in_cooldown(self):
        """Sinyal yang dikirim 8 menit lalu masih dalam cooldown."""
        from handlers.scheduler._common import signal_in_cooldown

        rows = {'CRYPTO_BDX-USD_1': db_row(
            'CRYPTO_BDX-USD_1', 'BDX-USD', 100.0, '2026-10-25 10:00:00')}

        assert signal_in_cooldown(
            NOW, rows, 'CRYPTO_BDX-USD_1', '240', 'LONG', 100.0) is True

    def test_old_signal_without_price_move_stays_blocked(self):
        """Lebih dari 24 jam, tapi harga tidak bergerak -> tetap diblokir."""
        from handlers.scheduler._common import signal_in_cooldown

        rows = {'CRYPTO_BDX-USD_1': db_row(
            'CRYPTO_BDX-USD_1', 'BDX-USD', 100.0, '2026-10-23 10:00:00')}

        assert signal_in_cooldown(
            NOW, rows, 'CRYPTO_BDX-USD_1', '240', 'LONG', 101.0) is True

    def test_old_signal_with_price_move_is_allowed(self):
        """Lebih dari 24 jam DAN harga bergerak > 5% -> boleh kirim lagi."""
        from handlers.scheduler._common import signal_in_cooldown

        rows = {'CRYPTO_BDX-USD_1': db_row(
            'CRYPTO_BDX-USD_1', 'BDX-USD', 100.0, '2026-10-23 10:00:00')}

        assert signal_in_cooldown(
            NOW, rows, 'CRYPTO_BDX-USD_1', '240', 'LONG', 110.0) is False

    def test_other_timeframe_is_not_blocked(self):
        """Sinyal timeframe lain tidak memblokir timeframe ini."""
        from handlers.scheduler._common import signal_in_cooldown

        rows = {'CRYPTO_BDX-USD_1': db_row(
            'CRYPTO_BDX-USD_1', 'BDX-USD', 100.0, '2026-10-25 10:00:00',
            timeframe='60')}

        assert signal_in_cooldown(
            NOW, rows, 'CRYPTO_BDX-USD_1', '240', 'LONG', 100.0) is False
