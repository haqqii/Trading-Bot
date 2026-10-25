"""
Test filter stablecoin.

Stablecoin = aset yang nilainya dipatok ke mata uang lain (biasanya $1).
Rekomendasi "beli" untuk stablecoin itu noise: volatilitasnya ~0, jadi tidak
ada peluang profit dan TP/SL-nya tidak pernah kena. Di produksi, 12 dari 54
sinyal crypto yang terkirim dalam sehari adalah stablecoin.

Yang dijaga di sini:
1. Stablecoin dikenali dan dilewati, jadi tidak ada sinyal/notifikasi.
2. Aset biasa yang kebetulan harganya ~$1 (BTSE $1.07, FIL $0.91, DOT $1.02)
   TIDAK ikut tersarang - syarat "hampir tidak bergerak" tidak terpenuhi.
3. Kalau metriknya tidak ada, aset tidak boleh asal tersaring.
4. Putusan tidak bergantung timeframe: metrik yang dipakai adalah rentang
   harga 24 jam, bukan lebar Bollinger band (yang ikut timeframe).

Angka di bawah diambil dari data produksi (25 Okt 2026).
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 25, 17, 8, tzinfo=WIB)


def make_data(price, range_24h, ticker=None, **extra):
    """Bentuk dict data crypto seperti hasil get_crypto_data*().

    `range_24h` = rentang harga 24 jam dalam persen (sudah ternormalisasi
    ke 24 jam, sama untuk semua timeframe).
    """
    data = {
        'price': price,
        'range_24h': range_24h,
        'candles': 50,
        'atr': price * 0.01,
        'rsi': 45.0,
        'ma_fast': price * 1.001,
        'ma_slow': price * 0.999,
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
    }
    if ticker:
        data['ticker'] = ticker
    data.update(extra)
    return data


# --- Data produksi (interval 4h, 25 Okt 2026) ---------------------------------
# (ticker, harga, rentang 24 jam %)
STABLECOIN_CASES = [
    ('USDC-USD', 0.9998, 0.0383),
    ('USDAI-USD', 0.9999, 0.0370),
    ('RLUSD-USD', 1.0000, 0.0417),
    ('DAI-USD', 0.9997, 0.0500),
    ('USDD-USD', 0.9989, 0.0800),
    ('USX-USD', 0.9998, 0.0600),
    ('CRVUSD-USD', 0.9991, 0.0500),
    ('USD0-USD', 0.9990, 0.0400),
    ('BFUSD-USD', 0.9990, 0.1394),
    ('GUSD-USD', 1.0004, 0.1200),
    ('USAT-USD', 0.9985, 0.1600),
    ('EURCV-USD', 1.1197, 0.1684),   # dipatok ke EUR
    ('EURC-USD', 1.1205, 0.2331),    # dipatok ke EUR
]

NORMAL_CASES = [
    ('BTSE-USD', 1.0727, 1.4954),    # harga ~$1 tapi bergerak normal
    ('MX-USD', 1.5662, 1.4651),
    ('DOT-USD', 1.0244, 4.1942),
    ('FIL-USD', 0.9136, 4.1189),
    ('PIEVERSE-USD', 1.0184, 5.3769),
    ('BTC-USD', 80310.0, 1.8049),
    ('ETH-USD', 2328.0, 2.0000),
    ('XRP-USD', 1.2943, 2.0000),
    ('OZO-USD', 0.1308, 0.2213),     # token murah yang datar - bukan stablecoin
]


class TestRange24h:
    """Perhitungan rentang harga 24 jam."""

    @staticmethod
    def _df(rows):
        """Frame OHLC sederhana: rows = [(high, low), ...]"""
        import pandas as pd
        return pd.DataFrame(
            {'High': [r[0] for r in rows], 'Low': [r[1] for r in rows]})

    def test_full_24h_window_is_not_scaled(self):
        """Window 24 jam penuh -> tidak ada penskalaan."""
        from services.crypto_service import compute_range_24h

        df = self._df([(101, 99)] * 24)
        result = compute_range_24h(df, 100.0, '1h')

        assert result == pytest.approx(2.0, rel=0.01)

    def test_short_window_is_normalized_to_24h(self):
        """Window shorter (Yahoo 1m cuma ~8 jam) diskalakan ke 24 jam."""
        import math
        from services.crypto_service import compute_range_24h

        # 8 bar 1 menit = 0.133 jam
        df = self._df([(100.5, 99.5)] * 8)
        result = compute_range_24h(df, 100.0, '1m')

        expected = 1.0 / math.sqrt((8 / 60) / 24.0)
        assert result == pytest.approx(expected, rel=0.01)

    def test_flat_asset_has_zero_range(self):
        """Aset yang tidak bergerak -> rentang 0."""
        from services.crypto_service import compute_range_24h

        df = self._df([(100.0, 100.0)] * 24)
        assert compute_range_24h(df, 100.0, '1h') == pytest.approx(0.0)

    def test_missing_data_returns_none(self):
        """Data tidak lengkap -> None, bukan error."""
        from services.crypto_service import compute_range_24h, compute_range_pct

        assert compute_range_24h(None, 100.0, '1h') is None
        assert compute_range_24h(self._df([(100, 99)]), 0, '1h') is None
        assert compute_range_pct(101, 99, 100, 0) is None
        assert compute_range_pct(101, 99, 0, 24) is None


class TestIsStablecoin:
    """Unit test untuk deteksi stablecoin."""

    @pytest.mark.parametrize('ticker,price,range_24h', STABLECOIN_CASES)
    def test_stablecoin_is_detected(self, ticker, price, range_24h):
        from services.crypto_service import is_stablecoin

        data = make_data(price, range_24h, ticker=ticker)
        assert is_stablecoin(data) is True, \
            f'{ticker} harus terdeteksi sebagai stablecoin'

    @pytest.mark.parametrize('ticker,price,range_24h', NORMAL_CASES)
    def test_normal_coin_is_kept(self, ticker, price, range_24h):
        from services.crypto_service import is_stablecoin

        data = make_data(price, range_24h, ticker=ticker)
        assert is_stablecoin(data) is False, \
            f'{ticker} bukan stablecoin dan harus tetap lolos'

    def test_non_usd_peg_is_detected(self):
        """Stablecoin yang dipatok ke yen: harganya $0.006, di luar band.

        JPYC bergerak mengikuti kurs (rentang 24 jam 0.99%), jadi tidak bisa
        dideteksi dari pergerakan - harus dari daftar ticker.
        """
        from services.crypto_service import is_stablecoin

        data = make_data(0.0088, 0.9943, ticker='JPYC-USD')
        assert is_stablecoin(data) is True

    def test_depeg_is_still_stablecoin(self):
        """Stablecoin yang lepas dari peg (0.85) tetap ikut tersaring."""
        from services.crypto_service import is_stablecoin

        assert is_stablecoin(make_data(0.85, 0.30, ticker='USDC-USD')) is True

    def test_missing_metric_is_not_filtered(self):
        """Metrik tidak ada -> jangan asal menyaring."""
        from services.crypto_service import is_stablecoin

        data = make_data(1.0, 0.05, ticker='USDC-USD')
        del data['range_24h']

        assert is_stablecoin(data) is False

    def test_broken_metric_is_not_filtered(self):
        """Nilai metrik rusak -> jangan asal menyaring."""
        from services.crypto_service import is_stablecoin

        data = make_data(1.0, 0.05, ticker='USDC-USD')
        data['range_24h'] = 'bukan angka'
        assert is_stablecoin(data) is False

        data = make_data(1.0, 0.05, ticker='USDC-USD')
        data['range_24h'] = None
        assert is_stablecoin(data) is False

    def test_zero_price_is_not_filtered(self):
        """Harga 0 (data gagal) bukan stablecoin."""
        from services.crypto_service import is_stablecoin

        assert is_stablecoin(make_data(0, 0.05, ticker='X-USD')) is False

    def test_verdict_does_not_depend_on_bollinger_width(self):
        """Lebar Bollinger band tidak boleh jadi penentu.

        Di jalur CoinGecko band dihitung dari sparkline 7 hari, jadi lebarnya
        tidak sebanding dengan data Yahoo. Yang dipakai hanya rentang 24 jam.
        """
        from services.crypto_service import is_stablecoin

        data = make_data(1.0, 0.05, ticker='USDC-USD')
        data['bb_upper'] = 1.10
        data['bb_lower'] = 0.90
        assert is_stablecoin(data) is True

        data = make_data(1.07, 1.49, ticker='BTSE-USD')
        data['bb_upper'] = 1.071
        data['bb_lower'] = 1.069
        assert is_stablecoin(data) is False


class FakeBot:
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


class TestScanSkipsStablecoin:
    """Scan otomatis tidak boleh mengirim sinyal untuk stablecoin."""

    @pytest.fixture
    def scan(self, monkeypatch):
        from handlers.scheduler import jobs_crypto

        state = {'signals': {}, 'db_rows': {}, 'saved': []}

        class FakeCryptoService:
            crypto_pairs = {'USDC-USD': 'USDC', 'FIL-USD': 'Filecoin'}
            used_fallback_pairs = False

            def get_usd_idr_rate(self):
                return 16000

            def get_crypto_data(self, ticker, interval, period,
                                skip_if_missing=False, priority=False):
                return fake_data(ticker, interval, period)[0]

            def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                               use_cache=True):
                return {}

        class FakeSignalService:
            def generate_crypto_signal(self, d):
                return {
                    'signal': 'BUY',
                    'entry': d['price'],
                    'buy_score': 80,
                    'sell_score': 40,
                    'quality': 'STRONG',
                    'reason': 'test',
                    'macd_hist': 0.5,
                    'is_reversal': False,
                }

        def fake_save_active_signal(**kwargs):
            state['saved'].append(kwargs)
            return True

        def fake_data(ticker, interval, period):
            if ticker == 'USDC-USD':
                return make_data(1.0, 0.04, ticker=ticker), False
            return make_data(0.91, 4.11, ticker=ticker), False

        monkeypatch.setattr(jobs_crypto, 'now_wib', lambda: NOW)
        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeCryptoService())
        monkeypatch.setattr(jobs_crypto, 'signal_service', FakeSignalService())
        monkeypatch.setattr(jobs_crypto, 'get_crypto_data_with_fallback', fake_data)
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

    def test_stablecoin_signal_is_not_sent(self, scan):
        """Hanya aset biasa yang dikirim, stablecoin dilewati."""
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 1, 'hanya boleh 1 sinyal (aset biasa)'
        assert 'FIL' in app.bot.sent[0][1]
        assert 'USDC' not in app.bot.sent[0][1]

    def test_stablecoin_never_reaches_db(self, scan):
        """Sinyal stablecoin tidak boleh ditulis ke tabel signals."""
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        saved_tickers = [s['ticker'] for s in scan['saved']]
        assert 'USDC-USD' not in saved_tickers


class TestTpSlClosesStablecoin:
    """Sinyal stablecoin yang sudah terlanjur dibuat ditutup tanpa notifikasi."""

    @pytest.fixture
    def tp_sl_loop(self, monkeypatch):
        from handlers.scheduler import jobs_crypto

        state = {'signals': {}, 'outcomes': []}

        def fake_fetch(ticker, interval, period, skip_if_missing=False,
                         priority=False):
            # USDC: harga dipatok $1, tidak bergerak
            return make_data(1.0, 0.04, ticker=ticker)

        def fake_save_outcome(key, outcome, **kwargs):
            state['outcomes'].append((key, outcome))

        # Job cek TP/SL memakai fetch_crypto_prices (Yahoo per ticker +
        # CoinGecko bulk untuk yang tidak ada di Yahoo).
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data', fake_fetch)
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_coingecko_bulk',
                            lambda tickers, retry=3, use_cache=True: {})
        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_usd_idr_rate', lambda: 16000)
        monkeypatch.setattr(jobs_crypto.db, 'save_signal_outcome',
                            fake_save_outcome)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: state['signals'])
        monkeypatch.setattr(jobs_crypto, '_remove_signal', lambda key: None)
        return state

    def test_stablecoin_signal_is_closed_without_notification(self, tp_sl_loop):
        """Sinyal stablecoin ditutup dengan outcome 'invalid', tanpa pesan."""
        from handlers.scheduler import jobs_crypto

        tp_sl_loop['signals'] = {'CRYPTO_USDC-USD_1': {
            'name': 'USDC',
            'entry': 1.0,
            'tp1': 1.02, 'tp2': 1.04, 'tp3': 1.07, 'sl': 0.98,
            'time': NOW,
            'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
            'type': 'crypto',
            'direction': 'LONG',
            'ticker_raw': 'USDC-USD',
            'buy_score': 70, 'sell_score': 0,
            'quality': 'STRONG',
            'signal_type': 'BUY',
            'is_reversal': False,
            'atr': 0.01,
            'user_id': '1',
            'timeframe': '60',
        }}

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_tp_sl(app))

        assert len(app.bot.sent) == 0, 'stablecoin tidak boleh Dikirim notifikasi TP/SL'
        assert ('CRYPTO_USDC-USD_1', 'invalid') in tp_sl_loop['outcomes']
        assert 'CRYPTO_USDC-USD_1' not in tp_sl_loop['signals']
