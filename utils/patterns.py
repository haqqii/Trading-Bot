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


def detect_triangle_patterns(df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
    """Detect triangle patterns: Symmetrical, Ascending, Descending"""
    patterns = []
    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values

    if len(df) < lookback:
        return patterns

    price_level = np.mean(close[-lookback:])
    price_range_pct = (np.max(high[-lookback:]) - np.min(low[-lookback:])) / price_level * 100
    if price_range_pct < 1.5:
        return patterns

    x = np.arange(lookback)
    upper_coef = np.polyfit(x, high[-lookback:], 1)
    lower_coef = np.polyfit(x, low[-lookback:], 1)

    upper_slope_pct = upper_coef[0] / price_level * 100
    lower_slope_pct = lower_coef[0] / price_level * 100

    # Measure convergence: range at start vs end
    early_high = upper_coef[0] * 5 + upper_coef[1]
    early_low = lower_coef[0] * 5 + lower_coef[1]
    late_high = upper_coef[0] * (lookback - 5) + upper_coef[1]
    late_low = lower_coef[0] * (lookback - 5) + lower_coef[1]
    early_range = early_high - early_low
    late_range = late_high - late_low
    convergence = (early_range - late_range) / early_range if early_range > 0 else 0

    pattern_name = None
    description = None

    # Ascending Triangle: flat/gentle resistance, clearly rising support
    # Upper must be nearly flat (rising less than 0.15), lower must be clearly rising (>0.15)
    if abs(upper_slope_pct) < 0.15 and lower_slope_pct > 0.15:
        pattern_name = "Ascending Triangle"
        description = "Flat resistance being tested, rising support"
    # Descending Triangle: clearly falling resistance, flat/slightly rising support
    elif upper_slope_pct < -0.15 and abs(lower_slope_pct) < 0.25:
        pattern_name = "Descending Triangle"
        description = "Declining resistance, flat support being tested"
    # Symmetrical Triangle: both lines sloping toward center with significant convergence
    elif convergence > 0.15:  # At least 15% narrowing
        pattern_name = "Symmetrical Triangle"
        description = "Bouncing between converging support and resistance"

    if pattern_name:
        # Directional triangles get higher base strength than convergence-based
        base = 0.5 if pattern_name in ["Ascending Triangle", "Descending Triangle"] else 0.3
        strength = min(0.9, base + convergence * 0.5)

        patterns.append({
            'type': 'triangle',
            'name': pattern_name,
            'description': description,
            'strength': max(0.3, strength),
            'bullish': pattern_name in ["Ascending Triangle"],
            'bearish': pattern_name in ["Descending Triangle"],
            'neutral': pattern_name == "Symmetrical Triangle"
        })

    return patterns


def detect_channel_patterns(df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
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

    # Minimum volatility check: price must move enough to form a real channel
    # Use ATR-based check: range must be > 1.5% of price to avoid noise
    price_range_pct = (np.max(recent_highs) - np.min(recent_lows)) / price_level * 100
    if price_range_pct < 1.5:
        # Too flat — skip channel detection entirely, return empty
        # This prevents Ranging Channel from falsely matching sideways noise
        return patterns

    # Determine trend strength
    avg_abs_slope = (abs(high_slope_pct) + abs(low_slope_pct)) / 2

    if avg_abs_slope > 0.3:
        # Meaningful trend — check direction
        if high_slope_pct > 0.15 and low_slope_pct > 0.15:
            pattern_name = "Uptrend Channel"
            description = "Higher highs and higher lows, bullish continuation"
        elif high_slope_pct < -0.15 and low_slope_pct < -0.15:
            pattern_name = "Downtrend Channel"
            description = "Lower highs and lower lows, bearish continuation"
        else:
            # Slopes exist but not both clearly up/down — could be ascending/descending wedge
            if high_slope_pct > 0.05 and low_slope_pct > 0.15:
                pattern_name = "Ascending Wedge"
                description = "Rising support faster than resistance, potential bearish reversal"
            elif high_slope_pct < -0.15 and low_slope_pct < -0.05:
                pattern_name = "Descending Wedge"
                description = "Falling support faster than resistance, potential bullish reversal"
            else:
                # Conflicting slopes, no clear channel
                return patterns
    else:
        # Flat — only call it Ranging if it's genuinely consolidating
        # (not just noise). Require some back-and-forth swings.
        if price_range_pct >= 1.5:
            pattern_name = "Ranging"
            description = "Sideways consolidation, no clear trend direction"
        else:
            return patterns

    if pattern_name:
        # Strength: penalize Ranging slightly so directional channels win
        base_strength = min(1.0, price_range_pct / 5.0)
        if pattern_name == "Ranging":
            # Only strong if there's actual swing within the range
            base_strength = min(0.6, base_strength)

        patterns.append({
            'type': 'channel',
            'name': pattern_name,
            'description': description,
            'strength': base_strength,
            'bullish': pattern_name in ["Uptrend Channel"],
            'bearish': pattern_name in ["Downtrend Channel"],
            'neutral': pattern_name == "Ranging"
        })

    return patterns


def detect_wedge_patterns(df: pd.DataFrame, lookback: int = 100) -> List[Dict]:
    """Detect wedge patterns: Rising, Falling, Contracting, Expanding"""
    patterns = []
    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values

    if len(df) < lookback:
        return patterns

    price_level = np.mean(close[-lookback:])
    price_range_pct = (np.max(high[-lookback:]) - np.min(low[-lookback:])) / price_level * 100
    if price_range_pct < 1.5:
        return patterns

    x = np.arange(lookback)
    high_slope, _ = np.polyfit(x, high[-lookback:], 1)
    low_slope, _ = np.polyfit(x, low[-lookback:], 1)
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
        early_range = high[-lookback:][0] - low[-lookback:][0]
        late_range = high[-lookback:][-1] - low[-lookback:][-1]
        if early_range > late_range * 1.15:
            pattern_name = "Contracting Triangle"
            description = "Narrowing price range, breakout imminent"

    if pattern_name:
        early_range = high[-lookback:][0] - low[-lookback:][0]
        late_range = high[-lookback:][-1] - low[-lookback:][-1]
        convergence = (early_range - late_range) / early_range if early_range > 0 else 0
        # Directional wedges get higher base strength to beat triangle detector
        base = 0.6 if pattern_name in ["Rising Wedge", "Falling Wedge"] else 0.3
        strength = min(0.9, base + convergence * 0.4)

        patterns.append({
            'type': 'wedge',
            'name': pattern_name,
            'description': description,
            'strength': max(0.3, strength),
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


def detect_all_patterns(df: pd.DataFrame, lookback: int = 100) -> Dict:
    """
    Detect all chart patterns and return comprehensive analysis.
    Uses lookback=100 (candles) for sufficient market structure detection.
    """
    all_patterns = []

    # Triangle patterns — lookback=100 for proper trendline convergence
    triangles = detect_triangle_patterns(df, lookback=lookback)
    all_patterns.extend(triangles)

    # Channel patterns — lookback=100 for meaningful trend slopes
    channels = detect_channel_patterns(df, lookback=lookback)
    all_patterns.extend(channels)

    # Wedge patterns — lookback=100 for converging/diverging lines
    wedges = detect_wedge_patterns(df, lookback=lookback)
    all_patterns.extend(wedges)

    # Harmonic patterns — lookback=100 already default
    harmonics = detect_harmonic_patterns(df, lookback=lookback)
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
