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

from utils.backtest import (build_data_at, simulate_outcome, run_backtest, summarize,
                           compare_features, collect_signals, find_data_problems)
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


class FakeSignalService:
    """Engine palsu: sinyal per bar ditentukan skenario, bukan skor.

    Dipakai untuk menguji gerbang min_quality/long_only secara
    deterministik - sinyal sintetis dari indikator asli tidak bisa dijamin
    menghasilkan kualitas tertentu di bar tertentu.
    """

    def __init__(self, by_bar):
        self.by_bar = by_bar  # {bar: (signal, quality)}

    def generate_stock_signal(self, data):
        bar = data['candles'] - 1
        signal, quality = self.by_bar.get(bar, ('HOLD', 'WEAK'))
        price = data['price']
        return {
            'signal': signal,
            'quality': quality,
            'entry': price,
            'tp1': price * 1.04,
            'tp2': price * 1.08,
            'tp3': price * 1.12,
            'sl': price * 0.97,
            'buy_score': 70 if quality == 'STRONG' else 30,
            'sell_score': 0,
        }


# Bar ≥ WARMUP_BARS (110), < len(df) - 1: sinyal BUY STRONG di 115, BUY
# WEAK di 117, BUY STRONG lagi di 118 (jarak 3 bar, cooldown 2 bar).
GATE_SCENARIO = {115: ('BUY', 'STRONG'), 117: ('BUY', 'WEAK'), 118: ('BUY', 'STRONG')}


class TestGateFilter:
    """Gerbang min_quality/long_only menyaring sebelum cooldown."""

    def test_long_only_drops_sell(self, monkeypatch):
        df = make_df(falling_after_peak_prices())
        fake = FakeSignalService({120: ('SELL', 'STRONG'), 130: ('BUY', 'MODERATE')})
        monkeypatch.setattr('utils.backtest.SignalService', fake)
        trades = run_backtest(df, market='stock', timeframe='60', horizon=10,
                              cooldown_bars=5, long_only=True)
        assert all(t['signal'] == 'BUY' for t in trades)
        assert [t['bar'] for t in trades] == [130]

    def test_min_quality_keeps_only_strong(self, monkeypatch):
        df = make_df(rising_after_dip_prices())
        fake = FakeSignalService(GATE_SCENARIO)
        monkeypatch.setattr('utils.backtest.SignalService', fake)
        trades = run_backtest(df, market='stock', timeframe='60', horizon=10,
                              cooldown_bars=2, min_quality='STRONG')
        assert [t['bar'] for t in trades] == [115, 118]
        assert all(t['quality'] == 'STRONG' for t in trades)

    def test_filtered_signal_does_not_consume_cooldown(self, monkeypatch):
        """Sinyal yang digerbang keluar tidak boleh menelan slot cooldown.

        Tanpa gerbang: BUY WEAK di 117 memicu cooldown, jadi BUY STRONG di
        118 (jarak 1 bar) ditahan. Dengan gerbang: 117 tidak pernah jadi
        kandidat, jadi 118 lolos.
        """
        df = make_df(rising_after_dip_prices())
        fake = FakeSignalService(GATE_SCENARIO)
        monkeypatch.setattr('utils.backtest.SignalService', fake)

        semua = run_backtest(df, market='stock', timeframe='60', horizon=10,
                             cooldown_bars=2)
        assert [t['bar'] for t in semua] == [115, 117]

        gerbang = run_backtest(df, market='stock', timeframe='60', horizon=10,
                               cooldown_bars=2, min_quality='STRONG')
        assert [t['bar'] for t in gerbang] == [115, 118]


class TestCompareGate:
    def test_ab_returns_both_arms(self, monkeypatch):
        from utils.backtest import compare_gate

        df = make_df(rising_after_dip_prices())
        fake = FakeSignalService(GATE_SCENARIO)
        monkeypatch.setattr('utils.backtest.SignalService', fake)
        result = compare_gate(df, market='stock', timeframe='60', horizon=10,
                              cooldown_bars=2, fee_pct=0.3)
        assert set(result) >= {'semua', 'gerbang', 'trades_semua', 'trades_gerbang'}
        assert [t['bar'] for t in result['trades_semua']] == [115, 117]
        assert [t['bar'] for t in result['trades_gerbang']] == [115, 118]
        # Tabel gerbang hanya memuat kualitas STRONG.
        gerbang_table = result['gerbang']
        assert set(gerbang_table['quality']) <= {'STRONG', '-'}


class TestFindDataProblems:
    """Penjaga kualitas data - seri rusak tidak boleh diukur."""

    def test_normal_series_is_clean(self):
        df = make_df(rising_after_dip_prices())
        assert find_data_problems(df) == []

    def test_price_scale_break_is_detected(self):
        # Skala harga token berubah (mis. token redenominasi) -> lonjakan
        # absurd yang bukan pergerakan harga.
        prices = [100.0] * 50 + [50000.0] * 5
        df = make_df(prices)
        problems = find_data_problems(df)
        assert any('skala harga berubah' in p for p in problems)

    def test_frozen_series_is_detected(self):
        df = make_df([100.0] * 60)
        problems = find_data_problems(df)
        assert any('tidak bergerak' in p for p in problems)

    def test_empty_frame_is_safe(self):
        assert find_data_problems(pd.DataFrame()) == []

    def test_missing_close_column_is_reported(self):
        df = make_df([100.0] * 10).drop(columns=['Close'])
        assert find_data_problems(df)


class FakeCryptoEngine:
    """Engine palsu untuk crypto: sinyal per bar ditentukan manual."""

    def __init__(self, by_bar):
        self.by_bar = by_bar

    def generate_crypto_signal(self, data):
        bar = data['candles'] - 1
        signal, quality = self.by_bar.get(bar, ('HOLD', 'WEAK'))
        price = data['price']
        return {
            'signal': signal,
            'quality': quality,
            'entry': price,
            'tp1': price * 1.05,
            'tp2': price * 1.08,
            'tp3': price * 1.12,
            'sl': price * 0.95,
            'buy_score': 70,
            'sell_score': 70,
        }


class TestCollectSignals:
    """Kandidat dari mesin sinyal, termasuk jenis REVERSAL."""

    def test_reversal_is_kept_as_long(self, monkeypatch):
        from utils.backtest import collect_signals

        df = make_df(rising_after_dip_prices())
        fake = FakeCryptoEngine({120: ('REVERSAL', 'STRONG')})
        monkeypatch.setattr('utils.backtest.SignalService', fake)

        sigs = collect_signals(df, market='crypto', timeframe='1440')

        reversal = [s for s in sigs if s['signal_type'] == 'REVERSAL']
        assert len(reversal) == 1
        # REVERSAL = setup rebound, jadi arah trade-nya BUY.
        assert reversal[0]['signal'] == 'BUY'
        assert reversal[0]['bar'] == 120

    def test_signal_has_measured_features(self, monkeypatch):
        from utils.backtest import collect_signals

        df = make_df(rising_after_dip_prices())
        fake = FakeCryptoEngine({120: ('BUY', 'STRONG')})
        monkeypatch.setattr('utils.backtest.SignalService', fake)

        sigs = collect_signals(df, market='crypto', timeframe='1440')

        assert sigs
        for key in ('mom_10', 'rsi', 'adx', 'htf_trend', 'signal_type'):
            assert key in sigs[0], f"key {key} hilang"


class TestSignalTypeFilter:
    """signal_types menyaring jenis sinyal yang dipakai."""

    def test_only_requested_type_is_traded(self, monkeypatch):
        df = make_df(rising_after_dip_prices())
        fake = FakeCryptoEngine({120: ('REVERSAL', 'STRONG'),
                                  130: ('SELL', 'STRONG')})
        monkeypatch.setattr('utils.backtest.SignalService', fake)

        semua = run_backtest(df, market='crypto', timeframe='1440', horizon=10,
                             cooldown_bars=5, exit_model='signal')
        assert {t['signal_type'] for t in semua} == {'REVERSAL', 'SELL'}

        hanya_sell = run_backtest(df, market='crypto', timeframe='1440',
                                  horizon=10, cooldown_bars=5,
                                  exit_model='signal',
                                  signal_types=['SELL'])
        assert [t['signal_type'] for t in hanya_sell] == ['SELL']


class TestMomentumGuard:
    """Penjaga momentum: jangan menangkap pisau jatuh."""

    def _df_with_drop(self):
        flat = [100.0] * 110
        drop = [100.0 - (i + 1) * 2.0 for i in range(10)]   # → 80.0
        return make_df(flat + drop + [80.0] * 40)

    def test_buy_after_drop_is_blocked(self, monkeypatch):
        df = self._df_with_drop()
        # Bar 119 = bar terakhir penurunan (~-20% dalam 10 bar).
        fake = FakeCryptoEngine({119: ('BUY', 'STRONG')})
        monkeypatch.setattr('utils.backtest.SignalService', fake)

        tanpa = run_backtest(df, market='crypto', timeframe='1440', horizon=10,
                             cooldown_bars=5, exit_model='signal')
        assert [t['bar'] for t in tanpa] == [119]

        dengan = run_backtest(df, market='crypto', timeframe='1440', horizon=10,
                              cooldown_bars=5, exit_model='signal',
                              momentum_guard=10.0)
        assert dengan == []
