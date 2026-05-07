"""
Chart Pattern Detection Module.
Detects Triangle, Channel, Wedge, and Harmonic patterns.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple


def find_swing_points(df: pd.DataFrame, lookback: int = 20) -> Tuple[np.ndarray, np.ndarray]:
    """Find swing highs and lows using simple price action"""
    highs = []
    lows = []

    for i in range(lookback, len(df) - lookback):
        # Check for swing high
        is_high = True
        for j in range(max(0, i - lookback), min(len(df), i + lookback + 1)):
            if j != i and df['High'].iloc[j] >= df['High'].iloc[i]:
                is_high = False
                break
        if is_high:
            highs.append((i, df['High'].iloc[i]))

        # Check for swing low
        is_low = True
        for j in range(max(0, i - lookback), min(len(df), i + lookback + 1)):
            if j != i and df['Low'].iloc[j] <= df['Low'].iloc[i]:
                is_low = False
                break
        if is_low:
            lows.append((i, df['Low'].iloc[i]))

    return highs, lows


def detect_triangle_patterns(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    """Detect triangle patterns: Symmetrical, Ascending, Descending"""
    patterns = []
    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values

    if len(df) < lookback:
        return patterns

    recent_highs = high[-lookback:]
    recent_lows = low[-lookback:]

    # Fit trendlines
    # Upper trendline (resistance) - connecting swing highs
    upper_points = []
    lower_points = []

    for i in range(1, lookback - 1):
        # Local high
        if high[i] > high[i-1] and high[i] > high[i+1]:
            upper_points.append((i, high[i]))
        # Local low
        if low[i] < low[i-1] and low[i] < low[i+1]:
            lower_points.append((i, low[i]))

    if len(upper_points) < 2 or len(lower_points) < 2:
        return patterns

    # Calculate slope of trendlines
    upper_slope = (upper_points[-1][1] - upper_points[0][1]) / (upper_points[-1][0] - upper_points[0][0] + 0.001)
    lower_slope = (lower_points[-1][1] - lower_points[0][1]) / (lower_points[-1][0] - lower_points[0][0] + 0.001)

    # Normalize slopes by price level
    price_level = np.mean(close[-lookback:])
    upper_slope_pct = upper_slope / price_level * 100
    lower_slope_pct = lower_slope / price_level * 100

    # Detect pattern type based on trendline convergence
    pattern_name = None
    description = None

    # Symmetrical Triangle: both lines converging, upper sloping down, lower sloping up
    if abs(upper_slope_pct) < 0.5 and abs(lower_slope_pct) < 0.5:
        # Check if they're converging
        upper_start = upper_points[0][1]
        lower_start = lower_points[0][1]
        upper_end = upper_points[-1][1]
        lower_end = lower_points[-1][1]

        upper_range_start = upper_start - lower_start
        upper_range_end = upper_end - lower_end

        if upper_range_start > upper_range_end * 1.2:  # Converging
            pattern_name = "Symmetrical Triangle"
            description = "Bouncing between converging support and resistance"

    # Ascending Triangle: flat upper, rising lower
    elif upper_slope_pct < 0.2 and lower_slope_pct > 0.3:
        pattern_name = "Ascending Triangle"
        description = "Flat resistance being tested, rising support"

    # Descending Triangle: falling upper, flat lower
    elif upper_slope_pct < -0.3 and lower_slope_pct > -0.1:
        pattern_name = "Descending Triangle"
        description = "Declining resistance, flat support being tested"

    if pattern_name:
        # Calculate pattern strength based on number of touches
        strength = min(len(upper_points), len(lower_points)) / 5.0
        strength = min(1.0, strength)

        patterns.append({
            'type': 'triangle',
            'name': pattern_name,
            'description': description,
            'strength': strength,
            'bullish': pattern_name in ["Ascending Triangle"],
            'bearish': pattern_name in ["Descending Triangle"],
            'neutral': pattern_name == "Symmetrical Triangle"
        })

    return patterns


def detect_channel_patterns(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    """Detect channel patterns: Uptrend, Downtrend, Ranging"""
    patterns = []
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values

    if len(df) < lookback:
        return patterns

    recent_highs = high[-lookback:]
    recent_lows = low[-lookback:]

    # Linear regression for trend
    x = np.arange(lookback)

    # Calculate trend slope for highs and lows
    high_slope, _ = np.polyfit(x, recent_highs, 1)
    low_slope, _ = np.polyfit(x, recent_lows, 1)

    price_level = np.mean(close[-lookback:])
    high_slope_pct = high_slope / price_level * 100
    low_slope_pct = low_slope / price_level * 100

    # Check parallelism
    slope_diff = abs(high_slope_pct - low_slope_pct)

    pattern_name = None
    description = None

    if slope_diff < 0.3:  # Parallel lines
        if high_slope_pct > 0.2 and low_slope_pct > 0.2:
            pattern_name = "Uptrend Channel"
            description = "Higher highs and higher lows, bullish continuation"
        elif high_slope_pct < -0.2 and low_slope_pct < -0.2:
            pattern_name = "Downtrend Channel"
            description = "Lower highs and lower lows, bearish continuation"
        elif abs(high_slope_pct) < 0.2 and abs(low_slope_pct) < 0.2:
            pattern_name = "Ranging Channel"
            description = "Horizontal movement, consolidation phase"

    if pattern_name:
        strength = min(len(recent_highs), len(recent_lows)) / 10.0
        strength = min(1.0, strength)

        patterns.append({
            'type': 'channel',
            'name': pattern_name,
            'description': description,
            'strength': strength,
            'bullish': pattern_name == "Uptrend Channel",
            'bearish': pattern_name == "Downtrend Channel",
            'neutral': pattern_name == "Ranging Channel"
        })

    return patterns


def detect_wedge_patterns(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    """Detect wedge patterns: Rising, Falling, Contracting, Expanding"""
    patterns = []
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values

    if len(df) < lookback:
        return patterns

    recent_highs = high[-lookback:]
    recent_lows = low[-lookback:]

    x = np.arange(lookback)

    # Calculate trend slopes
    high_slope, _ = np.polyfit(x, recent_highs, 1)
    low_slope, _ = np.polyfit(x, recent_lows, 1)

    price_level = np.mean(close[-lookback:])
    high_slope_pct = high_slope / price_level * 100
    low_slope_pct = low_slope / price_level * 100

    pattern_name = None
    description = None

    # Rising Wedge: both sloping up, but low slope > high slope (converging)
    if high_slope_pct > 0.1 and low_slope_pct > 0.1:
        if low_slope_pct > high_slope_pct:
            pattern_name = "Rising Wedge"
            description = "Converging upward, typically bearish reversal"
        else:
            pattern_name = "Expanding Triangle"
            description = "Diverging upward, potential reversal ahead"

    # Falling Wedge: both sloping down, but high slope < low slope (converging)
    elif high_slope_pct < -0.1 and low_slope_pct < -0.1:
        if high_slope_pct < low_slope_pct:
            pattern_name = "Falling Wedge"
            description = "Converging downward, typically bullish reversal"
        else:
            pattern_name = "Expanding Triangle"
            description = "Diverging downward, potential reversal ahead"

    # Contracting Triangle: both lines relatively flat but converging
    elif abs(high_slope_pct) < 0.3 and abs(low_slope_pct) < 0.3:
        # Check for convergence
        early_range = recent_highs[0] - recent_lows[0]
        late_range = recent_highs[-1] - recent_lows[-1]

        if early_range > late_range * 1.2:
            pattern_name = "Contracting Triangle"
            description = "Narrowing price range, breakout imminent"

    if pattern_name:
        strength = 0.7  # Default strength for wedge

        patterns.append({
            'type': 'wedge',
            'name': pattern_name,
            'description': description,
            'strength': strength,
            'bullish': pattern_name in ["Falling Wedge"],
            'bearish': pattern_name in ["Rising Wedge"],
            'neutral': pattern_name in ["Contracting Triangle", "Expanding Triangle"]
        })

    return patterns


def detect_harmonic_patterns(df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
    """Detect harmonic patterns: Gartley, Bat, Butterfly, Crab, etc."""
    patterns = []
    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values

    if len(df) < lookback:
        return patterns

    # Find recent swing points
    recent = df.iloc[-lookback:].copy()
    highs_idx = recent['High'].nlargest(5).index.tolist()
    lows_idx = recent['Low'].nsmallest(5).index.tolist()

    if len(highs_idx) < 3 or len(lows_idx) < 3:
        return patterns

    # Get price levels
    all_points = []
    for idx in highs_idx:
        all_points.append((idx, recent.loc[idx, 'High'], 'high'))
    for idx in lows_idx:
        all_points.append((idx, recent.loc[idx, 'Low'], 'low'))

    # Sort by index
    all_points.sort(key=lambda x: x[0])

    # Look for Gartley-like patterns (XABCD structure)
    # For simplicity, we'll detect based on Fibonacci ratios

    if len(all_points) >= 5:
        # Get the last 5 points (X, A, B, C, D)
        points = all_points[-5:]

        # Calculate legs
        X_A = abs(points[1][1] - points[0][1])
        A_B = abs(points[2][1] - points[1][1])
        B_C = abs(points[3][1] - points[2][1])
        C_D = abs(points[4][1] - points[3][1])

        if X_A > 0 and A_B > 0 and B_C > 0 and C_D > 0:
            # Calculate ratios
            AB_XA = A_B / X_A
            BC_AB = B_C / A_B
            CD_BC = C_D / B_C
            CD_XA = C_D / X_A

            # Gartley: AB = 0.618, BC = 0.382, CD = 0.786
            if 0.55 <= AB_XA <= 0.65 and 0.35 <= BC_AB <= 0.45 and 0.75 <= CD_XA <= 0.85:
                patterns.append({
                    'type': 'harmonic',
                    'name': 'Gartley',
                    'description': 'Classic harmonic pattern, CD leg completing at 0.786',
                    'strength': 0.8,
                    'bullish': True,
                    'bearish': False,
                    'neutral': False,
                    'ratios': {'AB_XA': round(AB_XA, 3), 'CD_XA': round(CD_XA, 3)}
                })

            # Bat: AB = 0.382, BC = 0.5, CD = 0.886
            elif 0.32 <= AB_XA <= 0.42 and 0.4 <= BC_AB <= 0.6 and 0.82 <= CD_XA <= 0.95:
                patterns.append({
                    'type': 'harmonic',
                    'name': 'Bat',
                    'description': 'Bat pattern with deep CD leg',
                    'strength': 0.85,
                    'bullish': True,
                    'bearish': False,
                    'neutral': False,
                    'ratios': {'AB_XA': round(AB_XA, 3), 'CD_XA': round(CD_XA, 3)}
                })

            # Butterfly: AB = 0.786, BC = 0.382, CD = 1.27
            elif 0.7 <= AB_XA <= 0.83 and 0.35 <= BC_AB <= 0.45 and 1.2 <= CD_XA <= 1.35:
                patterns.append({
                    'type': 'harmonic',
                    'name': 'Butterfly',
                    'description': 'Butterfly pattern with extended CD leg beyond X',
                    'strength': 0.75,
                    'bullish': False,
                    'bearish': True,
                    'neutral': False,
                    'ratios': {'AB_XA': round(AB_XA, 3), 'CD_XA': round(CD_XA, 3)}
                })

            # Crab: AB = 0.382, BC = 0.5, CD = 1.618
            elif 0.32 <= AB_XA <= 0.42 and 0.4 <= BC_AB <= 0.6 and 1.5 <= CD_XA <= 1.7:
                patterns.append({
                    'type': 'harmonic',
                    'name': 'Crab',
                    'description': 'Crab pattern with very deep CD extension',
                    'strength': 0.9,
                    'bullish': False,
                    'bearish': True,
                    'neutral': False,
                    'ratios': {'AB_XA': round(AB_XA, 3), 'CD_XA': round(CD_XA, 3)}
                })

            # Cypher: AB = 0.382, BC = 0.786, CD = 0.786
            elif 0.32 <= AB_XA <= 0.42 and 0.7 <= BC_AB <= 0.85 and 0.7 <= CD_XA <= 0.85:
                patterns.append({
                    'type': 'harmonic',
                    'name': 'Cypher',
                    'description': 'Cypher pattern with BC extension beyond B',
                    'strength': 0.75,
                    'bullish': True,
                    'bearish': False,
                    'neutral': False,
                    'ratios': {'AB_XA': round(AB_XA, 3), 'BC_AB': round(BC_AB, 3), 'CD_XA': round(CD_XA, 3)}
                })

            # Deep Crab: AB = 0.886
            elif 0.8 <= AB_XA <= 0.95 and 0.4 <= BC_AB <= 0.6 and 1.5 <= CD_XA <= 1.7:
                patterns.append({
                    'type': 'harmonic',
                    'name': 'Deep Crab',
                    'description': 'Deep Crab with AB at 0.886 retracement',
                    'strength': 0.85,
                    'bullish': False,
                    'bearish': True,
                    'neutral': False,
                    'ratios': {'AB_XA': round(AB_XA, 3), 'CD_XA': round(CD_XA, 3)}
                })

    return patterns


def detect_all_patterns(df: pd.DataFrame) -> Dict:
    """
    Detect all chart patterns and return comprehensive analysis.
    """
    all_patterns = []

    # Triangle patterns
    triangles = detect_triangle_patterns(df)
    all_patterns.extend(triangles)

    # Channel patterns
    channels = detect_channel_patterns(df)
    all_patterns.extend(channels)

    # Wedge patterns
    wedges = detect_wedge_patterns(df)
    all_patterns.extend(wedges)

    # Harmonic patterns
    harmonics = detect_harmonic_patterns(df)
    all_patterns.extend(harmonics)

    # Summary
    summary = {
        'patterns_found': len(all_patterns),
        'pattern_list': all_patterns,
        'bullish_patterns': [p for p in all_patterns if p.get('bullish')],
        'bearish_patterns': [p for p in all_patterns if p.get('bearish')],
        'neutral_patterns': [p for p in all_patterns if p.get('neutral')],
        'strongest_pattern': max(all_patterns, key=lambda x: x.get('strength', 0)) if all_patterns else None,
        'pattern_summary': format_pattern_summary(all_patterns)
    }

    return summary


def format_pattern_summary(patterns: List[Dict]) -> str:
    """Format pattern summary for display"""
    if not patterns:
        return "No clear patterns detected"

    summary_parts = []
    for p in patterns:
        emoji = "🟢" if p.get('bullish') else "🔴" if p.get('bearish') else "🟡"
        strength_bar = "█" * int(p.get('strength', 0) * 5)
        summary_parts.append(f"{emoji} {p['name']} ({strength_bar})")

    return " | ".join(summary_parts)


def get_pattern_emoji(pattern_name: str) -> str:
    """Get emoji for pattern type"""
    emojis = {
        'Symmetrical Triangle': '🔺',
        'Ascending Triangle': '📈',
        'Descending Triangle': '📉',
        'Uptrend Channel': '↗️',
        'Downtrend Channel': '↘️',
        'Ranging Channel': '↔️',
        'Rising Wedge': '🔺',
        'Falling Wedge': '🔻',
        'Contracting Triangle': '⏸️',
        'Expanding Triangle': '⏫',
        'Gartley': '🦋',
        'Bat': '🦇',
        'Butterfly': '🦋',
        'Crab': '🦀',
        'Deep Crab': '🦀',
        'Cypher': '🔐',
        'Shark': '🦈',
    }
    return emojis.get(pattern_name, '📊')
