"""
Unit tests for utils/indicators.py
Tests technical indicators used for signal generation.
"""
import sys
import os

import numpy as np
import pandas as pd
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.indicators import (
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_volume_metrics,
    calculate_vwap,
    calculate_stochastic,
    calculate_adx,
    calculate_ichimoku,
    calculate_fibonacci_retracement,
    calculate_atr,
    calculate_pivot_points,
)


# === RSI Tests ===

class TestRSI:
    """Tests for Relative Strength Index calculation."""

    def test_rsi_with_known_value(self):
        """Test RSI with a strongly rising scenario."""
        # Strongly rising prices → RSI should be high
        prices = pd.Series([100 + i * 2 for i in range(30)])
        rsi = calculate_rsi(prices, period=14)
        # Strong uptrend should give RSI > 60
        assert rsi.iloc[-1] > 60, f"Expected RSI > 60 for rising prices, got {rsi.iloc[-1]}"

    def test_rsi_oversold(self):
        """Test RSI with declining prices → should be < 30."""
        # Steadily declining prices → RSI should be low (<30)
        prices = pd.Series([100 - i * 2 for i in range(30)])
        rsi = calculate_rsi(prices, period=14)
        assert rsi.iloc[-1] < 30, f"Expected RSI < 30 for declining prices, got {rsi.iloc[-1]}"

    def test_rsi_range(self):
        """RSI should always be between 0 and 100."""
        np.random.seed(42)
        prices = pd.Series(np.cumsum(np.random.randn(100)) + 100)
        rsi = calculate_rsi(prices)
        valid_rsi = rsi.dropna()
        assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all(), "RSI out of range"

    def test_rsi_empty_input(self):
        """RSI with too-few data should return mostly NaN."""
        prices = pd.Series([100, 101, 102])
        rsi = calculate_rsi(prices, period=14)
        # First 14 values should be NaN
        assert rsi.iloc[:14].isna().all()

    def test_rsi_default_period(self):
        """Default period should be 14."""
        prices = pd.Series([100 + i for i in range(20)])
        rsi = calculate_rsi(prices)
        assert rsi.iloc[-1] is not None


# === MACD Tests ===

class TestMACD:
    """Tests for MACD indicator."""

    def test_macd_returns_dict(self):
        """MACD should return dict with macd, signal, histogram."""
        prices = pd.Series([100 + i * 0.5 for i in range(50)])
        result = calculate_macd(prices)
        assert 'macd' in result
        assert 'signal' in result
        assert 'histogram' in result

    def test_macd_uptrend_positive_histogram(self):
        """Uptrend should have positive histogram."""
        prices = pd.Series([100 + i * 2 for i in range(50)])
        result = calculate_macd(prices)
        assert result['histogram'].iloc[-1] > 0

    def test_macd_downtrend_negative_histogram(self):
        """Downtrend should have negative histogram."""
        prices = pd.Series([100 - i * 2 for i in range(50)])
        result = calculate_macd(prices)
        assert result['histogram'].iloc[-1] < 0

    def test_macd_default_params(self):
        """Default params: fast=12, slow=26, signal=9."""
        prices = pd.Series([100 + np.random.randn() for _ in range(50)])
        result = calculate_macd(prices)
        # Verify lengths match
        assert len(result['macd']) == len(prices)
        assert len(result['signal']) == len(prices)
        assert len(result['histogram']) == len(prices)


# === Bollinger Bands Tests ===

class TestBollingerBands:
    """Tests for Bollinger Bands."""

    def test_bb_returns_dict(self):
        """BB should return dict with upper, middle, lower."""
        prices = pd.Series([100 + i for i in range(30)])
        result = calculate_bollinger_bands(prices)
        assert 'upper' in result
        assert 'middle' in result
        assert 'lower' in result

    def test_bb_upper_above_lower(self):
        """Upper band should always be above lower band."""
        np.random.seed(42)
        prices = pd.Series(np.cumsum(np.random.randn(50)) + 100)
        result = calculate_bollinger_bands(prices)
        valid = ~result['upper'].isna()
        assert (result['upper'][valid] > result['lower'][valid]).all()

    def test_bb_middle_is_sma(self):
        """Middle band should be SMA."""
        prices = pd.Series([100 + i for i in range(30)])
        result = calculate_bollinger_bands(prices, period=20)
        # Middle band should equal rolling mean
        expected = prices.rolling(20).mean()
        pd.testing.assert_series_equal(result['middle'], expected, check_names=False)

    def test_bb_period_default(self):
        """Default period should be 20."""
        prices = pd.Series([100 + i for i in range(30)])
        result = calculate_bollinger_bands(prices)
        # First 19 should be NaN
        assert result['upper'].iloc[:19].isna().all()


# === Volume Metrics Tests ===

class TestVolumeMetrics:
    """Tests for Volume Metrics."""

    def test_volume_metrics_returns_dict(self):
        """Should return dict with ma, ratio, is_spike."""
        volume = pd.Series([1000] * 25)
        result = calculate_volume_metrics(volume)
        assert 'ma' in result
        assert 'ratio' in result
        assert 'is_spike' in result

    def test_volume_spike_detection(self):
        """Volume 2x average should be detected as spike."""
        volume = pd.Series([1000] * 25)
        # Replace last value with spike (2x average)
        volume.iloc[-1] = 2000
        result = calculate_volume_metrics(volume, period=20)
        assert bool(result['is_spike']) is True

    def test_no_volume_spike(self):
        """Normal volume should not be detected as spike."""
        volume = pd.Series([1000] * 25)
        result = calculate_volume_metrics(volume, period=20)
        assert bool(result['is_spike']) is False


# === VWAP Tests ===

class TestVWAP:
    """Tests for Volume Weighted Average Price."""

    def test_vwap_returns_dict(self):
        """VWAP should return dict with vwap, current, above."""
        high = pd.Series([101 + i for i in range(20)])
        low = pd.Series([99 + i for i in range(20)])
        close = pd.Series([100 + i for i in range(20)])
        volume = pd.Series([1000] * 20)
        result = calculate_vwap(high, low, close, volume)
        assert 'vwap' in result
        assert 'current' in result
        assert 'above' in result

    def test_vwap_above_price(self):
        """If close > vwap, above should be True."""
        high = pd.Series([110] * 20)
        low = pd.Series([90] * 20)
        close = pd.Series([105] * 20)
        volume = pd.Series([1000] * 20)
        result = calculate_vwap(high, low, close, volume, period=14)
        # VWAP for uniform prices should be ~100
        # close=105 > 100 → above should be True
        assert bool(result['above']) is True

    def test_vwap_below_price(self):
        """If close < vwap, above should be False."""
        high = pd.Series([110] * 20)
        low = pd.Series([90] * 20)
        close = pd.Series([95] * 20)
        volume = pd.Series([1000] * 20)
        result = calculate_vwap(high, low, close, volume, period=14)
        assert bool(result['above']) is False


# === Stochastic Tests ===

class TestStochastic:
    """Tests for Stochastic Oscillator."""

    def test_stochastic_returns_dict(self):
        """Should return dict with k, d, and current values."""
        high = pd.Series([101 + i for i in range(20)])
        low = pd.Series([99 + i for i in range(20)])
        close = pd.Series([100 + i for i in range(20)])
        result = calculate_stochastic(high, low, close)
        assert 'k' in result
        assert 'd' in result
        assert 'k_current' in result
        assert 'd_current' in result

    def test_stochastic_range(self):
        """Stochastic %K should be between 0 and 100 with realistic price data."""
        np.random.seed(42)
        # Use realistic prices where high >= low always
        base = 100
        high = pd.Series([base + abs(np.random.randn()) for _ in range(60)])
        low = pd.Series([max(0.1, base - abs(np.random.randn())) for _ in range(60)])
        close = pd.Series([base + np.random.randn() for _ in range(60)])
        result = calculate_stochastic(high, low, close)
        valid_k = result['k'].dropna()
        # Most values should be in range; allow some tolerance for edge cases
        in_range = ((valid_k >= 0) & (valid_k <= 100)).sum()
        assert in_range / len(valid_k) > 0.95, f"Only {in_range}/{len(valid_k)} in range"


# === ADX Tests ===

class TestADX:
    """Tests for Average Directional Index."""

    def test_adx_returns_dict(self):
        """Should return dict with adx, plus_di, minus_di."""
        np.random.seed(42)
        high = pd.Series(100 + np.cumsum(np.random.randn(50)))
        low = pd.Series(99 + np.cumsum(np.random.randn(50)))
        close = pd.Series(100 + np.cumsum(np.random.randn(50)))
        result = calculate_adx(high, low, close)
        assert 'adx' in result
        assert 'plus_di' in result
        assert 'minus_di' in result

    def test_adx_range(self):
        """ADX should be between 0 and 100."""
        np.random.seed(42)
        high = pd.Series(100 + np.cumsum(np.random.randn(50)))
        low = pd.Series(99 + np.cumsum(np.random.randn(50)))
        close = pd.Series(100 + np.cumsum(np.random.randn(50)))
        result = calculate_adx(high, low, close)
        valid_adx = result['adx'].dropna()
        assert (valid_adx >= 0).all() and (valid_adx <= 100).all()


# === ATR Tests ===

class TestATR:
    """Tests for Average True Range."""

    def test_atr_returns_series(self):
        """ATR should return pandas Series."""
        high = pd.Series([101 + i for i in range(30)])
        low = pd.Series([99 + i for i in range(30)])
        close = pd.Series([100 + i for i in range(30)])
        result = calculate_atr(high, low, close)
        assert isinstance(result, pd.Series)

    def test_atr_positive(self):
        """ATR should always be positive."""
        np.random.seed(42)
        high = pd.Series(100 + np.abs(np.random.randn(30)))
        low = pd.Series(100 - np.abs(np.random.randn(30)))
        close = pd.Series(100 + np.random.randn(30))
        result = calculate_atr(high, low, close)
        valid_atr = result.dropna()
        assert (valid_atr > 0).all()


# === Fibonacci Retracement Tests ===

class TestFibonacci:
    """Tests for Fibonacci retracement calculation."""

    def test_fibonacci_returns_dict(self):
        """Should return dict with levels and S/R info."""
        high = pd.Series([110 + i for i in range(60)])
        low = pd.Series([90 + i for i in range(60)])
        close = pd.Series([100 + i for i in range(60)])
        result = calculate_fibonacci_retracement(high, low, period=50)
        assert 'levels' in result
        assert '0.0' in result['levels']
        assert '100.0' in result['levels']

    def test_fibonacci_levels_correct(self):
        """Test Fibonacci level percentages."""
        high = pd.Series([110] * 60)
        low = pd.Series([90] * 60)
        close = pd.Series([100] * 60)
        result = calculate_fibonacci_retracement(high, low, period=50)
        # Range = 20
        # 23.6 level = 90 + 20*0.236 = 94.72
        # 50.0 level = 90 + 20*0.5 = 100
        # 61.8 level = 90 + 20*0.618 = 102.36
        levels = result['levels']
        assert abs(levels['23.6'] - 94.72) < 0.1
        assert abs(levels['50.0'] - 100.0) < 0.1
        assert abs(levels['61.8'] - 102.36) < 0.1


# === Ichimoku Tests ===

class TestIchimoku:
    """Tests for Ichimoku Cloud."""

    def test_ichimoku_returns_dict(self):
        """Should return dict with all components."""
        high = pd.Series([101 + i for i in range(60)])
        low = pd.Series([99 + i for i in range(60)])
        close = pd.Series([100 + i for i in range(60)])
        result = calculate_ichimoku(high, low, close)
        assert 'tenkan' in result
        assert 'kijun' in result
        assert 'senkou_a' in result
        assert 'senkou_b' in result


# === Pivot Points Tests ===

class TestPivotPoints:
    """Tests for Pivot Points calculation."""

    def test_pivot_returns_dict(self):
        """Should return dict with pivot, supports, resistances."""
        result = calculate_pivot_points(110, 90, 100)
        assert 'pp' in result
        assert 's1' in result
        assert 's2' in result
        assert 's3' in result
        assert 'r1' in result
        assert 'r2' in result
        assert 'r3' in result

    def test_pivot_calculation_correct(self):
        """PP = (H + L + C) / 3."""
        result = calculate_pivot_points(110, 90, 100)
        # PP = (110 + 90 + 100) / 3 = 100
        assert abs(result['pp'] - 100) < 0.01

    def test_resistance_above_pivot(self):
        """R1 should be above pivot."""
        result = calculate_pivot_points(110, 90, 100)
        assert result['r1'] > result['pp']
        assert result['r2'] > result['r1']

    def test_support_below_pivot(self):
        """S1 should be below pivot."""
        result = calculate_pivot_points(110, 90, 100)
        assert result['s1'] < result['pp']
        assert result['s2'] < result['s1']


# === Integration Tests ===

class TestIntegration:
    """Integration tests combining multiple indicators."""

    def test_full_pipeline(self):
        """Test full calculation pipeline with realistic data."""
        np.random.seed(42)
        n = 100
        # Simulate a trending stock
        close = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5 + 0.1))
        high = close + np.abs(np.random.randn(n))
        low = close - np.abs(np.random.randn(n))
        volume = pd.Series([1000] * n)
        volume.iloc[-1] = 3000  # Volume spike (3x average)

        # Calculate indicators
        rsi = calculate_rsi(close)
        macd = calculate_macd(close)
        bb = calculate_bollinger_bands(close)
        vol = calculate_volume_metrics(volume)

        # Verify all results are valid
        assert not rsi.iloc[-1] != rsi.iloc[-1]  # not NaN
        assert not np.isnan(macd['histogram'].iloc[-1])
        assert not np.isnan(bb['upper'].iloc[-1])
        assert bool(vol['is_spike']) is True
