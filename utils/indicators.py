"""
Technical indicators calculation module.
"""
import pandas as pd
import numpy as np


def calculate_rsi(prices, period=14):
    """Calculate RSI (Relative Strength Index)"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(prices, fast=12, slow=26, signal=9):
    """Calculate MACD indicator"""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        'macd': macd_line,
        'signal': signal_line,
        'histogram': histogram
    }


def calculate_bollinger_bands(prices, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    middle = prices.rolling(period).mean()
    std = prices.rolling(period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return {
        'upper': upper,
        'middle': middle,
        'lower': lower
    }


def calculate_volume_metrics(volume, period=20):
    """Calculate volume metrics - volume ratio vs average"""
    volume_ma = volume.rolling(period).mean()
    current_volume = volume.iloc[-1]
    avg_volume = volume_ma.iloc[-1] if not volume_ma.empty else 1
    ratio = current_volume / avg_volume if avg_volume > 0 else 1
    return {
        'ma': volume_ma,
        'ratio': ratio,
        'is_spike': ratio > 1.5
    }


def calculate_vwap(high, low, close, volume, period=14):
    """Calculate VWAP (Volume Weighted Average Price)"""
    typical_price = (high + low + close) / 3
    cumulative_tp_vol = (typical_price * volume).rolling(period).sum()
    cumulative_vol = volume.rolling(period).sum()
    vwap = cumulative_tp_vol / cumulative_vol
    current_vwap = vwap.iloc[-1] if not vwap.empty else close.iloc[-1]
    return {
        'vwap': vwap,
        'current': current_vwap,
        'above': close.iloc[-1] > current_vwap if not close.empty else True
    }


def calculate_stochastic(high, low, close, k_period=14, d_period=3):
    """Calculate Stochastic Oscillator (%K and %D)"""
    lowest_low = low.rolling(k_period).min()
    highest_high = high.rolling(k_period).max()
    k = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d = k.rolling(d_period).mean()
    current_k = k.iloc[-1] if not k.empty else 50
    current_d = d.iloc[-1] if not d.empty else 50
    return {
        'k': k,
        'd': d,
        'k_current': current_k,
        'd_current': current_d,
        'oversold': current_k < 20,
        'overbought': current_k > 80,
        'bullish_cross': (k.iloc[-2] < d.iloc[-2]) and (k.iloc[-1] > d.iloc[-1]),
        'bearish_cross': (k.iloc[-2] > d.iloc[-2]) and (k.iloc[-1] < d.iloc[-1])
    }


def calculate_adx(high, low, close, period=14):
    """Calculate ADX (Average Directional Index)"""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0

    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr = tr.rolling(period).mean()

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(period).mean()

    current_adx = adx.iloc[-1] if not adx.empty else 25
    current_plus_di = plus_di.iloc[-1] if not plus_di.empty else 25
    current_minus_di = minus_di.iloc[-1] if not minus_di.empty else 25

    return {
        'adx': adx,
        'plus_di': plus_di,
        'minus_di': minus_di,
        'adx_current': current_adx,
        'plus_di_current': current_plus_di,
        'minus_di_current': current_minus_di,
        'strong_trend': current_adx > 25,
        'bullish': current_plus_di > current_minus_di,
        'bearish': current_minus_di > current_plus_di
    }


def calculate_ichimoku(high, low, close, period=9, period2=26, period3=52):
    """Calculate Ichimoku Cloud components"""
    tenkan = (high.rolling(period).max() + low.rolling(period).min()) / 2
    kijun = (high.rolling(period2).max() + low.rolling(period2).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(period2)
    senkou_b = ((high.rolling(period3).max() + low.rolling(period3).min()) / 2).shift(period2)
    chikou = close.shift(-period2)

    current_tenkan = tenkan.iloc[-1] if not tenkan.empty else close.iloc[-1]
    current_kijun = kijun.iloc[-1] if not kijun.empty else close.iloc[-1]
    current_senkou_a = senkou_a.iloc[-1] if not senkou_a.empty else close.iloc[-1]
    current_senkou_b = senkou_b.iloc[-1] if not senkou_b.empty else close.iloc[-1]

    price = close.iloc[-1]
    cloud_above = price > max(current_senkou_a, current_senkou_b)
    cloud_below = price < min(current_senkou_a, current_senkou_b)

    return {
        'tenkan': tenkan,
        'kijun': kijun,
        'senkou_a': senkou_a,
        'senkou_b': senkou_b,
        'chikou': chikou,
        'tenkan_current': current_tenkan,
        'kijun_current': current_kijun,
        'senkou_a_current': current_senkou_a,
        'senkou_b_current': current_senkou_b,
        'tenkan_above_kijun': current_tenkan > current_kijun,
        'tenkan_below_kijun': current_tenkan < current_kijun,
        'cloud_above': cloud_above,
        'cloud_below': cloud_below,
        'bullish_cloud': cloud_above and current_tenkan > current_kijun,
        'bearish_cloud': cloud_below and current_tenkan < current_kijun
    }


def calculate_fibonacci_retracement(high, low, period=50):
    """Calculate Fibonacci retracement levels"""
    highest = high.rolling(period).max().iloc[-1]
    lowest = low.rolling(period).min().iloc[-1]
    diff = highest - lowest

    levels = {
        '0.0': lowest,
        '23.6': lowest + (diff * 0.236),
        '38.2': lowest + (diff * 0.382),
        '50.0': lowest + (diff * 0.500),
        '61.8': lowest + (diff * 0.618),
        '78.6': lowest + (diff * 0.786),
        '100.0': highest
    }

    return {
        'high': highest,
        'low': lowest,
        'levels': levels
    }


def calculate_atr(high, low, close, period=14):
    """Calculate Average True Range"""
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_indicators(df):
    """Calculate all advanced indicators for a DataFrame"""
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    indicators = {}

    # MACD
    macd_data = calculate_macd(close)
    indicators['macd'] = {'value': macd_data['macd'].iloc[-1],
                          'signal': macd_data['signal'].iloc[-1],
                          'hist': macd_data['histogram'].iloc[-1]}
    indicators['macd_histogram'] = 'POSITIVE' if macd_data['histogram'].iloc[-1] > 0 else 'NEGATIVE'

    # Bollinger Bands
    bb_data = calculate_bollinger_bands(close)
    indicators['bb'] = {'upper': bb_data['upper'].iloc[-1],
                         'middle': bb_data['middle'].iloc[-1],
                         'lower': bb_data['lower'].iloc[-1]}
    current_price = close.iloc[-1]
    if current_price > bb_data['upper'].iloc[-1]:
        indicators['bb_position'] = 'ABOVE'
    elif current_price < bb_data['lower'].iloc[-1]:
        indicators['bb_position'] = 'BELOW'
    elif current_price > bb_data['middle'].iloc[-1]:
        indicators['bb_position'] = 'UPPER_HALF'
    else:
        indicators['bb_position'] = 'LOWER_HALF'

    # ATR
    atr_data = calculate_atr(high, low, close)
    indicators['atr'] = atr_data.iloc[-1]
    indicators['atr_pct'] = (indicators['atr'] / current_price) * 100

    # VWAP
    vwap_data = calculate_vwap(high, low, close, volume)
    indicators['vwap'] = vwap_data['current']
    indicators['vwap_vs_price'] = 'ABOVE' if current_price > vwap_data['current'] else 'BELOW'

    # Stochastic
    stoch_data = calculate_stochastic(high, low, close)
    indicators['stoch_k'] = stoch_data['k_current']
    indicators['stoch_d'] = stoch_data['d_current']
    if stoch_data['k_current'] > 80:
        indicators['stoch_signal'] = 'OVERBOUGHT'
    elif stoch_data['k_current'] < 20:
        indicators['stoch_signal'] = 'OVERSOLD'
    else:
        indicators['stoch_signal'] = 'NEUTRAL'

    # RSI
    rsi_data = calculate_rsi(close)
    indicators['rsi'] = rsi_data.iloc[-1] if not rsi_data.empty else 50

    # ADX
    adx_data = calculate_adx(high, low, close)
    indicators['adx'] = adx_data['adx_current']
    indicators['adx_signal'] = 'STRONG_TREND' if adx_data['adx_current'] > 25 else 'WEAK_TREND'

    # Volume
    vol_avg = volume.rolling(20).mean().iloc[-1]
    indicators['volume_now'] = volume.iloc[-1]
    indicators['volume_avg'] = vol_avg
    indicators['volume_ratio'] = volume.iloc[-1] / vol_avg if vol_avg > 0 else 1

    return indicators
