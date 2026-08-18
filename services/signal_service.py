"""
Signal generation service for stocks and crypto.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

# Type alias for signal dict
SignalDict = Dict[str, Any]
StockDataDict = Dict[str, Any]


# TP/SL multipliers by timeframe category
# Scalping: 1m, 5m — tight targets, frequent checks
# Intraday: 15m, 30m — moderate targets
# Swing: 60m (1h), 240m (4h), 1440m (1d) — wider targets, less frequent checks
TF_TPSL_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    'scalping': {'sl': 2.0, 'tp1': 1.0, 'tp2': 2.0, 'tp3': 3.0},   # 1m, 5m
    'intraday': {'sl': 2.5, 'tp1': 1.5, 'tp2': 3.0, 'tp3': 5.0},  # 15m, 30m
    'swing': {'sl': 3.0, 'tp1': 2.0, 'tp2': 4.0, 'tp3': 7.0},     # 1h, 4h, 1d
}


def get_tPSL_multipliers(timeframe: str) -> Dict[str, float]:
    """Get TP/SL multipliers based on timeframe."""
    if timeframe in ('1', '5'):
        return TF_TPSL_MULTIPLIERS['scalping']
    elif timeframe in ('15', '30'):
        return TF_TPSL_MULTIPLIERS['intraday']
    else:  # '60', '240', '1440' and default
        return TF_TPSL_MULTIPLIERS['swing']


def calc_tPSL(signal: str, price: float, atr: float, timeframe: str = '5') -> Dict[str, Optional[float]]:
    """Calculate TP/SL with timeframe-adjusted multipliers."""
    m = get_tPSL_multipliers(timeframe)
    if signal == 'BUY':
        return {
            'sl': price - (m['sl'] * atr),
            'tp1': price + (m['tp1'] * atr),
            'tp2': price + (m['tp2'] * atr),
            'tp3': price + (m['tp3'] * atr),
        }
    elif signal == 'SELL':
        return {
            'sl': price + (m['sl'] * atr),
            'tp1': price - (m['tp1'] * atr),
            'tp2': price - (m['tp2'] * atr),
            'tp3': price - (m['tp3'] * atr),
        }
    return {'sl': None, 'tp1': None, 'tp2': None, 'tp3': None}


# === Shared Scoring Helpers ===


def score_rsi(
    rsi: float,
    buy_thresholds: tuple[float, float] = (30, 40),
    sell_thresholds: tuple[float, float] = (70, 60),
    buy_pts: tuple[int, int] = (25, 10),
    sell_pts: tuple[int, int] = (25, 10)
) -> tuple[int, int, str | None]:
    """Score RSI indicator. Returns (buy_score, sell_score, reason_or_None)."""
    buy_score = sell_score = 0
    reason: str | None = None
    buy_lo, buy_hi = buy_thresholds
    sell_lo, sell_hi = sell_thresholds
    buy_full, buy_partial = buy_pts
    sell_full, sell_partial = sell_pts
    if rsi < buy_lo:
        buy_score = buy_full
        reason = f'RSI {rsi:.0f} oversold'
    elif rsi < buy_hi:
        buy_score = buy_partial
        reason = f'RSI {rsi:.0f} bullish'
    elif rsi > sell_lo:
        sell_score = sell_full
        reason = f'RSI {rsi:.0f} overbought'
    elif rsi > sell_hi:
        sell_score = sell_partial
        reason = f'RSI {rsi:.0f} bearish'
    return buy_score, sell_score, reason


def score_ma(ma_fast: float, ma_slow: float) -> tuple:
    """Score MA crossover. Returns (buy_score, sell_score, reason_or_None)."""
    buy_score = sell_score = 0
    reason = None
    if ma_fast > ma_slow:
        buy_score = 20
        reason = 'MA golden cross'
    elif ma_fast < ma_slow:
        sell_score = 20
        reason = 'MA death cross'
    return buy_score, sell_score, reason


def score_macd(
    macd: float,
    macd_signal: float,
    macd_hist: float,
    weights: tuple[int, int] = (25, 15)
) -> tuple[int, int, str | None]:
    """Score MACD. weights = (cross_pts, above_pts)."""
    buy_score: int = 0
    sell_score: int = 0
    reason: str | None = None
    cross, above = weights
    if macd > macd_signal and macd_hist > 0:
        buy_score = cross
        reason = 'MACD bullish cross'
    elif macd > macd_signal:
        buy_score = above
        reason = 'MACD above signal'
    elif macd < macd_signal and macd_hist < 0:
        sell_score = cross
        reason = 'MACD bearish cross'
    elif macd < macd_signal:
        sell_score = above
        reason = 'MACD below signal'
    return buy_score, sell_score, reason



def score_bb(
    price: float,
    bb_upper: float,
    bb_lower: float,
    weight: int = 15
) -> tuple[int, int, str | None]:
    """Score Bollinger Bands position. Returns (buy_score, sell_score, reason_or_None)."""
    buy_score: int = 0
    sell_score: int = 0
    reason: str | None = None
    spread = bb_upper - bb_lower
    pos = (price - bb_lower) / spread if spread > 0 else 0.5
    if pos < 0.2:
        buy_score = weight
        reason = 'BB near lower band'
    elif pos > 0.8:
        sell_score = weight
        reason = 'BB near upper band'
    return buy_score, sell_score, reason


def score_volume(
    volume_ratio: float,
    buy_score: int,
    sell_score: int,
    spike_pts: int = 15,
    moderate_pts: int = 8,
    spike_thresh: float = 1.5,
    moderate_thresh: float = 1.2
) -> tuple[int, int, str | None]:
    """Score volume. Returns (buy_added, sell_added, reason)."""
    """Score volume. Adjusts based on current direction bias."""
    added_buy = added_sell = 0
    reason: str | None = None
    bias = 1 if buy_score > sell_score else -1 if sell_score > buy_score else 0
    added_buy: int = 0
    added_sell: int = 0
    if volume_ratio > spike_thresh:
        added_buy = spike_pts if bias >= 0 else 0
        added_sell = spike_pts if bias <= 0 else 0
        if added_buy or added_sell:
            reason = f'Vol spike {volume_ratio:.1f}x'
    elif volume_ratio > moderate_thresh:
        added_buy = moderate_pts if bias >= 0 else 0
        added_sell = moderate_pts if bias <= 0 else 0
    return added_buy, added_sell, reason


def determine_signal(
    buy_score: int,
    sell_score: int,
    buy_threshold: int = 55,
    sell_threshold: int = 55,
    weak_threshold: int = 40
) -> tuple[str, str]:
                    #
    """Determine signal type and quality from scores. Returns (signal, quality)."""
    if buy_score >= buy_threshold:
        return 'BUY', 'STRONG' if buy_score >= 70 else 'MODERATE'
    if sell_score >= sell_threshold:
        return 'SELL', 'STRONG' if sell_score >= 70 else 'MODERATE'
    if buy_score >= weak_threshold and buy_score > sell_score:
        return 'BUY', 'WEAK'
    if sell_score >= weak_threshold and sell_score > buy_score:
        return 'SELL', 'WEAK'
    return 'HOLD', 'WEAK'


def detect_patterns_from_data(data):
    """
    Detect chart patterns from stock/crypto data.
    Returns pattern information if patterns are found.
    """
    try:
        from utils.patterns import detect_all_patterns
        import pandas as pd

        # Create DataFrame from data if raw data is available
        if 'raw_df' in data:
            df = data['raw_df']
        elif 'candles' in data and data['candles'] > 0:
            # We need OHLCV data for pattern detection
            return None
        else:
            return None

        # Detect all patterns
        patterns = detect_all_patterns(df)

        if patterns and patterns.get('patterns_found', 0) > 0:
            return {
                'patterns_found': patterns['patterns_found'],
                'strongest': patterns.get('strongest_pattern'),
                'summary': patterns.get('pattern_summary', ''),
                'bullish_count': len(patterns.get('bullish_patterns', [])),
                'bearish_count': len(patterns.get('bearish_patterns', []))
            }

        return None
    except Exception as e:
        logger.debug(f"Pattern detection error: {e}")
        return None


class SignalService:
    """Service for generating trading signals"""

    @staticmethod
    def generate_stock_signal(data: Optional[StockDataDict]) -> SignalDict:
        """Generate stock signal using weighted multi-indicator scoring system."""
        if not data:
            return {'signal': 'HOLD', 'reason': 'No data'}

        price = data['price']
        rsi = data.get('rsi', 50)
        ma_f = data.get('ma_fast', price)
        ma_s = data.get('ma_slow', price)
        atr = data.get('atr', price * 0.015)
        macd = data.get('macd', 0)
        macd_signal = data.get('macd_signal', 0)
        macd_hist = data.get('macd_hist', 0)
        bb_upper = data.get('bb_upper', price * 1.05)
        bb_lower = data.get('bb_lower', price * 0.95)
        volume_ratio = data.get('volume_ratio', 1.0)

        buy_score = 0
        sell_score = 0
        reasons = []

        # RSI Score (weight: 25%)
        b, s, r = score_rsi(rsi, (30, 40), (70, 60), (25, 10), (25, 10))
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # MA Score (weight: 20%)
        b, s, r = score_ma(ma_f, ma_s)
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # MACD Score (weight: 25%)
        b, s, r = score_macd(macd, macd_signal, macd_hist, (25, 15))
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Bollinger Bands Score (weight: 15%)
        b, s, r = score_bb(price, bb_upper, bb_lower, 15)
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Volume Score (weight: 15%)
        b, s, r = score_volume(volume_ratio, buy_score, sell_score, 15, 8, 1.5, 1.2)
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Determine signal
        signal, quality = determine_signal(buy_score, sell_score)

        # Calculate TP/SL with timeframe-adjusted multipliers
        effective_atr = max(atr, price * 0.003)
        timeframe = data.get('timeframe', '5')
        tpsl = calc_tPSL(signal, price, effective_atr, timeframe)

        return {
            'signal': signal,
            'reason': ', '.join(reasons) if reasons else 'No signal',
            'entry': price,
            'tp1': tpsl['tp1'], 'tp2': tpsl['tp2'], 'tp3': tpsl['tp3'],
            'sl': tpsl['sl'],
            'rsi': rsi,
            'atr': effective_atr,
            'quality': quality,
            'buy_score': buy_score,
            'sell_score': sell_score,
            'macd_hist': macd_hist,
            'volume_ratio': volume_ratio
        }

    @staticmethod
    def generate_crypto_signal(data: Optional[StockDataDict]) -> SignalDict:
        """Generate crypto signal using weighted multi-indicator scoring system"""
        if not data:
            return {'signal': 'HOLD', 'reason': 'No data'}

        price = data['price']
        rsi = data.get('rsi', 50)
        ma_f = data.get('ma_fast', price)
        ma_s = data.get('ma_slow', price)
        atr = data.get('atr', price * 0.025)
        change = data.get('change', 0)

        macd = data.get('macd', 0)
        macd_signal = data.get('macd_signal', 0)
        macd_hist = data.get('macd_hist', 0)
        bb_upper = data.get('bb_upper', price * 1.05)
        bb_lower = data.get('bb_lower', price * 0.95)
        volume_ratio = data.get('volume_ratio', 1.0)
        vwap = data.get('vwap', price)
        stoch_k = data.get('stoch_k', 50)
        stoch_oversold = data.get('stoch_oversold', False)
        stoch_overbought = data.get('stoch_overbought', False)
        stoch_bullish_cross = data.get('stoch_bullish_cross', False)
        adx = data.get('adx', 25)
        plus_di = data.get('plus_di', 25)
        minus_di = data.get('minus_di', 25)
        adx_strong = data.get('adx_strong', False)
        ichi_bullish = data.get('ichi_bullish', False)
        ichi_bearish = data.get('ichi_bearish', False)
        ichi_cloud_above = data.get('ichi_cloud_above', False)

        buy_score = 0
        sell_score = 0
        reasons = []

        # RSI Score (weight: 20%) - INCREASED from 15%
        # Option A: More aggressive RSI oversold scoring
        if rsi < 30:
            buy_score += 20
            reasons.append(f'RSI {rsi:.0f} STRONG oversold')
        elif rsi < 35:
            buy_score += 15
            reasons.append(f'RSI {rsi:.0f} oversold')
        elif rsi < 45:
            buy_score += 10
            reasons.append(f'RSI {rsi:.0f} bullish')
        elif rsi < 50:
            buy_score += 5
            reasons.append(f'RSI {rsi:.0f} near oversold')
        elif rsi > 70:
            sell_score += 20
            reasons.append(f'RSI {rsi:.0f} STRONG overbought')
        elif rsi > 65:
            sell_score += 15
            reasons.append(f'RSI {rsi:.0f} overbought')
        elif rsi > 60:
            sell_score += 8
            reasons.append(f'RSI {rsi:.0f} bearish')

        # MA Score (weight: 10%) - same as stock
        b, s, r = score_ma(ma_f, ma_s)
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # MACD Score (weight: 15%)
        b, s, r = score_macd(macd, macd_signal, macd_hist, (15, 8))
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Bollinger Bands Score (weight: 10%)
        b, s, r = score_bb(price, bb_upper, bb_lower, 10)
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Stochastic Score (weight: 10%)
        if stoch_oversold:
            buy_score += 10
            reasons.append(f'Stoch {stoch_k:.0f} oversold')
        elif stoch_k < 30:
            buy_score += 5
        if stoch_bullish_cross:
            buy_score += 5
            reasons.append('Stoch bullish cross')
        if stoch_overbought:
            sell_score += 10
            reasons.append(f'Stoch {stoch_k:.0f} overbought')
        elif stoch_k > 70:
            sell_score += 5

        # VWAP Score (weight: 10%)
        if price > vwap:
            buy_score += 10
            reasons.append('Above VWAP')
        else:
            sell_score += 10
            reasons.append('Below VWAP')

        # ADX Score (weight: 10%)
        if adx_strong:
            if plus_di > minus_di:
                buy_score += 10
                reasons.append(f'ADX {adx:.0f} strong up')
            else:
                sell_score += 10
                reasons.append(f'ADX {adx:.0f} strong down')

        # Ichimoku Score (weight: 10%)
        if ichi_bullish:
            buy_score += 10
            reasons.append('Ichimoku bullish')
        if ichi_bearish:
            sell_score += 10
            reasons.append('Ichimoku bearish')

        # Volume + Momentum Score (weight: 10%)
        if change >= 3:
            if buy_score > sell_score:
                buy_score += 8
                reasons.append(f'Momentum +{change:.1f}%')
            else:
                sell_score += 8
                reasons.append(f'Momentum -{abs(change):.1f}%')

        if volume_ratio > 1.5:
            if buy_score > sell_score:
                buy_score += 2
                reasons.append(f'Vol {volume_ratio:.1f}x')
            else:
                sell_score += 2

        # Option B (Minor): REVERSAL signal detection
        # RSI oversold + price rising = potential reversal
        is_reversal = False
        reversal_reasons = []

        if rsi < 40 and change > 1:
            is_reversal = True
            reversal_reasons.append(f'RSI oversold ({rsi:.0f})')
            reversal_reasons.append(f'Harga naik +{change:.1f}%')
            if macd_hist > 0:
                reversal_reasons.append('MACD histogram positif')
            if stoch_oversold:
                reversal_reasons.append('Stochastic oversold')
            reversal_reasons.append('Momentum rising - potential rebound')
            reasons.append('REVERSAL CANDIDATE')

        # Determine signal
        signal = 'HOLD'
        quality = 'WEAK'

        # Check REVERSAL first - special signal type
        if is_reversal and signal == 'HOLD':
            signal = 'REVERSAL'
            quality = 'STRONG' if len(reversal_reasons) >= 3 else 'MODERATE'

        # Normal BUY/SELL signals
        if signal == 'HOLD':
            if buy_score >= 35:
                signal = 'BUY'
                quality = 'STRONG' if buy_score >= 60 else ('MODERATE' if buy_score >= 45 else 'WEAK')
            elif sell_score >= 35:
                signal = 'SELL'
                quality = 'STRONG' if sell_score >= 60 else ('MODERATE' if sell_score >= 45 else 'WEAK')
            elif buy_score >= 25 and buy_score > sell_score:
                signal = 'BUY'
                quality = 'WEAK'
            elif sell_score >= 25 and sell_score > buy_score:
                signal = 'SELL'
                quality = 'WEAK'

        # Crypto TP/SL: wider for volatile market, timeframe-adjusted
        effective_atr = max(atr, price * 0.005)
        timeframe = data.get('timeframe', '60')  # Crypto default 1h
        tpsl = calc_tPSL(signal, price, effective_atr, timeframe)

        return {
            'signal': signal,
            'reason': ', '.join(reasons) if reasons else 'No signal',
            'entry': price,
            'tp1': tpsl['tp1'], 'tp2': tpsl['tp2'], 'tp3': tpsl['tp3'],
            'sl': tpsl['sl'],
            'rsi': rsi,
            'atr': effective_atr,
            'quality': quality,
            'buy_score': buy_score,
            'sell_score': sell_score,
            'macd_hist': macd_hist,
            'volume_ratio': volume_ratio,
            'stoch_k': stoch_k,
            'adx': adx,
            'vwap': vwap,
            'ichi_bullish': ichi_bullish,
            'ichi_bearish': ichi_bearish,
            'fib_levels': data.get('fib_levels', {}),
            # Option B: REVERSAL signal info
            'is_reversal': is_reversal,
            'reversal_reasons': reversal_reasons,
        }


# Singleton instance
signal_service = SignalService()
