"""Unit tests untuk utils/backtest.py (mesin backtest walk-forward).

Data sintetis dibuat deterministik: datar → tajam turun (oversold) → naik
kuat harusnya menghasilkan sinyal BUY yang TP1-nya tersentuh; kebalikannya
menghasilkan SELL.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.backtest import build_data_at, simulate_outcome, run_backtest, summarize, compare_features
from services.signal_service import FEATURES, set_features


def make_df(prices, spread=0.5, volume=1000.0):
    close = pd.Series(prices, dtype=float)
    return pd.DataFrame({
        'Open': close - 0.1,
        'High': close + spread,
        'Low': close - spread,
        'Close': close,
        'Volume': [float(volume)] * len(prices),
    })


def rising_after_dip_prices():
    """80 bar datar → dip 20 bar (90) → pulih kuat 60 bar."""
    flat = [100.0] * 80
    dip = [100.0 - (i + 1) * 0.5 for i in range(20)]        # → 90.0
    recovery = [90.0 + (i + 1) * 1.5 for i in range(60)]    # → 178.5
    return flat + dip + recovery


def falling_after_peak_prices():
    """80 bar datar → naik 20 bar (110) → jatuh kuat 60 bar."""
    flat = [100.0] * 80
    rise = [100.0 + (i + 1) * 0.5 for i in range(20)]       # → 110.0
    fall = [110.0 - (i + 1) * 1.5 for i in range(60)]       # → 20.0
    return flat + rise + fall


@pytest.fixture(autouse=True)
def _restore_features():
    old = dict(FEATURES)
    yield
    set_features(**old)


class TestSimulateOutcome:
    def test_tp1_hit_first(self):
        df = make_df([100, 101, 103, 104], spread=0.5)
        outcome, bars = simulate_outcome(df, 0, 'BUY', tp1=102.5, sl=95, horizon=5)
        assert outcome == 'tp1'
        assert bars == 2

    def test_sl_hit_first(self):
        df = make_df([100, 99, 97, 96], spread=0.5)
        outcome, bars = simulate_outcome(df, 0, 'BUY', tp1=110, sl=98, horizon=5)
        assert outcome == 'sl'

    def test_same_bar_touch_counts_as_sl(self):
        """Asumsi pesimis: satu bar menyentuh TP dan SL → dihitung SL."""
        df = make_df([100, 100], spread=0.0)
        df.loc[1, 'High'] = 106  # >= tp1
        df.loc[1, 'Low'] = 94    # <= sl
        outcome, bars = simulate_outcome(df, 0, 'BUY', tp1=105, sl=95, horizon=5)
        assert outcome == 'sl'
        assert bars == 1

    def test_sell_direction_mirrors(self):
        df = make_df([100, 99, 98, 97], spread=0.5)
        outcome, _ = simulate_outcome(df, 0, 'SELL', tp1=97.5, sl=103, horizon=5)
        assert outcome == 'tp1'

    def test_timeout(self):
        df = make_df([100, 100.2, 100.1, 100.3], spread=0.05)
        outcome, bars = simulate_outcome(df, 0, 'BUY', tp1=105, sl=95, horizon=2)
        assert outcome == 'timeout'
        assert bars == 2


class TestBuildDataAt:
    def test_short_window_returns_none(self):
        df = make_df(rising_after_dip_prices())
        assert build_data_at(df, 50) is None

    def test_payload_shape_matches_engine_expectations(self):
        df = make_df(rising_after_dip_prices())
        data = build_data_at(df, 150)
        assert data is not None
        for key in ('price', 'rsi', 'ma_fast', 'ma_slow', 'macd', 'macd_signal',
                    'macd_hist', 'bb_upper', 'bb_lower', 'atr', 'volume_ratio',
                    'vwap', 'htf_trend', 'support', 'resistance', 'change'):
            assert key in data, f"key {key} hilang"
        assert data['price'] == pytest.approx(df.iloc[150]['Close'])


class TestRunBacktest:
    def test_recovery_fires_buy_and_hits_tp1(self):
        df = make_df(rising_after_dip_prices())
        trades = run_backtest(df, market='stock', timeframe='60',
                              horizon=40, cooldown_bars=12)
        assert trades, "harusnya ada sinyal BUY di fase pemulihan"
        buys = [t for t in trades if t['signal'] == 'BUY']
        assert buys, "fase pemulihan harus menghasilkan BUY"
        # Sinyal dekat ujung data wajib timeout (kehabisan bar maju);
        # sisanya di tren naik kuat harus TP1 sebelum SL.
        assert any(t['outcome'] == 'tp1' for t in buys)
        assert all(t['outcome'] in ('tp1', 'timeout') for t in buys)

    def test_decline_fires_sell_and_hits_tp1(self):
        df = make_df(falling_after_peak_prices())
        trades = run_backtest(df, market='stock', timeframe='60',
                              horizon=40, cooldown_bars=12)
        assert trades, "harusnya ada sinyal SELL di fase penurunan"
        sells = [t for t in trades if t['signal'] == 'SELL']
        assert sells, "fase penurunan harus menghasilkan SELL"
        assert any(t['outcome'] == 'tp1' for t in sells)
        assert all(t['outcome'] in ('tp1', 'timeout') for t in sells), \
            "tren turun kuat tidak boleh menyentuh SL lebih dulu"

    def test_cooldown_suppresses_repeats(self):
        df = make_df(rising_after_dip_prices())
        trades = run_backtest(df, market='stock', timeframe='60',
                              horizon=40, cooldown_bars=12)
        buy_bars = [t['bar'] for t in trades if t['signal'] == 'BUY']
        for a, b in zip(buy_bars, buy_bars[1:]):
            assert b - a >= 12, "dua sinyal BUY dalam jendela cooldown"

    def test_features_restored_after_run(self):
        df = make_df(rising_after_dip_prices())
        run_backtest(df, market='stock', timeframe='60', horizon=20,
                     features={'htf_trend': False})
        assert FEATURES['htf_trend'] is True


class TestSummarize:
    def test_summary_has_total_row(self):
        trades = [
            {'bar': 1, 'signal': 'BUY', 'quality': 'MODERATE', 'buy_score': 60,
             'sell_score': 0, 'htf_trend': 'up', 'outcome': 'tp1', 'bars': 3,
             'tp1_ret_pct': 2.0, 'sl_ret_pct': -3.0, 'risk_reward': 0.67,
             'max_tp_mult': 2.0},
            {'bar': 20, 'signal': 'BUY', 'quality': 'MODERATE', 'buy_score': 60,
             'sell_score': 0, 'htf_trend': 'up', 'outcome': 'sl', 'bars': 7,
             'tp1_ret_pct': 2.0, 'sl_ret_pct': -3.0, 'risk_reward': 0.67,
             'max_tp_mult': 2.0},
        ]
        table = summarize(trades, fee_pct=0.3)
        assert not table.empty
        total = table[table['signal'] == 'TOTAL']
        assert len(total) == 1
        assert total.iloc[0]['n'] == 2
        # win rate keputusan = 1 dari 2
        assert total.iloc[0]['win_rate_decided'] == '50%'
        # ekspektansi = ((2.0 - 0.3) + (-3.0 - 0.3)) / 2 = -0.8
        assert total.iloc[0]['exp_net_%'] == '-0.80'

    def test_empty_trades_give_empty_table(self):
        assert summarize([]).empty


class TestCompareFeatures:
    def test_ab_returns_both_engines(self):
        df = make_df(rising_after_dip_prices())
        result = compare_features(df, market='stock', timeframe='60',
                                  horizon=40, cooldown_bars=12)
        assert 'classic' in result and 'v2' in result
        assert 'classic_trades' in result and 'v2_trades' in result
        assert result['v2'].empty or (result['v2']['signal'] == 'TOTAL').any()
        assert result['classic'].empty or (result['classic']['signal'] == 'TOTAL').any()
