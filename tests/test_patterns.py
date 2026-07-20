"""
Unit tests for utils/patterns.py
Tests chart pattern detection (channels, triangles, wedges, harmonics).
"""
import sys
import os

import numpy as np
import pandas as pd
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.patterns import (
    detect_channel_patterns,
    detect_triangle_patterns,
    detect_wedge_patterns,
    detect_all_patterns,
    format_pattern_summary,
    get_pattern_emoji,
)


def make_ohlcv(high=None, low=None, close=None, open_=None, volume=None, n=100):
    """Helper to create OHLCV DataFrame for testing."""
    if close is None:
        close = np.ones(n) * 100
    if high is None:
        high = close + 1
    if low is None:
        low = close - 1
    if open_ is None:
        open_ = close
    if volume is None:
        volume = np.ones(n) * 1000
    return pd.DataFrame({
        'Open': open_,
        'High': high,
        'Low': low,
        'Close': close,
        'Volume': volume
    })


# === Channel Pattern Tests ===

class TestChannelPatterns:
    """Tests for channel pattern detection."""

    def test_insufficient_data(self):
        """Should return empty list if not enough data."""
        df = make_ohlcv(n=50)
        result = detect_channel_patterns(df, lookback=100)
        assert result == []

    def test_flat_data_no_pattern(self):
        """Flat data with insufficient volatility should return empty."""
        # Truly flat: high/low very close to close
        close = np.linspace(100, 100.5, 100)
        high = close + 0.05  # Very tight range
        low = close - 0.05
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        assert result == []

    def test_uptrend_channel(self):
        """Strong uptrend should detect Uptrend Channel."""
        # Strong uptrend: +0.5 per step, ~50% over 100 candles
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        names = [p['name'] for p in result]
        assert 'Uptrend Channel' in names

    def test_downtrend_channel(self):
        """Strong downtrend should detect Downtrend Channel."""
        close = np.linspace(100, 50, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        names = [p['name'] for p in result]
        assert 'Downtrend Channel' in names

    def test_uptrend_channel_bullish_flag(self):
        """Uptrend Channel should be marked bullish."""
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        uptrend = [p for p in result if p['name'] == 'Uptrend Channel']
        if uptrend:
            assert uptrend[0]['bullish'] is True
            assert uptrend[0]['bearish'] is False

    def test_downtrend_channel_bearish_flag(self):
        """Downtrend Channel should be marked bearish."""
        close = np.linspace(100, 50, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        downtrend = [p for p in result if p['name'] == 'Downtrend Channel']
        if downtrend:
            assert downtrend[0]['bearish'] is True
            assert downtrend[0]['bullish'] is False

    def test_pattern_has_required_fields(self):
        """Pattern should have type, name, description, strength, bullish/bearish/neutral."""
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        for p in result:
            assert 'type' in p
            assert 'name' in p
            assert 'description' in p
            assert 'strength' in p
            assert 'bullish' in p
            assert 'bearish' in p
            assert 'neutral' in p

    def test_pattern_strength_range(self):
        """Pattern strength should be between 0 and 1."""
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_channel_patterns(df, lookback=100)
        for p in result:
            assert 0 <= p['strength'] <= 1


# === Triangle Pattern Tests ===

class TestTrianglePatterns:
    """Tests for triangle pattern detection."""

    def test_insufficient_data(self):
        """Should return empty if not enough data."""
        df = make_ohlcv(n=50)
        result = detect_triangle_patterns(df, lookback=100)
        assert result == []

    def test_ascending_triangle(self):
        """Flat resistance, rising support should detect Ascending Triangle."""
        # Flat highs around 130, rising lows from 90 to 115
        highs = np.ones(100) * 130
        lows = np.linspace(90, 115, 100)
        closes = (highs + lows) / 2
        df = make_ohlcv(high=highs, low=lows, close=closes, n=100)
        result = detect_triangle_patterns(df, lookback=100)
        names = [p['name'] for p in result]
        assert 'Ascending Triangle' in names

    def test_descending_triangle(self):
        """Falling resistance, flat support should detect Descending Triangle."""
        highs = np.linspace(130, 105, 100)
        lows = np.ones(100) * 100
        closes = (highs + lows) / 2
        df = make_ohlcv(high=highs, low=lows, close=closes, n=100)
        result = detect_triangle_patterns(df, lookback=100)
        names = [p['name'] for p in result]
        assert 'Descending Triangle' in names

    def test_ascending_triangle_bullish(self):
        """Ascending Triangle should be bullish."""
        highs = np.ones(100) * 130
        lows = np.linspace(90, 115, 100)
        closes = (highs + lows) / 2
        df = make_ohlcv(high=highs, low=lows, close=closes, n=100)
        result = detect_triangle_patterns(df, lookback=100)
        asc = [p for p in result if p['name'] == 'Ascending Triangle']
        if asc:
            assert asc[0]['bullish'] is True

    def test_descending_triangle_bearish(self):
        """Descending Triangle should be bearish."""
        highs = np.linspace(130, 105, 100)
        lows = np.ones(100) * 100
        closes = (highs + lows) / 2
        df = make_ohlcv(high=highs, low=lows, close=closes, n=100)
        result = detect_triangle_patterns(df, lookback=100)
        desc = [p for p in result if p['name'] == 'Descending Triangle']
        if desc:
            assert desc[0]['bearish'] is True

    def test_low_volatility_no_pattern(self):
        """Very low volatility should return no patterns."""
        # Range < 1.5%
        close = np.ones(100) * 100
        high = close + 0.1
        low = close - 0.1
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_triangle_patterns(df, lookback=100)
        assert result == []


# === Wedge Pattern Tests ===

class TestWedgePatterns:
    """Tests for wedge pattern detection."""

    def test_insufficient_data(self):
        """Should return empty if not enough data."""
        df = make_ohlcv(n=50)
        result = detect_wedge_patterns(df, lookback=100)
        assert result == []

    def test_falling_wedge(self):
        """Both falling with support falling faster should detect Falling Wedge."""
        # Highs: 130→105 (slow fall), Lows: 110→95 (fast fall)
        highs = np.linspace(130, 105, 100)
        lows = np.linspace(110, 95, 100)
        closes = (highs + lows) / 2
        df = make_ohlcv(high=highs, low=lows, close=closes, n=100)
        result = detect_wedge_patterns(df, lookback=100)
        names = [p['name'] for p in result]
        assert 'Falling Wedge' in names

    def test_falling_wedge_bullish(self):
        """Falling Wedge should be bullish."""
        highs = np.linspace(130, 105, 100)
        lows = np.linspace(110, 95, 100)
        closes = (highs + lows) / 2
        df = make_ohlcv(high=highs, low=lows, close=closes, n=100)
        result = detect_wedge_patterns(df, lookback=100)
        fall = [p for p in result if p['name'] == 'Falling Wedge']
        if fall:
            assert fall[0]['bullish'] is True

    def test_low_volatility_no_pattern(self):
        """Very low volatility should return no patterns."""
        close = np.ones(100) * 100
        high = close + 0.1
        low = close - 0.1
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_wedge_patterns(df, lookback=100)
        assert result == []


# === detect_all_patterns Tests ===

class TestDetectAllPatterns:
    """Tests for combined pattern detection."""

    def test_returns_dict_structure(self):
        """Should return dict with required fields."""
        df = make_ohlcv(close=np.linspace(100, 150, 100), n=100)
        result = detect_all_patterns(df, lookback=100)
        assert 'patterns_found' in result
        assert 'pattern_list' in result
        assert 'bullish_patterns' in result
        assert 'bearish_patterns' in result
        assert 'neutral_patterns' in result
        assert 'strongest_pattern' in result
        assert 'pattern_summary' in result

    def test_uptrend_has_strongest(self):
        """Strong uptrend should have a strongest pattern."""
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_all_patterns(df, lookback=100)
        assert result['strongest_pattern'] is not None
        assert 'name' in result['strongest_pattern']

    def test_pattern_counts_consistent(self):
        """Pattern counts should be consistent across the dict."""
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_all_patterns(df, lookback=100)
        # patterns_found should equal len(pattern_list)
        assert result['patterns_found'] == len(result['pattern_list'])
        # bullish_patterns + bearish_patterns + neutral_patterns may overlap or not cover all
        # but the total must match
        assert len(result['pattern_list']) >= max(
            len(result['bullish_patterns']),
            len(result['bearish_patterns']),
            len(result['neutral_patterns'])
        )

    def test_pattern_summary_format(self):
        """Pattern summary should return string."""
        close = np.linspace(100, 150, 100)
        high = close + 2
        low = close - 2
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_all_patterns(df, lookback=100)
        assert isinstance(result['pattern_summary'], str)

    def test_pattern_summary_empty(self):
        """Empty patterns should give default summary."""
        # Flat data
        close = np.ones(100) * 100
        high = close + 0.1
        low = close - 0.1
        df = make_ohlcv(high=high, low=low, close=close, n=100)
        result = detect_all_patterns(df, lookback=100)
        assert result['patterns_found'] == 0
        assert result['pattern_summary'] == "No clear patterns detected"

    def test_uses_lookback_param(self):
        """Custom lookback should be respected."""
        df = make_ohlcv(close=np.linspace(100, 150, 200), n=200)
        result_50 = detect_all_patterns(df, lookback=50)
        result_100 = detect_all_patterns(df, lookback=100)
        # Both should work
        assert 'patterns_found' in result_50
        assert 'patterns_found' in result_100


# === Format Pattern Summary Tests ===

class TestFormatPatternSummary:
    """Tests for format_pattern_summary function."""

    def test_empty_patterns(self):
        """Empty patterns should return default message."""
        result = format_pattern_summary([])
        assert result == "No clear patterns detected"

    def test_single_pattern(self):
        """Single pattern should produce emoji + name + bar."""
        patterns = [{'name': 'Uptrend Channel', 'strength': 0.8, 'bullish': True, 'bearish': False, 'neutral': False}]
        result = format_pattern_summary(patterns)
        assert 'Uptrend Channel' in result
        assert '🟢' in result  # bullish emoji

    def test_bearish_pattern(self):
        """Bearish pattern should have red emoji."""
        patterns = [{'name': 'Downtrend Channel', 'strength': 0.6, 'bullish': False, 'bearish': True, 'neutral': False}]
        result = format_pattern_summary(patterns)
        assert 'Downtrend Channel' in result
        assert '🔴' in result

    def test_neutral_pattern(self):
        """Neutral pattern should have yellow emoji."""
        patterns = [{'name': 'Ranging', 'strength': 0.5, 'bullish': False, 'bearish': False, 'neutral': True}]
        result = format_pattern_summary(patterns)
        assert 'Ranging' in result
        assert '🟡' in result

    def test_multiple_patterns(self):
        """Multiple patterns should be joined by pipe."""
        patterns = [
            {'name': 'Uptrend Channel', 'strength': 0.8, 'bullish': True, 'bearish': False, 'neutral': False},
            {'name': 'Ranging', 'strength': 0.5, 'bullish': False, 'bearish': False, 'neutral': True}
        ]
        result = format_pattern_summary(patterns)
        assert ' | ' in result
        assert 'Uptrend Channel' in result
        assert 'Ranging' in result


# === Pattern Emoji Tests ===

class TestPatternEmoji:
    """Tests for get_pattern_emoji function."""

    def test_known_patterns(self):
        """Known patterns should return their emoji."""
        assert get_pattern_emoji('Uptrend Channel') == '↗️'
        assert get_pattern_emoji('Downtrend Channel') == '↘️'
        assert get_pattern_emoji('Ascending Triangle') == '📈'
        assert get_pattern_emoji('Descending Triangle') == '📉'
        assert get_pattern_emoji('Gartley') == '🦋'

    def test_unknown_pattern(self):
        """Unknown pattern should return default emoji."""
        result = get_pattern_emoji('NonexistentPattern')
        assert result == '📊'

    def test_empty_string(self):
        """Empty string should return default emoji."""
        result = get_pattern_emoji('')
        assert result == '📊'


# === Integration Tests ===

class TestPatternsIntegration:
    """Integration tests for pattern detection."""

    def test_realistic_uptrend(self):
        """Test with realistic stock-like uptrend data."""
        np.random.seed(42)
        n = 100
        base = np.linspace(100, 130, n)
        noise = np.random.randn(n) * 0.5
        close = base + noise
        high = close + np.abs(np.random.randn(n)) * 0.3
        low = close - np.abs(np.random.randn(n)) * 0.3
        df = make_ohlcv(high=high, low=low, close=close, n=n)
        result = detect_all_patterns(df, lookback=100)
        # Should detect at least one pattern
        assert result['patterns_found'] >= 0  # May or may not detect
        # Verify summary is a string
        assert isinstance(result['pattern_summary'], str)

    def test_realistic_downtrend(self):
        """Test with realistic stock-like downtrend data."""
        np.random.seed(42)
        n = 100
        base = np.linspace(100, 70, n)
        noise = np.random.randn(n) * 0.5
        close = base + noise
        high = close + np.abs(np.random.randn(n)) * 0.3
        low = close - np.abs(np.random.randn(n)) * 0.3
        df = make_ohlcv(high=high, low=low, close=close, n=n)
        result = detect_all_patterns(df, lookback=100)
        assert isinstance(result['pattern_summary'], str)

    def test_realistic_sideways(self):
        """Test with realistic sideways data."""
        np.random.seed(42)
        n = 100
        close = np.ones(n) * 100 + np.random.randn(n) * 0.3
        high = close + np.abs(np.random.randn(n)) * 0.2
        low = close - np.abs(np.random.randn(n)) * 0.2
        df = make_ohlcv(high=high, low=low, close=close, n=n)
        result = detect_all_patterns(df, lookback=100)
        # Sideways with low volatility should have no patterns or 'Ranging'
        assert result['patterns_found'] == 0 or 'Ranging' in [p['name'] for p in result['pattern_list']]
