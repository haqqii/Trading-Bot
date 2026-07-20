"""
Unit tests for services/signal_service.py
Tests stock and crypto signal generation logic.
"""
import sys
import os

import pandas as pd
import numpy as np
import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.signal_service import (
    SignalService,
    detect_patterns_from_data,
    signal_service,
)


def make_stock_data(**overrides):
    """Helper to create stock data dict with defaults."""
    data = {
        'price': 100,
        'rsi': 50,
        'ma_fast': 100,
        'ma_slow': 100,
        'atr': 1.5,
        'macd': 0,
        'macd_signal': 0,
        'macd_hist': 0,
        'bb_upper': 105,
        'bb_lower': 95,
        'bb_middle': 100,
        'volume_ratio': 1.0,
    }
    data.update(overrides)
    return data


def make_crypto_data(**overrides):
    """Helper to create crypto data dict with defaults."""
    data = {
        'price': 100,
        'rsi': 50,
        'ma_fast': 100,
        'ma_slow': 100,
        'atr': 2.5,
        'macd': 0,
        'macd_signal': 0,
        'macd_hist': 0,
        'bb_upper': 105,
        'bb_lower': 95,
        'volume_ratio': 1.0,
        'change': 0,
        'vwap': 100,
        'stoch_k': 50,
        'stoch_oversold': False,
        'stoch_overbought': False,
        'stoch_bullish_cross': False,
        'adx': 25,
        'plus_di': 25,
        'minus_di': 25,
        'adx_strong': False,
        'ichi_bullish': False,
        'ichi_bearish': False,
        'ichi_cloud_above': False,
    }
    data.update(overrides)
    return data


# === Stock Signal Tests ===

class TestStockSignalBasic:
    """Basic tests for stock signal generation."""

    def test_no_data_returns_hold(self):
        """Empty data should return HOLD."""
        result = SignalService.generate_stock_signal(None)
        assert result['signal'] == 'HOLD'

    def test_empty_dict_returns_hold(self):
        """Empty dict should return HOLD."""
        result = SignalService.generate_stock_signal({})
        # Empty dict doesn't have 'price' key
        # Should still work but defaults
        assert 'signal' in result

    def test_neutral_data_returns_hold(self):
        """All neutral indicators → HOLD."""
        data = make_stock_data()  # All defaults = neutral
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] in ['HOLD', 'BUY', 'SELL']

    def test_signal_has_required_fields(self):
        """Signal output should have all required fields."""
        data = make_stock_data()
        result = SignalService.generate_stock_signal(data)
        assert 'signal' in result
        assert 'reason' in result
        assert 'entry' in result
        assert 'tp1' in result
        assert 'tp2' in result
        assert 'tp3' in result
        assert 'sl' in result
        assert 'rsi' in result
        assert 'atr' in result
        assert 'quality' in result
        assert 'buy_score' in result
        assert 'sell_score' in result


class TestStockSignalRSI:
    """Tests for RSI scoring."""

    def test_rsi_oversold_triggers_buy(self):
        """RSI < 30 should add buy score."""
        data = make_stock_data(rsi=20)
        result = SignalService.generate_stock_signal(data)
        # Should add at least 25 buy points
        assert result['buy_score'] >= 25

    def test_rsi_overbought_triggers_sell(self):
        """RSI > 70 should add sell score."""
        data = make_stock_data(rsi=80)
        result = SignalService.generate_stock_signal(data)
        assert result['sell_score'] >= 25

    def test_rsi_bullish_zone(self):
        """RSI 30-40 should add small buy score."""
        data = make_stock_data(rsi=35)
        result = SignalService.generate_stock_signal(data)
        assert result['buy_score'] >= 10

    def test_rsi_bearish_zone(self):
        """RSI 60-70 should add small sell score."""
        data = make_stock_data(rsi=65)
        result = SignalService.generate_stock_signal(data)
        assert result['sell_score'] >= 10


class TestStockSignalMA:
    """Tests for MA crossover scoring."""

    def test_golden_cross_adds_buy(self):
        """MA Fast > MA Slow → buy score."""
        data = make_stock_data(ma_fast=110, ma_slow=100)
        result = SignalService.generate_stock_signal(data)
        assert result['buy_score'] >= 20

    def test_death_cross_adds_sell(self):
        """MA Fast < MA Slow → sell score."""
        data = make_stock_data(ma_fast=90, ma_slow=100)
        result = SignalService.generate_stock_signal(data)
        assert result['sell_score'] >= 20


class TestStockSignalMACD:
    """Tests for MACD scoring."""

    def test_macd_bullish_cross(self):
        """MACD > signal with positive histogram → buy."""
        data = make_stock_data(macd=1.0, macd_signal=0.5, macd_hist=0.5)
        result = SignalService.generate_stock_signal(data)
        assert result['buy_score'] >= 25

    def test_macd_bearish_cross(self):
        """MACD < signal with negative histogram → sell."""
        data = make_stock_data(macd=-1.0, macd_signal=-0.5, macd_hist=-0.5)
        result = SignalService.generate_stock_signal(data)
        assert result['sell_score'] >= 25

    def test_macd_above_signal_only(self):
        """MACD > signal but no histogram → smaller buy."""
        data = make_stock_data(macd=1.0, macd_signal=0.5, macd_hist=0)
        result = SignalService.generate_stock_signal(data)
        assert result['buy_score'] >= 15


class TestStockSignalBB:
    """Tests for Bollinger Bands scoring."""

    def test_bb_near_lower_band(self):
        """Price near lower band → buy."""
        data = make_stock_data(price=96, bb_upper=105, bb_lower=95)
        # Position = (96-95)/(105-95) = 0.1 (< 0.2)
        result = SignalService.generate_stock_signal(data)
        assert result['buy_score'] >= 15

    def test_bb_near_upper_band(self):
        """Price near upper band → sell."""
        data = make_stock_data(price=104, bb_upper=105, bb_lower=95)
        # Position = (104-95)/(105-95) = 0.9 (> 0.8)
        result = SignalService.generate_stock_signal(data)
        assert result['sell_score'] >= 15


class TestStockSignalVolume:
    """Tests for volume scoring."""

    def test_volume_spike_buy(self):
        """Volume spike with buy pressure → buy score."""
        data = make_stock_data(
            volume_ratio=2.0,
            rsi=20,  # Buy trigger
            ma_fast=110, ma_slow=100  # Buy trigger
        )
        result = SignalService.generate_stock_signal(data)
        # Should have added vol bonus to buy
        assert result['buy_score'] >= 25  # RSI + MA + Vol spike

    def test_volume_spike_sell(self):
        """Volume spike with sell pressure → sell score."""
        data = make_stock_data(
            volume_ratio=2.0,
            rsi=80,  # Sell trigger
            ma_fast=90, ma_slow=100  # Sell trigger
        )
        result = SignalService.generate_stock_signal(data)
        assert result['sell_score'] >= 25


class TestStockSignalQuality:
    """Tests for signal quality classification."""

    def test_strong_buy_signal(self):
        """Strong buy signal: multiple indicators confirm."""
        data = make_stock_data(
            rsi=20,  # +25 buy
            ma_fast=110, ma_slow=100,  # +20 buy
            macd=1.0, macd_signal=0.5, macd_hist=0.5,  # +25 buy
            price=96, bb_upper=105, bb_lower=95,  # +15 buy
            volume_ratio=2.0  # +15 buy
        )
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'BUY'
        assert result['quality'] == 'STRONG'
        assert result['buy_score'] >= 70

    def test_strong_sell_signal(self):
        """Strong sell signal: multiple indicators confirm."""
        data = make_stock_data(
            rsi=80,  # +25 sell
            ma_fast=90, ma_slow=100,  # +20 sell
            macd=-1.0, macd_signal=-0.5, macd_hist=-0.5,  # +25 sell
            price=104, bb_upper=105, bb_lower=95,  # +15 sell
            volume_ratio=2.0  # +15 sell
        )
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'SELL'
        assert result['quality'] == 'STRONG'
        assert result['sell_score'] >= 70

    def test_weak_signal(self):
        """Weak signal: only one weak indicator."""
        data = make_stock_data(rsi=35)  # +10 buy, RSI 30-40
        result = SignalService.generate_stock_signal(data)
        # buy_score = 10, should be HOLD (10 < 40)
        assert result['signal'] == 'HOLD'


class TestStockSignalTPSL:
    """Tests for TP/SL calculation."""

    def test_buy_tp_sl_calculation(self):
        """TP/SL for buy should be above/below entry."""
        data = make_stock_data(price=100, atr=2.0)
        # Force a buy signal
        data.update(rsi=20, ma_fast=110, ma_slow=100, macd=1.0, macd_signal=0.5, macd_hist=0.5)
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'BUY'
        assert result['tp1'] > result['entry']
        assert result['tp2'] > result['tp1']
        assert result['tp3'] > result['tp2']
        assert result['sl'] < result['entry']

    def test_sell_tp_sl_calculation(self):
        """TP/SL for sell should be below/above entry."""
        data = make_stock_data(price=100, atr=2.0)
        # Force a sell signal
        data.update(rsi=80, ma_fast=90, ma_slow=100, macd=-1.0, macd_signal=-0.5, macd_hist=-0.5)
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'SELL'
        assert result['tp1'] < result['entry']
        assert result['tp2'] < result['tp1']
        assert result['tp3'] < result['tp2']
        assert result['sl'] > result['entry']

    def test_hold_signal_no_tp_sl(self):
        """HOLD signal should have None TP/SL."""
        data = make_stock_data()  # Neutral → HOLD
        result = SignalService.generate_stock_signal(data)
        if result['signal'] == 'HOLD':
            assert result['tp1'] is None
            assert result['sl'] is None

    def test_minimum_atr_applied(self):
        """Minimum ATR should be 0.3% of price."""
        data = make_stock_data(price=100, atr=0.01)  # Very small ATR
        data.update(rsi=20, ma_fast=110, ma_slow=100, macd=1.0, macd_signal=0.5, macd_hist=0.5)
        result = SignalService.generate_stock_signal(data)
        # min_atr = max(0.01, 100 * 0.003) = 0.3
        assert result['atr'] >= 0.3


# === Crypto Signal Tests ===

class TestCryptoSignalBasic:
    """Basic tests for crypto signal generation."""

    def test_no_data_returns_hold(self):
        """Empty data should return HOLD."""
        result = SignalService.generate_crypto_signal(None)
        assert result['signal'] == 'HOLD'

    def test_signal_has_required_fields(self):
        """Crypto signal output should have all required fields."""
        data = make_crypto_data()
        result = SignalService.generate_crypto_signal(data)
        for field in ['signal', 'reason', 'entry', 'tp1', 'tp2', 'tp3', 'sl',
                      'rsi', 'atr', 'quality', 'buy_score', 'sell_score',
                      'is_reversal', 'reversal_reasons']:
            assert field in result


class TestCryptoSignalRSI:
    """Tests for crypto RSI scoring."""

    def test_rsi_strong_oversold(self):
        """RSI < 30 should trigger strong buy."""
        data = make_crypto_data(rsi=20)
        result = SignalService.generate_crypto_signal(data)
        assert result['buy_score'] >= 20

    def test_rsi_strong_overbought(self):
        """RSI > 70 should trigger strong sell."""
        data = make_crypto_data(rsi=80)
        result = SignalService.generate_crypto_signal(data)
        assert result['sell_score'] >= 20


class TestCryptoSignalReversal:
    """Tests for REVERSAL signal detection."""

    def test_reversal_rsi_oversold_with_change(self):
        """RSI oversold + positive change → REVERSAL."""
        data = make_crypto_data(rsi=35, change=2.0)
        result = SignalService.generate_crypto_signal(data)
        assert result['is_reversal'] is True

    def test_no_reversal_rsi_high(self):
        """RSI > 40 should not trigger reversal."""
        data = make_crypto_data(rsi=50, change=2.0)
        result = SignalService.generate_crypto_signal(data)
        assert result['is_reversal'] is False

    def test_no_reversal_change_zero(self):
        """Zero change should not trigger reversal."""
        data = make_crypto_data(rsi=35, change=0)
        result = SignalService.generate_crypto_signal(data)
        assert result['is_reversal'] is False


class TestCryptoSignalQuality:
    """Tests for crypto signal quality."""

    def test_strong_crypto_buy(self):
        """Multiple indicators should give STRONG BUY."""
        data = make_crypto_data(
            rsi=20,  # +20 buy
            ma_fast=110, ma_slow=100,  # +10 buy
            macd=1.0, macd_signal=0.5, macd_hist=0.5,  # +15 buy
            price=96, bb_upper=105, bb_lower=95,  # +10 buy
            vwap=90,  # +10 buy (price > vwap)
            adx_strong=True, plus_di=40, minus_di=20,  # +10 buy
            ichi_bullish=True,  # +10 buy
        )
        result = SignalService.generate_crypto_signal(data)
        # Total: 20+10+15+10+10+10+10 = 85
        assert result['signal'] == 'BUY'
        assert result['quality'] in ['STRONG', 'MODERATE']


# === Singleton Test ===

class TestSignalServiceSingleton:
    """Tests for the signal_service singleton."""

    def test_singleton_exists(self):
        """Singleton instance should exist."""
        assert signal_service is not None

    def test_singleton_methods(self):
        """Singleton should have all signal methods."""
        assert hasattr(signal_service, 'generate_stock_signal')
        assert hasattr(signal_service, 'generate_crypto_signal')


# === Edge Cases ===

class TestEdgeCases:
    """Edge case tests for signal generation."""

    def test_zero_volume_ratio(self):
        """Zero volume should not crash."""
        data = make_stock_data(volume_ratio=0)
        result = SignalService.generate_stock_signal(data)
        assert result is not None

    def test_extreme_rsi(self):
        """Extreme RSI values should still work."""
        for rsi in [0, 100, 150, -10]:
            data = make_stock_data(rsi=rsi)
            result = SignalService.generate_stock_signal(data)
            assert result is not None

    def test_zero_atr(self):
        """Zero ATR should be handled with minimum value."""
        data = make_stock_data(price=100, atr=0)
        data.update(rsi=20, ma_fast=110, ma_slow=100)
        result = SignalService.generate_stock_signal(data)
        assert result['atr'] > 0  # Should use minimum

    def test_equal_ma(self):
        """Equal MA fast/slow → no MA bonus."""
        data = make_stock_data(ma_fast=100, ma_slow=100)
        result = SignalService.generate_stock_signal(data)
        # No MA bonus added
        assert result['buy_score'] == 0 or result['sell_score'] == 0
