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
    calc_tPSL,
    get_tPSL_multipliers,
    score_rsi, score_ma, score_macd, score_bb, score_volume, score_htf_trend,
    determine_signal,
    cap_quality,
    set_features, FEATURES,
)


@pytest.fixture(autouse=True)
def _restore_features():
    """Setiap tes dapat flag fitur default, apapun yang diubah tes lain."""
    old = dict(FEATURES)
    yield
    set_features(**old)


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


class TestTimeframeAwareTPSL:
    """Tests for timeframe-aware TP/SL multipliers."""

    def test_scalping_multipliers(self):
        """Scalping (1m, 5m) should have tightest targets."""
        m = get_tPSL_multipliers('1')
        assert m['sl'] == 2.0
        assert m['tp1'] == 1.0
        assert m['tp2'] == 2.0
        assert m['tp3'] == 3.0

        m = get_tPSL_multipliers('5')
        assert m == get_tPSL_multipliers('1')

    def test_intraday_multipliers(self):
        """Intraday (15m, 30m) should have moderate targets."""
        m = get_tPSL_multipliers('15')
        assert m['sl'] == 2.5
        assert m['tp1'] == 1.5
        assert m['tp2'] == 3.0
        assert m['tp3'] == 5.0

        m = get_tPSL_multipliers('30')
        assert m == get_tPSL_multipliers('15')

    def test_swing_multipliers(self):
        """Swing (1h, 4h, 1d) should have widest targets."""
        m = get_tPSL_multipliers('60')
        assert m['sl'] == 3.0
        assert m['tp1'] == 2.0
        assert m['tp2'] == 4.0
        assert m['tp3'] == 7.0

        assert get_tPSL_multipliers('240') == m
        assert get_tPSL_multipliers('1440') == m

    def test_calc_tpsl_buy_scalping(self):
        """Scalping BUY should have tight targets."""
        tpsl = calc_tPSL('BUY', 100, 1.0, '5')
        assert tpsl['sl'] == 98.0    # 100 - 2.0
        assert tpsl['tp1'] == 101.0   # 100 + 1.0
        assert tpsl['tp2'] == 102.0   # 100 + 2.0
        assert tpsl['tp3'] == 103.0   # 100 + 3.0

    def test_calc_tpsl_buy_swing(self):
        """Swing BUY should have wider targets."""
        tpsl = calc_tPSL('BUY', 100, 1.0, '60')
        assert tpsl['sl'] == 97.0     # 100 - 3.0
        assert tpsl['tp1'] == 102.0   # 100 + 2.0
        assert tpsl['tp2'] == 104.0   # 100 + 4.0
        assert tpsl['tp3'] == 107.0   # 100 + 7.0

    def test_calc_tpsl_sell(self):
        """SELL TP/SL should be below entry."""
        tpsl = calc_tPSL('SELL', 100, 1.0, '5')
        assert tpsl['sl'] == 102.0    # 100 + 2.0
        assert tpsl['tp1'] == 99.0    # 100 - 1.0
        assert tpsl['tp2'] == 98.0    # 100 - 2.0
        assert tpsl['tp3'] == 97.0    # 100 - 3.0

    def test_calc_tpsl_hold(self):
        """HOLD should return None for all targets."""
        tpsl = calc_tPSL('HOLD', 100, 1.0, '5')
        assert tpsl['sl'] is None
        assert tpsl['tp1'] is None
        assert tpsl['tp2'] is None
        assert tpsl['tp3'] is None

    def test_default_timeframe(self):
        """Unknown timeframe should default to swing (safest option)."""
        tpsl = calc_tPSL('BUY', 100, 1.0, '999')
        assert tpsl['sl'] == 97.0  # Same as swing (wider protection)



class TestScoringHelpers:
    """Tests for shared scoring helper functions."""

    def test_score_rsi_oversold(self):
        """RSI < 30 should give full buy score."""
        b, s, r = score_rsi(25)
        assert b == 25
        assert s == 0
        assert 'oversold' in r

    def test_score_rsi_overbought(self):
        """RSI > 70 should give full sell score."""
        b, s, r = score_rsi(75)
        assert b == 0
        assert s == 25
        assert 'overbought' in r

    def test_score_rsi_neutral(self):
        """RSI in middle should give 0."""
        b, s, r = score_rsi(50)
        assert b == 0
        assert s == 0
        assert r is None

    def test_score_ma_golden_cross(self):
        """MA fast > MA slow = golden cross."""
        b, s, r = score_ma(110, 100)
        assert b == 20
        assert r == 'MA golden cross'

    def test_score_ma_death_cross(self):
        """MA fast < MA slow = death cross."""
        b, s, r = score_ma(90, 100)
        assert s == 20
        assert r == 'MA death cross'

    def test_score_macd_bullish_cross(self):
        """MACD > signal + positive hist = bullish cross."""
        b, s, r = score_macd(1.0, 0.5, 0.3)
        assert b == 25
        assert r == 'MACD bullish cross'

    def test_score_bb_lower_band(self):
        """Price near BB lower band = buy."""
        # pos = (91-90)/(100-90) = 0.1 < 0.2
        b, s, r = score_bb(91, 100, 90)
        assert b == 15
        assert r == 'BB near lower band'

    def test_score_bb_upper_band(self):
        """Price near BB upper band = sell."""
        # pos = (99-90)/(100-90) = 0.9 > 0.8
        b, s, r = score_bb(99, 100, 90)
        assert s == 15
        assert r == 'BB near upper band'

    def test_score_volume_spike_bullish(self):
        """Volume spike with bullish bias adds to buy score."""
        b, s, r = score_volume(2.0, 60, 30)
        assert b == 15
        assert 'Vol spike' in r

    def test_score_volume_spike_bearish(self):
        """Volume spike with bearish bias adds to sell score."""
        b, s, r = score_volume(2.0, 30, 60)
        assert s == 15
        assert 'Vol spike' in r

    def test_determine_signal_strong_buy(self):
        """Score >= 55 with >= 70 = STRONG BUY."""
        s, q = determine_signal(75, 30)
        assert s == 'BUY'
        assert q == 'STRONG'

    def test_determine_signal_moderate_buy(self):
        """Score 55-69 = MODERATE BUY."""
        s, q = determine_signal(60, 30)
        assert s == 'BUY'
        assert q == 'MODERATE'

    def test_determine_signal_weak_buy(self):
        """Score 40-54 = WEAK BUY."""
        s, q = determine_signal(45, 30)
        assert s == 'BUY'
        assert q == 'WEAK'

    def test_determine_signal_hold(self):
        """Both scores low = HOLD."""
        s, q = determine_signal(30, 30)
        assert s == 'HOLD'
        assert q == 'WEAK'


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


# === Regression: bias SELL dari VWAP yang hilang ===

class TestCryptoVwapBias:
    """VWAP tanpa data harus netral, bukan +10 poin SELL.

    Dulu default vwap = harga membuat `price > vwap` selalu False, jadi
    seluruh jalur scan CoinGecko (yang tidak punya VWAP) menerima +10
    poin SELL gratis - SELL lebih mudah nembak threshold dan BUY
    terbebani.
    """

    def test_missing_vwap_is_neutral(self):
        """Tanpa key vwap: tidak boleh ada +10 SELL dari VWAP."""
        data = make_crypto_data()      # vwap=100 = price → dulu selalu +10 SELL
        del data['vwap']
        result = SignalService.generate_crypto_signal(data)
        # Bandingkan dengan data yang sama tanpa komponen VWAP sama sekali
        assert result['sell_score'] == 0
        assert result['buy_score'] == 0

    def test_missing_vwap_not_scored_as_sell(self):
        """Sinyal buy murni tidak boleh menerima sell_score dari VWAP hilang."""
        data = make_crypto_data(rsi=20)
        del data['vwap']
        result = SignalService.generate_crypto_signal(data)
        assert result['sell_score'] == 0
        assert result['buy_score'] >= 20

    def test_present_vwap_still_scored(self):
        """VWAP yang benar-benar ada tetap dinilai seperti dulu."""
        data = make_crypto_data(vwap=90)
        result = SignalService.generate_crypto_signal(data)
        assert result['buy_score'] >= 10
        data = make_crypto_data(vwap=110)
        result = SignalService.generate_crypto_signal(data)
        assert result['sell_score'] >= 10

    def test_vwap_nan_is_neutral(self):
        """VWAP NaN (bukan None) juga harus netral."""
        data = make_crypto_data(vwap=float('nan'))
        result = SignalService.generate_crypto_signal(data)
        assert result['sell_score'] == 0
        assert result['buy_score'] == 0

    def test_feature_off_restores_classic_behavior(self):
        """Flag vwap_crypto=False mem reproduksi perilaku lama (A/B backtest)."""
        set_features(vwap_crypto=False)
        data = make_crypto_data()
        del data['vwap']
        result = SignalService.generate_crypto_signal(data)
        # Perilaku lama: vwap hilang = harga → +10 SELL
        assert result['sell_score'] == 10


# === Konfirmasi trend besar (HTF) ===

class TestHtfTrendScoring:
    """Sinyal searah trend besar dapat bonus, yang melawan kena penalti."""

    def test_crypto_aligned_buy_gets_bonus(self):
        data = make_crypto_data(rsi=20, htf_trend='up')
        result = SignalService.generate_crypto_signal(data)
        no_htf = SignalService.generate_crypto_signal({**data, 'htf_trend': None})
        assert result['buy_score'] == no_htf['buy_score'] + 8

    def test_crypto_against_downtrend_gets_penalty(self):
        data = make_crypto_data(rsi=20, htf_trend='down')
        result = SignalService.generate_crypto_signal(data)
        no_htf = SignalService.generate_crypto_signal({**data, 'htf_trend': None})
        # Penalti masuk ke skor lawan (SELL) - efeknya BUY butuh bukti lebih
        assert result['sell_score'] == no_htf['sell_score'] + 8
        assert result['buy_score'] == no_htf['buy_score']

    def test_crypto_flat_is_neutral(self):
        data = make_crypto_data(rsi=20, htf_trend='flat')
        result = SignalService.generate_crypto_signal(data)
        no_htf = SignalService.generate_crypto_signal({**data, 'htf_trend': None})
        assert result['buy_score'] == no_htf['buy_score']

    def test_crypto_feature_off_disables_htf(self):
        set_features(htf_trend=False)
        data = make_crypto_data(rsi=20, htf_trend='up')
        result = SignalService.generate_crypto_signal(data)
        no_htf = SignalService.generate_crypto_signal({**data, 'htf_trend': None})
        assert result['buy_score'] == no_htf['buy_score']

    def test_stock_aligned_sell_gets_bonus(self):
        data = make_stock_data(rsi=80, ma_fast=90, ma_slow=100, htf_trend='down')
        result = SignalService.generate_stock_signal(data)
        no_htf = SignalService.generate_stock_signal({**data, 'htf_trend': None})
        assert result['sell_score'] == no_htf['sell_score'] + 10

    def test_score_htf_trend_helper(self):
        assert score_htf_trend('up', 'BUY', 10, 10) == (10, 0, 'HTF uptrend aligned')
        assert score_htf_trend('down', 'SELL', 10, 10) == (0, 10, 'HTF downtrend aligned')
        # Melawan: poin jatuh ke sisi lawan
        assert score_htf_trend('up', 'SELL', 10, 10) == (10, 0, 'Against HTF uptrend')
        assert score_htf_trend('down', 'BUY', 10, 10) == (0, 10, 'Against HTF downtrend')
        assert score_htf_trend('flat', 'BUY', 10, 10) == (0, 0, None)
        assert score_htf_trend(None, 'BUY', 10, 10) == (0, 0, None)


# === Data tidak lengkap menurunkan kualitas ===

class TestDegradedDataCaps:
    """Sinyal dari data bolong tidak boleh menyebut dirinya STRONG."""

    def test_tradingview_style_degraded_capped_at_moderate(self):
        """MACD+BB hilang (jalur fallback TV): sinyal kuat pun max MODERATE."""
        data = make_stock_data(
            rsi=20, ma_fast=110, ma_slow=100,
            macd=None, macd_signal=None, macd_hist=None,
            bb_upper=None, bb_lower=None,
            volume_ratio=2.0, degraded=True,
            indicator_missing=['macd', 'bb'],
        )
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'BUY'
        assert result['quality'] == 'MODERATE'
        assert set(result['missing_indicators']) == {'macd', 'bb'}
        assert 'Limited data' in result['reason']

    def test_three_missing_capped_at_weak(self):
        data = make_stock_data(
            rsi=None, ma_fast=None, ma_slow=None, macd=None,
            macd_signal=None, macd_hist=None,
        )
        result = SignalService.generate_stock_signal(data)
        assert result['quality'] == 'WEAK'

    def test_nan_detected_as_missing(self):
        """NaN dari jalur Yahoo terhitung data hilang, bukan netral diam-diam."""
        data = make_stock_data(macd=float('nan'), macd_signal=float('nan'),
                               macd_hist=float('nan'))
        result = SignalService.generate_stock_signal(data)
        assert 'macd' in result['missing_indicators']

    def test_complete_data_not_capped(self):
        data = make_stock_data(rsi=20, ma_fast=110, ma_slow=100,
                               macd=1.0, macd_signal=0.5, macd_hist=0.5,
                               price=96, bb_upper=105, bb_lower=95,
                               volume_ratio=2.0)
        result = SignalService.generate_stock_signal(data)
        assert result['quality'] == 'STRONG'
        assert result['missing_indicators'] == []

    def test_feature_off_disables_cap(self):
        # Data lengkap minus MACD: cap ON → MODERATE, cap OFF → STRONG.
        base = dict(rsi=20, ma_fast=110, ma_slow=100,
                    macd=None, macd_signal=None, macd_hist=None,
                    price=96, bb_upper=105, bb_lower=95,
                    volume_ratio=2.0)
        set_features(degraded_cap=False)
        result = SignalService.generate_stock_signal(dict(base))
        assert result['quality'] == 'STRONG'

        set_features(degraded_cap=True)
        result = SignalService.generate_stock_signal(dict(base))
        assert result['quality'] == 'MODERATE'

    def test_crypto_degraded_capped(self):
        data = make_crypto_data(rsi=20, ma_fast=110, ma_slow=100,
                                macd=None, macd_signal=None, macd_hist=None,
                                bb_upper=None, bb_lower=None)
        result = SignalService.generate_crypto_signal(data)
        assert result['quality'] in ('WEAK', 'MODERATE')
        assert 'macd' in result['missing_indicators']


# === Gate volatilitas ekstrem ===

class TestVolatilityGate:
    """ATR% ekstrem menurunkan kualitas sinyal."""

    def test_stock_extreme_vol_capped(self):
        data = make_stock_data(price=96, atr=8.0,  # 8.3% > 6%
                               rsi=20, ma_fast=110, ma_slow=100,
                               macd=1.0, macd_signal=0.5, macd_hist=0.5,
                               bb_upper=105, bb_lower=95,
                               volume_ratio=2.0)
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'BUY'
        assert result['quality'] == 'MODERATE'
        assert 'Extreme volatility' in result['reason']

    def test_crypto_extreme_vol_capped(self):
        data = make_crypto_data(price=100, atr=12.0,  # 12% > 10%
                                rsi=20, ma_fast=110, ma_slow=100,
                                macd=1.0, macd_signal=0.5, macd_hist=0.5,
                                vwap=90, adx_strong=True, plus_di=40,
                                minus_di=20, ichi_bullish=True)
        result = SignalService.generate_crypto_signal(data)
        assert result['quality'] != 'STRONG'

    def test_normal_vol_not_capped(self):
        data = make_crypto_data(price=100, atr=2.5)  # 2.5% aman
        result = SignalService.generate_crypto_signal(data)
        assert 'Extreme volatility' not in result['reason']

    def test_feature_off_disables_gate(self):
        set_features(vol_gate=False)
        data = make_stock_data(price=96, atr=8.0,
                               rsi=20, ma_fast=110, ma_slow=100,
                               macd=1.0, macd_signal=0.5, macd_hist=0.5,
                               bb_upper=105, bb_lower=95,
                               volume_ratio=2.0)
        result = SignalService.generate_stock_signal(data)
        assert result['quality'] == 'STRONG'


# === TP/SL sadar support/resistance ===

class TestCalcTPSLWithSR:
    """TP diklem di depan resistance, SL dipindah ke balik struktur."""

    def test_buy_tp_clamped_below_resistance(self):
        # Entry 100, ATR 1, swing: tp1=102, tp2=104, tp3=107.
        # Resistance di 105: tp3 menembus → diklem; tp1/tp2 di bawahnya: utuh.
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', resistance=105.0)
        cap = 105 * (1 - 0.003)
        assert tpsl['tp1'] == 102.0
        assert tpsl['tp2'] == 104.0          # memang di bawah resistance
        assert tpsl['tp3'] == cap
        assert tpsl['tp1'] < tpsl['tp2'] < tpsl['tp3']

    def test_buy_tp2_tp3_both_clamped_when_resistance_between(self):
        # Resistance di 103.5: tp2 dan tp3 sama-sama menembus → dua-duanya
        # diklem ke titik yang sama (sinyal: jangan berharap lebih).
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', resistance=103.5)
        cap = 103.5 * (1 - 0.003)
        assert tpsl['tp1'] == 102.0
        assert tpsl['tp2'] == cap
        assert tpsl['tp3'] == cap
        assert tpsl['tp1'] < tpsl['tp2'] <= tpsl['tp3']

    def test_buy_tp_untouched_when_resistance_far(self):
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', resistance=120.0)
        assert tpsl['tp2'] == 104.0
        assert tpsl['tp3'] == 107.0

    def test_buy_tp_untouched_when_resistance_too_close(self):
        # Resistance di bawah tp1: klem hanya akan meratakan semua target
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', resistance=101.0)
        assert tpsl['tp2'] == 104.0
        assert tpsl['tp3'] == 107.0

    def test_buy_sl_moves_behind_support(self):
        # ATR stop: 100 - 3 = 97. Support di 98.5 (1.5 ATR): SL pindah
        # sedikit di bawah support (lebih rapat, RR lebih baik).
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', support=98.5)
        assert tpsl['sl'] == pytest.approx(98.5 * (1 - 0.003))

    def test_buy_sl_kept_when_support_too_close(self):
        # Support hanya 0.3 ATR di bawah entry: dalam jangkauan noise,
        # tetap pakai stop ATR.
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', support=99.7)
        assert tpsl['sl'] == 97.0

    def test_buy_sl_kept_when_support_beyond_atr_stop(self):
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', support=90.0)
        assert tpsl['sl'] == 97.0

    def test_sell_tp_floored_above_support(self):
        # Entry 100, ATR 1, swing: tp1=98, tp2=96, tp3=93.
        # Support di 95: tp3 menembus ke bawah → dinaikkan ke lantai support.
        tpsl = calc_tPSL('SELL', 100, 1.0, '60', support=95.0)
        floor = 95 * (1 + 0.003)
        assert tpsl['tp1'] == 98.0
        assert tpsl['tp2'] == 96.0           # memang di atas support
        assert tpsl['tp3'] == floor
        assert tpsl['tp1'] > tpsl['tp2'] > tpsl['tp3']

    def test_sell_sl_moves_behind_resistance(self):
        # ATR stop: 100 + 3 = 103. Resistance di 101.5: SL pindah ke
        # sedikit di atas resistance.
        tpsl = calc_tPSL('SELL', 100, 1.0, '60', resistance=101.5)
        assert tpsl['sl'] == pytest.approx(101.5 * (1 + 0.003))

    def test_no_sr_matches_plain_result(self):
        with_sr = calc_tPSL('BUY', 100, 1.0, '60', support=None, resistance=None)
        plain = calc_tPSL('BUY', 100, 1.0, '60')
        assert with_sr == plain

    def test_sr_feature_off_disables_adjustment(self):
        set_features(sr_tpsl=False)
        tpsl = calc_tPSL('BUY', 100, 1.0, '60', resistance=105.0)
        assert tpsl['tp3'] == 107.0

    def test_signal_output_uses_sr(self):
        """Mesin sinyal meneruskan support/resistance data ke TP/SL."""
        # Support 98.5 dari entry 100 = 1 ATR (default tf '5', sl 2 ATR = 97):
        # dalam band struktur → SL pindah sedikit di bawah support.
        data = make_stock_data(rsi=20, ma_fast=110, ma_slow=100,
                               macd=1.0, macd_signal=0.5, macd_hist=0.5,
                               support=98.5)
        result = SignalService.generate_stock_signal(data)
        assert result['signal'] == 'BUY'
        assert result['sl'] == pytest.approx(98.5 * (1 - 0.003))


# === Helper cap_quality ===

class TestCapQuality:
    def test_cap_lowering(self):
        assert cap_quality('STRONG', 'MODERATE') == 'MODERATE'
        assert cap_quality('MODERATE', 'WEAK') == 'WEAK'
        assert cap_quality('WEAK', 'MODERATE') == 'WEAK'   # cap tidak menaikkan
        assert cap_quality('STRONG', None) == 'STRONG'
        assert cap_quality('MODERATE', 'MODERATE') == 'MODERATE'


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


# === Null Numeric Field Tests ===

class TestNullNumericFields:
    """Regression: API fields arriving as None must not break scoring.

    CoinGecko returns ``price_change_percentage_24h: null`` for some tokens
    (e.g. FIGR_HELOC / Figure Heloc).  ``dict.get(key, default)`` does not help
    when the key exists with a None value — the comparison then raises
    ``TypeError: '>=' not supported between instances of 'NoneType' and 'int'``.
    """

    def test_crypto_signal_with_null_change(self):
        """Null change should not raise and should be treated as 0%."""
        data = make_crypto_data(change=None)
        result = SignalService.generate_crypto_signal(data)
        assert result is not None
        assert result['signal'] in ('BUY', 'SELL', 'REVERSAL', 'HOLD')

    def test_crypto_signal_with_null_rsi(self):
        """Null RSI should fall back to neutral 50."""
        data = make_crypto_data(rsi=None)
        result = SignalService.generate_crypto_signal(data)
        assert result['rsi'] == 50

    def test_crypto_signal_with_null_volume_ratio(self):
        """Null volume_ratio should fall back to 1.0."""
        data = make_crypto_data(volume_ratio=None)
        result = SignalService.generate_crypto_signal(data)
        assert result is not None

    def test_crypto_signal_with_null_indicator_batch(self):
        """Several null indicator fields at once should still work."""
        data = make_crypto_data(
            change=None, rsi=None, atr=None, macd=None, macd_signal=None,
            macd_hist=None, bb_upper=None, bb_lower=None, volume_ratio=None,
            vwap=None, stoch_k=None, adx=None, plus_di=None, minus_di=None,
        )
        result = SignalService.generate_crypto_signal(data)
        assert result is not None
        assert result['entry'] == 100

    def test_stock_signal_with_null_indicators(self):
        """Stock signal should also tolerate null indicator fields."""
        data = make_stock_data(rsi=None, atr=None, macd=None, volume_ratio=None)
        result = SignalService.generate_stock_signal(data)
        assert result is not None
        assert result['signal'] in ('BUY', 'SELL', 'HOLD')
