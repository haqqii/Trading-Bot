"""Test gerbang tren (utils/strategy) dan penerapannya di bot live.

Aturan yang diuji (lihat docs/SPEC.md 3.1.2):
- timeframe harian: hanya sinyal SELL yang boleh, dan hanya saat tren
  harian sedang naik (MA50 > MA100 + deadband);
- timeframe lain: tidak digerbang (belum diukur);
- data yang trennya tidak bisa dipercaya (bukan dari Yahoo, atau bar-nya
  kurang dari 100) diblokir, bukan diteruskan.

Yang dijaga di sini:
1. Aturan gerbang tidak lolos sinyal yang salah arah.
2. Bot live benar-benar memakai aturan yang sama - signal SELL di TF
   harian dengan tren 'up' terkirim, yang lain tidak.
3. Harness backtest memakai fungsi yang sama, jadi angka di SPEC bisa
   direproduksi.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.strategy import (
    TREND_GATE_MIN_BARS,
    TREND_GATE_RULES,
    passes_trend_gate,
    describe_trend_gate,
)

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=WIB)


class TestRuleUnit:
    """Aturan gerbang tren, level fungsi."""

    def test_daily_buy_is_blocked(self):
        # Beli dip di pasar turun diukur negatif (lihat SPEC 3.1.1)
        assert not passes_trend_gate('BUY', 'down', '1440')
        assert not passes_trend_gate('BUY', 'up', '1440')
        assert not passes_trend_gate('BUY', 'flat', '1440')

    def test_daily_sell_needs_uptrend(self):
        assert passes_trend_gate('SELL', 'up', '1440')
        assert not passes_trend_gate('SELL', 'down', '1440')
        assert not passes_trend_gate('SELL', 'flat', '1440')

    def test_unknown_trend_is_blocked(self):
        assert not passes_trend_gate('SELL', None, '1440')
        assert not passes_trend_gate('SELL', '', '1440')

    def test_other_timeframes_not_gated(self):
        # Timeframe intraday belum diukur - sinyal harus tetap lewat.
        for tf in ('1', '5', '15', '30', '60', '240'):
            assert passes_trend_gate('BUY', 'flat', tf)
            assert passes_trend_gate('SELL', 'up', tf)

    def test_untrusted_source_is_blocked(self):
        # Jalur CoinGecko memakai sparkline 7 jam - definisi trennya beda.
        assert not passes_trend_gate('SELL', 'up', '1440', source='coingecko')
        assert passes_trend_gate('SELL', 'up', '1440', source='yahoo')

    def test_too_short_series_is_blocked(self):
        assert not passes_trend_gate('SELL', 'up', '1440', source='yahoo',
                                     candles=TREND_GATE_MIN_BARS - 1)
        assert passes_trend_gate('SELL', 'up', '1440', source='yahoo',
                                 candles=TREND_GATE_MIN_BARS)

    def test_timeframe_may_be_int_or_str(self):
        assert passes_trend_gate('SELL', 'up', 1440)

    def test_rules_only_cover_measured_timeframes(self):
        assert set(TREND_GATE_RULES) <= {'1440'}
        assert '1440' in describe_trend_gate()


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


def make_data(ticker, price=100.0, htf_trend='up', source='yahoo', candles=250):
    """Payload crypto seperti hasil get_crypto_data*()."""
    return {
        'ticker': ticker,
        'price': price,
        'range_24h': 3.0,
        'candles': candles,
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
        'htf_trend': htf_trend,
        'source': source,
    }


# Ticker uji: (nama, tren, sumber, arah sinyal)
SCAN_CASES = [
    ('SELLUP-USD', 'up', 'yahoo', 'SELL'),      # lolos
    ('SELDOWN-USD', 'down', 'yahoo', 'SELL'),   # tren salah
    ('SELLFLAT-USD', 'flat', 'yahoo', 'SELL'),  # tren tidak diketahui
    ('SELLCOIN-USD', 'up', 'coingecko', 'SELL'),  # definisi tren beda
    ('BUYUP-USD', 'up', 'yahoo', 'BUY'),        # arah salah
]


class TestScanAppliesGate:
    """Scan harian live harus memakai gerbang tren."""

    @pytest.fixture
    def scan(self, monkeypatch):
        from handlers.scheduler import jobs_crypto

        state = {'saved': []}

        pairs = {name: name.split('-')[0] for name, _, _, _ in SCAN_CASES}

        class FakeCryptoService:
            crypto_pairs = pairs
            used_fallback_pairs = False

            def get_usd_idr_rate(self):
                return 16000

            def refresh_price_reference(self, priority=False):
                return {}

            def get_crypto_data(self, ticker, interval, period,
                                skip_if_missing=False, priority=False):
                case = next(c for c in SCAN_CASES if c[0] == ticker)
                if case[2] != 'yahoo':
                    # Jalur Yahoo gagal -> ticker diambil dari CoinGecko.
                    return None
                return make_data(ticker, htf_trend=case[1], source=case[2])

            def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                               use_cache=True, priority=False):
                out = {}
                for ticker in tickers:
                    case = next(c for c in SCAN_CASES if c[0] == ticker)
                    out[ticker] = make_data(ticker, htf_trend=case[1],
                                            source=case[2])
                return out

        direction_by_ticker = {name: direction
                               for name, _, _, direction in SCAN_CASES}

        monkeypatch.setattr(jobs_crypto, 'now_wib', lambda: NOW)
        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeCryptoService())
        monkeypatch.setattr(jobs_crypto, 'signal_service',
                            FakeSignalService(direction_by_ticker))
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '1440'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: state.setdefault('signals', {}))
        monkeypatch.setattr(jobs_crypto, '_schedule_followup_scan',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto.db, 'get_all_signals',
                            lambda: state.setdefault('db_rows', {}))
        monkeypatch.setattr(jobs_crypto.db, 'save_active_signal',
                            lambda **kwargs: state['saved'].append(kwargs))
        jobs_crypto._last_scan_by_tf.clear()
        jobs_crypto._force_next_scan = False
        return state

    def test_only_allowed_signal_is_sent(self, scan):
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 1, (
            f'hanya SELL saat tren up yang boleh, terkirim: {app.bot.sent}')
        assert 'SELLUP' in app.bot.sent[0][1]

    def test_blocked_signals_never_reach_db(self, scan):
        from handlers.scheduler import jobs_crypto

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        saved = [s['ticker'] for s in scan['saved']]
        assert saved == ['SELLUP-USD']

    def test_intraday_timeframe_not_gated(self, monkeypatch):
        """TF 1 jam belum diukur - sinyal tidak boleh diblokir gerbang."""
        from handlers.scheduler import jobs_crypto

        state = {'saved': []}

        class FakeCryptoService:
            crypto_pairs = {'INTRADAY-USD': 'Intraday'}
            used_fallback_pairs = False

            def get_usd_idr_rate(self):
                return 16000

            def refresh_price_reference(self, priority=False):
                return {}

            def get_crypto_data(self, ticker, interval, period,
                                skip_if_missing=False, priority=False):
                # Tren 'down' + sumber coingecko: kalau digerbang, sinyal
                # ini diblokir.
                return make_data(ticker, htf_trend='down', source='coingecko')

            def get_crypto_data_coingecko_bulk(self, tickers, retry=3,
                                               use_cache=True, priority=False):
                return {}

        monkeypatch.setattr(jobs_crypto, 'now_wib', lambda: NOW)
        monkeypatch.setattr(jobs_crypto, 'crypto_service', FakeCryptoService())
        monkeypatch.setattr(jobs_crypto, 'signal_service',
                            FakeSignalService({'INTRADAY-USD': 'BUY'}))
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {
            '1': {'notif_crypto': True, 'timeframe': '60'}})
        monkeypatch.setattr(jobs_crypto, '_get_last_buy_signals',
                            lambda: state.setdefault('signals', {}))
        monkeypatch.setattr(jobs_crypto, '_schedule_followup_scan',
                            lambda *a, **k: None)
        monkeypatch.setattr(jobs_crypto.db, 'get_all_signals',
                            lambda: state.setdefault('db_rows', {}))
        monkeypatch.setattr(jobs_crypto.db, 'save_active_signal',
                            lambda **kwargs: state['saved'].append(kwargs))
        jobs_crypto._last_scan_by_tf.clear()
        jobs_crypto._force_next_scan = False

        app = FakeApp()
        asyncio.run(jobs_crypto.check_crypto_signals(app))

        assert len(app.bot.sent) == 1


class TestHarnessUsesSameRule:
    """Harness backtest harus memakai fungsi gerbang yang sama."""

    def test_harness_gate_blocks_buy(self, monkeypatch):
        from utils.backtest import run_backtest

        def make_df(prices, spread=0.5):
            close = pd.Series(prices, dtype=float)
            return pd.DataFrame({
                'Open': close - 0.1,
                'High': close + spread,
                'Low': close - spread,
                'Close': close,
                'Volume': [1000.0] * len(prices),
            })

        # 120 bar datar lalu naik: MA50 > MA100 -> tren 'up'
        prices = [100.0] * 120 + [100.0 + i * 0.5 for i in range(40)]
        df = make_df(prices)

        class FakeEngine:
            def generate_crypto_signal(self, data):
                bar = data['candles'] - 1
                direction = 'SELL' if bar in (130, 140) else 'BUY'
                price = data['price']
                return {
                    'signal': direction,
                    'quality': 'STRONG',
                    'entry': price,
                    'tp1': price * 1.04,
                    'tp2': price * 1.08,
                    'tp3': price * 1.12,
                    'sl': price * 0.97,
                    'buy_score': 70,
                    'sell_score': 70,
                }

        monkeypatch.setattr('utils.backtest.SignalService', FakeEngine())

        semua = run_backtest(df, market='crypto', timeframe='1440', horizon=10,
                             cooldown_bars=5, exit_model='signal')
        assert any(t['signal'] == 'BUY' for t in semua)

        digerbang = run_backtest(df, market='crypto', timeframe='1440',
                                 horizon=10, cooldown_bars=5,
                                 exit_model='signal', trend_gate=True)
        assert [t['signal'] for t in digerbang] == ['SELL', 'SELL']
