"""
Signal generation service for stocks and crypto.
"""
import logging

logger = logging.getLogger(__name__)


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
    def generate_stock_signal(data):
        """Generate stock signal using weighted multi-indicator scoring system"""
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
        bb_middle = data.get('bb_middle', price)
        volume_ratio = data.get('volume_ratio', 1.0)

        buy_score = 0
        sell_score = 0
        reasons = []

        # RSI Score (weight: 25%)
        if rsi < 30:
            buy_score += 25
            reasons.append(f'RSI {rsi:.0f} oversold')
        elif rsi < 40:
            buy_score += 10
            reasons.append(f'RSI {rsi:.0f} bullish')
        elif rsi > 70:
            sell_score += 25
            reasons.append(f'RSI {rsi:.0f} overbought')
        elif rsi > 60:
            sell_score += 10
            reasons.append(f'RSI {rsi:.0f} bearish')

        # MA Score (weight: 20%)
        if ma_f > ma_s:
            buy_score += 20
            reasons.append('MA golden cross')
        elif ma_f < ma_s:
            sell_score += 20
            reasons.append('MA death cross')

        # MACD Score (weight: 25%)
        if macd > macd_signal and macd_hist > 0:
            buy_score += 25
            reasons.append('MACD bullish cross')
        elif macd > macd_signal:
            buy_score += 15
            reasons.append('MACD above signal')
        elif macd < macd_signal and macd_hist < 0:
            sell_score += 25
            reasons.append('MACD bearish cross')
        elif macd < macd_signal:
            sell_score += 15
            reasons.append('MACD below signal')

        # Bollinger Bands Score (weight: 15%)
        bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        if bb_position < 0.2:
            buy_score += 15
            reasons.append('BB near lower band')
        elif bb_position > 0.8:
            sell_score += 15
            reasons.append('BB near upper band')

        # Volume Score (weight: 15%)
        if volume_ratio > 1.5:
            if buy_score > sell_score:
                buy_score += 15
                reasons.append(f'Vol spike {volume_ratio:.1f}x')
            else:
                sell_score += 15
                reasons.append(f'Vol spike {volume_ratio:.1f}x')
        elif volume_ratio > 1.2:
            if buy_score > sell_score:
                buy_score += 8
            else:
                sell_score += 8

        # Determine signal
        signal = 'HOLD'
        quality = 'WEAK'

        if buy_score >= 55:
            signal = 'BUY'
            quality = 'STRONG' if buy_score >= 70 else 'MODERATE'
        elif sell_score >= 55:
            signal = 'SELL'
            quality = 'STRONG' if sell_score >= 70 else 'MODERATE'
        elif buy_score >= 40 and buy_score > sell_score:
            signal = 'BUY'
            quality = 'WEAK'
        elif sell_score >= 40 and sell_score > buy_score:
            signal = 'SELL'
            quality = 'WEAK'

        # Calculate TP/SL
        min_atr = max(atr, price * 0.003)
        effective_atr = min_atr

        if signal == 'BUY':
            sl = price - (2 * effective_atr)
            tp1 = price + (1 * effective_atr)
            tp2 = price + (2 * effective_atr)
            tp3 = price + (3 * effective_atr)
        elif signal == 'SELL':
            sl = price + (2 * effective_atr)
            tp1 = price - (1 * effective_atr)
            tp2 = price - (2 * effective_atr)
            tp3 = price - (3 * effective_atr)
        else:
            sl = tp1 = tp2 = tp3 = None

        return {
            'signal': signal,
            'reason': ', '.join(reasons) if reasons else 'No signal',
            'entry': price,
            'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
            'sl': sl,
            'rsi': rsi,
            'atr': effective_atr,
            'quality': quality,
            'buy_score': buy_score,
            'sell_score': sell_score,
            'macd_hist': macd_hist,
            'volume_ratio': volume_ratio
        }

    @staticmethod
    def generate_crypto_signal(data):
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

        # MA Score (weight: 10%)
        if ma_f > ma_s:
            buy_score += 10
            reasons.append('MA golden cross')
        elif ma_f < ma_s:
            sell_score += 10
            reasons.append('MA death cross')

        # MACD Score (weight: 15%)
        if macd > macd_signal and macd_hist > 0:
            buy_score += 15
            reasons.append('MACD bullish')
        elif macd > macd_signal:
            buy_score += 8
            reasons.append('MACD above signal')
        elif macd < macd_signal and macd_hist < 0:
            sell_score += 15
            reasons.append('MACD bearish')
        elif macd < macd_signal:
            sell_score += 8
            reasons.append('MACD below signal')

        # Bollinger Bands Score (weight: 10%)
        bb_position = (price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        if bb_position < 0.2:
            buy_score += 10
            reasons.append('BB near lower')
        elif bb_position > 0.8:
            sell_score += 10
            reasons.append('BB near upper')

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

        # Crypto TP/SL: wider for volatile market
        effective_atr = max(atr, price * 0.005)

        if signal == 'BUY':
            sl = price - (2 * effective_atr)
            tp1 = price + (1 * effective_atr)
            tp2 = price + (2 * effective_atr)
            tp3 = price + (3 * effective_atr)
        elif signal == 'SELL':
            sl = price + (2 * effective_atr)
            tp1 = price - (1 * effective_atr)
            tp2 = price - (2 * effective_atr)
            tp3 = price - (3 * effective_atr)
        else:
            sl = tp1 = tp2 = tp3 = None

        return {
            'signal': signal,
            'reason': ', '.join(reasons) if reasons else 'No signal',
            'entry': price,
            'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
            'sl': sl,
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
