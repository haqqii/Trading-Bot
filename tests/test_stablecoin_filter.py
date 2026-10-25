"""
Test filter stablecoin.

Stablecoin = aset yang nilainya dipatok ke mata uang lain (biasanya $1).
Rekomendasi "beli" untuk stablecoin itu noise: volatilitasnya ~0, jadi tidak
ada peluang profit dan TP/SL-nya tidak pernah kena. Di produksi, 12 dari 54
sinyal crypto yang terkirim dalam sehari adalah stablecoin.

Yang dijaga di sini:
1. Stablecoin dikenali dan dilewati, jadi tidak ada sinyal/notifikasi.
2. Aset biasa yang kebetulan harganya ~$1 (BTSE $1.07, FIL $1.13) TIDAK ikut
   tersaring - syarat lebar Bollinger band tetap harus terpenuhi.
3. Kalau data Bollinger tidak ada, aset tidak boleh asal tersaring.

Angka di bawah diambil dari data produksi (interval 4h, 25 Okt 2026).
"""
import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 25, 17, 8, tzinfo=WIB)


def make_data(price, bb_width_pct, **extra):
    """Bentuk dict data crypto seperti hasil get_crypto_data*().

    `bb_width_pct` = lebar Bollinger band dalam persen dari bb_middle.
    """
    width = bb_width_pct / 100.0
    half = price * width / 2.0
    data = {
        'price': price,
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
        'bb_upper': price + half,
        'bb_lower': price - half,
    }
    data.update(extra)
    return data


# --- Data produksi -----------------------------------------------------------
# (ticker, harga, lebar band %) - diukur dari data live 4h/1mo.
STABLECOIN_CASES = [
    ('USDC-USD', 1.0000, 0.030),
    ('DAI-USD', 0.9998, 0.060),
    ('RLUSD-USD', 1.0001, 0.050),
    ('USDAI-USD', 1.0000, 0.100),
    ('USDD-USD', 0.9997, 0.120),
    ('BFUSD-USD', 1.0002, 0.050),
    ('GUSD-USD', 0.9985, 0.200),
    ('USX-USD', 0.9998, 0.054),     # jalur CoinGecko
    ('CRVUSD-USD', 0.9999, 0.100),
    ('EURCV-USD', 1.1207, 0.288),   # dipatok ke EUR, bukan USD
    ('USD0-USD', 1.0001, 0.070),
    ('USAT-USD', 1.0002, 0.160),
]

NORMAL_CASES = [
    ('BTSE-USD', 1.0712, 2.511),    # harga ~$1 tapi band-nya lebar
    ('FIL-USD', 1.1300, 11.500),    # harga ~$1, band sangat lebar
    ('DOT-USD', 3.4200, 19.492),
    ('BTC-USD', 80000.0, 2.776),
    ('ETH-USD', 2800.0, 4.886),
    ('OZO-USD', 0.1300, 0.200),     # token murah yang datar - bukan stablecoin
]


class TestIsStablecoin:
    """Unit test untuk deteksi stablecoin."""

    @pytest.mark.parametrize('ticker,price,width', STABLECOIN_CASES)
    def test_stablecoin_is_detected(self, ticker, price, width):
        from services.crypto_service import is_stablecoin

        assert is_stablecoin(make_data(price, width)) is True, \
            f'{ticker} harus terdeteksi sebagai stablecoin'

    @pytest.mark.parametrize('ticker,price,width', NORMAL_CASES)
    def test_normal_coin_is_kept(self, ticker, price, width):
        from services.crypto_service import is_stablecoin

        assert is_stablecoin(make_data(price, width)) is False, \
            f'{ticker} bukan stablecoin dan harus tetap lolos'

    def test_depeg_is_still_stablecoin(self):
        """Stablecoin yang lepas dari peg (0.93) tetap ikut tersaring."""
        from services.crypto_service import is_stablecoin

        assert is_stablecoin(make_data(0.93, 0.30)) is True

    def test_missing_bollinger_data_is_not_filtered(self):
        """Data Bollinger hilang -> jangan asal menyaring."""
        from services.crypto_service import is_stablecoin

        data = make_data(1.0, 0.05)
        del data['bb_upper']
        del data['bb_lower']
        del data['bb_middle']

        assert is_stablecoin(data) is False

    def test_broken_bollinger_values_are_not_filtered(self):
        """Nilai Bollinger rusak (0/None) -> jangan asal menyaring."""
        from services.crypto_service import is_stablecoin

        data = make_data(1.0, 0.05)
        data['bb_upper'] = None
        assert is_stablecoin(data) is False

        data = make_data(1.0, 0.05)
        data['bb_middle'] = 0
        assert is_stablecoin(data) is False

    def test_zero_price_is_not_filtered(self):
        """Harga 0 (data gagal) bukan stablecoin."""
        from services.crypto_service import is_stablecoin

        assert is_stablecoin(make_data(0, 0.05)) is False


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

            def get_usd_idr_rate(self):
                return 16000

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
                return make_data(1.0, 0.03), False
            return make_data(1.13, 11.5), False

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

        def fake_fetch(ticker, interval, period, skip_if_missing=False):
            # USDC: harga dipatok $1, band sangat sempit
            return make_data(1.0, 0.03)

        def fake_save_outcome(key, outcome, **kwargs):
            state['outcomes'].append((key, outcome))

        monkeypatch.setattr(jobs_crypto.crypto_service,
                            'get_crypto_data_combined', fake_fetch)
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

