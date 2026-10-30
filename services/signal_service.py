"""
Signal generation service for stocks and crypto.

Engine v2 (akurasi):
- Indikator yang hilang di-skip dari scoring dan menurunkan kualitas sinyal,
  bukan diam-diam dianggap netral (dulu data fallback palsu ikut dinilai).
- VWAP crypto hanya dinilai kalau VWAP-nya benar-benar ada. Dulu default
  vwap = harga membuat seluruh jalur scan CoinGecko dapat +10 poin SELL.
- Konfirmasi trend besar (HTF): sinyal yang melawan arah besar kehilangan
  poin; yang searah dapat bonus.
- TP/SL menghormati support/resistance: TP diklem di depan resistance,
  SL dipindah ke balik support/resistance yang masuk akal.
- Volatilitas ekstrem menurunkan kualitas sinyal.

Semua fitur bisa dimatikan lewat set_features() untuk A/B backtest.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

# Type alias for signal dict
SignalDict = Dict[str, Any]
StockDataDict = Dict[str, Any]


# Feature flags. Default: semua aktif. Backtest mematikan sebagian untuk
# mengukur dampak tiap fitur terhadap win rate.
FEATURES: Dict[str, bool] = {
    'htf_trend': True,      # konfirmasi arah trend besar
    'sr_tpsl': True,        # TP/SL menghormati support/resistance
    'vol_gate': True,       # volatilitas ekstrem menurunkan kualitas
    'degraded_cap': True,   # indikator tidak lengkap menurunkan kualitas
    'vwap_crypto': True,    # VWAP crypto hanya dinilai kalau datanya ada
    # Regime gate MATI default: sudah diukur A/B (saham IDX 1h 6bln,
    # crypto 1h 90d - lihat docs/SPEC.md 3.1.1) dan tidak memperbaiki
    # hasil - di saham malah sedikit lebih buruk. Kode dan A/B-nya
    # tetap ada; nyalakan lagi di sini kalau mau diukur ulang.
    'regime_gate': False,   # ADX menentukan aturan yang dipakai (trend/range)
    'exit_mgmt': True,      # SL bergerak: breakeven setelah TP1, trailing setelah TP2
}

# Batas ATR% untuk gate volatilitas ekstrem (ATR / harga * 100).
VOL_GATE_STOCK_PCT = 6.0
VOL_GATE_CRYPTO_PCT = 10.0

# Regime pasar, ditentukan dari ADX.
#   trend = ada arah yang jelas  -> aturan momentum (MA/MACD) yang dipakai
#   range = sideways            -> aturan mean-reversion (RSI/BB) yang dipakai
#   none  = ADX di tengah atau tidak ada -> aturan seperti biasa
# Batasnya mengikuti konvensi ADX yang sudah dipakai di calculate_adx
# (ADX > 25 = tren kuat), jadi angka ini tidak bentrok dengan indikator
# 'adx_strong' yang sudah dipakai di mesin.
REGIME_NONE = 'none'
REGIME_TREND = 'trend'
REGIME_RANGE = 'range'
REGIME_ADX_TREND = 25.0
REGIME_ADX_RANGE = 20.0

# Pengali tiap komponen skor per regime.
# Di pasar sideways, MA/MACD berbalik bolak-balik (noise) dan harga yang
# menyentuh band Bollinger adalah peluang mean-reversion. Di pasar trending,
# sebaliknya: harga yang menyentuh band itu normal, dan MA/MACD yang
# menentukan arah.
REGIME_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    REGIME_NONE:  {'ma': 1.0, 'macd': 1.0, 'bb': 1.0, 'momentum': 1.0},
    REGIME_TREND: {'ma': 1.0, 'macd': 1.0, 'bb': 0.5, 'momentum': 1.0},
    REGIME_RANGE: {'ma': 0.5, 'macd': 0.5, 'bb': 1.5, 'momentum': 0.5},
}

# Poin komponen yang berlawanan arah dengan tren saat regime=trend
# (mis. RSI oversold sementara harga turun) dikurangi jadi seperempat.
COUNTER_TREND_FACTOR = 0.3

# Jarak aman TP dari resistance / SL dari support (0.3%).
TPSR_BUFFER = 0.003


def set_features(**flags: bool) -> Dict[str, bool]:
    """Ubah flag fitur (dipakai backtest A/B). Mengembalikan nilai lama."""
    old = dict(FEATURES)
    for key, value in flags.items():
        if key not in FEATURES:
            raise KeyError(f"Unknown feature flag: {key}")
        FEATURES[key] = bool(value)
    return old


_QUALITY_ORDER = {'WEAK': 0, 'MODERATE': 1, 'STRONG': 2}


def cap_quality(quality: str, cap: Optional[str]) -> str:
    """Batasi kualitas sinyal ke atas: STRONG di bawah cap MODERATE jadi MODERATE."""
    if cap is None or quality == cap:
        return quality
    if _QUALITY_ORDER.get(quality, 0) > _QUALITY_ORDER.get(cap, 2):
        return cap
    return quality


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


def _apply_sr_to_tpsl(tpsl: Dict[str, Optional[float]], signal: str, price: float,
                      atr: float, support: Optional[float],
                      resistance: Optional[float]) -> None:
    """Sesuaikan TP/SL mentah ATR dengan struktur support/resistance.

    BUY:
    - TP2/TP3 yang menembus resistance diklem sedikit di bawahnya - target
      di balik dinding hampir tidak pernah tersentuh sebelum harga berbalik.
    - SL dipindah tepat di balik support kalau support berada pada jarak
      yang masuk akal (antara 0.5x dan sl_multipliers x ATR). Terlalu dekat
      berarti dalam jangkauan noise; lebih jauh dari ATR berarti risiko
      membengkak - dua-duanya dibiarkan memakai stop ATR.
    SELL: cermin dengan resistance di atas dan support di bawah.
    """
    if signal == 'BUY':
        if resistance is not None and price < resistance:
            tp_cap = resistance * (1 - TPSR_BUFFER)
            if tpsl['tp1'] is not None and tpsl['tp1'] < tp_cap:
                tpsl['tp2'] = min(tpsl['tp2'], tp_cap)
                tpsl['tp3'] = min(tpsl['tp3'], tp_cap)
        sl_dist = price - (tpsl['sl'] or price)
        if support is not None and price > support:
            dist = price - support
            if 0.5 * atr <= dist <= sl_dist:
                tpsl['sl'] = max(tpsl['sl'] or price, support * (1 - TPSR_BUFFER))
    elif signal == 'SELL':
        if support is not None and price > support:
            tp_floor = support * (1 + TPSR_BUFFER)
            if tpsl['tp1'] is not None and tpsl['tp1'] > tp_floor:
                tpsl['tp2'] = max(tpsl['tp2'], tp_floor)
                tpsl['tp3'] = max(tpsl['tp3'], tp_floor)
        sl_dist = (tpsl['sl'] or price) - price
        if resistance is not None and resistance > price:
            dist = resistance - price
            if 0.5 * atr <= dist <= sl_dist:
                tpsl['sl'] = min(tpsl['sl'] or price, resistance * (1 + TPSR_BUFFER))


def calc_tPSL(signal: str, price: float, atr: float, timeframe: str = '5',
              support: Optional[float] = None,
              resistance: Optional[float] = None) -> Dict[str, Optional[float]]:
    """Calculate TP/SL with timeframe-adjusted multipliers.

    support/resistance opsional (harga struktur terdekat dari data yang
    sama); kalau diisi, target dan stop dijelaskan di _apply_sr_to_tpsl.
    """
    if signal not in ('BUY', 'SELL') or not price or not atr or atr <= 0:
        return {'sl': None, 'tp1': None, 'tp2': None, 'tp3': None}

    m = get_tPSL_multipliers(timeframe)
    if signal == 'BUY':
        tpsl: Dict[str, Optional[float]] = {
            'sl': price - (m['sl'] * atr),
            'tp1': price + (m['tp1'] * atr),
            'tp2': price + (m['tp2'] * atr),
            'tp3': price + (m['tp3'] * atr),
        }
    else:
        tpsl = {
            'sl': price + (m['sl'] * atr),
            'tp1': price - (m['tp1'] * atr),
            'tp2': price - (m['tp2'] * atr),
            'tp3': price - (m['tp3'] * atr),
        }

    if FEATURES['sr_tpsl']:
        _apply_sr_to_tpsl(tpsl, signal, price, atr, support, resistance)
    return tpsl


# === Shared Scoring Helpers ===


def safe_num(value: Any, default: float) -> float:
    """Return a numeric value, falling back to ``default`` when value is None.

    Upstream APIs can legitimately return ``null`` for a field (e.g. CoinGecko
    ``price_change_percentage_24h`` for yield-bearing tokens such as
    FIGR_HELOC).  When such a None is stored in the data dict, ``dict.get(key, default)``
    no longer helps because the key exists — the comparison blows up with
    ``TypeError: '>=' not supported between instances of 'NoneType' and 'int'``.

    Dipakai juga di luar mesin sinyal (job notifikasi) karena jalur
    fallback sekarang sengaja mengirim None untuk indikator yang tidak
    ada - bukan angka karangan.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Alias internal (dipakai banyak di modul ini).
_safe_num = safe_num


def finite_or_none(value: Any) -> Optional[float]:
    """Angka berhingga, atau None kalau hilang/NaN/inf/tipe salah.

    NaN lolos dari ``dict.get(key, default)`` karena key-nya ada - dan
    semua perbandingan dengan NaN bernilai False, jadi indikator rusak
    tadinya diam-diam dinilai netral. Sekarang None = komponen di-skip
    dan dihitung sebagai data tidak lengkap.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# Alias lama (dipakai modul lain).
_finite_or_none = finite_or_none


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
    if volume_ratio > spike_thresh:
        added_buy = spike_pts if bias >= 0 else 0
        added_sell = spike_pts if bias <= 0 else 0
        if added_buy or added_sell:
            reason = f'Vol spike {volume_ratio:.1f}x'
    elif volume_ratio > moderate_thresh:
        added_buy = moderate_pts if bias >= 0 else 0
        added_sell = moderate_pts if bias <= 0 else 0
    return added_buy, added_sell, reason


def score_htf_trend(
    htf_trend: Any,
    direction: str,
    aligned_pts: int,
    against_pts: int
) -> tuple[int, int, str | None]:
    """Score konfirmasi trend besar. direction = 'BUY' | 'SELL'.

    Searah dengan trend besar = bonus untuk arah sinyal; melawan = poin
    untuk sisi lawan (sinyal butuh bukti lebih untuk tetap menang).
    Reversal yang sah tetap bisa lewat, tapi tidak dapat nilai penuh dari
    komponen ini. htf_trend 'flat'/None/tidak dikenal = netral.
    """
    if htf_trend not in ('up', 'down'):
        return 0, 0, None
    bullish = htf_trend == 'up'
    aligned = (direction == 'BUY') == bullish
    if aligned:
        reason = 'HTF uptrend aligned' if bullish else 'HTF downtrend aligned'
    else:
        reason = 'Against HTF uptrend' if bullish else 'Against HTF downtrend'
    pts = aligned_pts if aligned else against_pts
    if (direction == 'BUY') == aligned:
        return pts, 0, reason
    return 0, pts, reason


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


def _quality_cap(missing: List[str], degraded: bool) -> tuple[Optional[str], Optional[str]]:
    """Cap kualitas berdasar kelengkapan data.

    Kembalikan (cap, reason). Sinyal dari data yang bolong tidak boleh
    menyebut dirinya STRONG - dulu sinyal fallback TradingView (yang
    MACD/BB-nya dikarang) bisa lolos sebagai sinyal penuh keyakinan.
    """
    uniq = sorted(set(missing))
    if not FEATURES['degraded_cap']:
        return None, None
    if len(uniq) >= 3:
        return 'WEAK', f"Limited data (missing: {', '.join(uniq)})"
    if uniq or degraded:
        return 'MODERATE', f"Limited data (missing: {', '.join(uniq)})" if uniq else 'Degraded data source'
    return None, None


def _volatility_cap(atr_pct: float, is_crypto: bool) -> tuple[Optional[str], Optional[str]]:
    """Cap kualitas saat volatilitas ekstrem; kembalikan (cap, reason)."""
    if not FEATURES['vol_gate']:
        return None, None
    limit = VOL_GATE_CRYPTO_PCT if is_crypto else VOL_GATE_STOCK_PCT
    if atr_pct > limit:
        return 'MODERATE', f'Extreme volatility {atr_pct:.1f}%'
    return None, None


def _apply_caps(quality: str, caps: List[tuple[Optional[str], Optional[str]]],
                reasons: List[str]) -> str:
    """Terapkan semua cap kualitas, catat reason untuk yang benar-benar menurunkan."""
    final_cap: Optional[str] = None
    for cap, reason in caps:
        if cap is None:
            continue
        if final_cap is None or _QUALITY_ORDER[cap] < _QUALITY_ORDER[final_cap]:
            final_cap = cap
        if reason:
            reasons.append(reason)
    return cap_quality(quality, final_cap)


def detect_regime(adx: Any) -> str:
    """Regime pasar dari ADX: trend / range / none.

    ADX tidak ada (data fallback) atau flag dimatikan -> 'none', artinya
    skor dihitung persis seperti sebelumnya.
    """
    if not FEATURES['regime_gate']:
        return REGIME_NONE
    value = finite_or_none(adx)
    if value is None:
        return REGIME_NONE
    if value > REGIME_ADX_TREND:
        return REGIME_TREND
    if value < REGIME_ADX_RANGE:
        return REGIME_RANGE
    return REGIME_NONE


def _scale_points(points: int, factor: float) -> int:
    """Kalikan poin komponen skor dengan faktor regime."""
    if not points or factor == 1.0:
        return points
    return int(round(points * factor))


def _trend_alignment(price: float, ma_slow: Optional[float]) -> Optional[bool]:
    """True kalau harga di atas MA lambat (tren naik), False kalau di bawah.

    None kalau MA lambat tidak ada - tanpa itu arah tren tidak diketahui
    dan aturan counter-trend tidak boleh ditebak.
    """
    if ma_slow is None or not price:
        return None
    return price > ma_slow


class SignalService:
    """Service for generating trading signals"""

    @staticmethod
    def generate_stock_signal(data: Optional[StockDataDict]) -> SignalDict:
        """Generate stock signal using weighted multi-indicator scoring system."""
        if not data:
            return {'signal': 'HOLD', 'reason': 'No data'}

        price = data['price']
        atr = _safe_num(data.get('atr'), price * 0.015)
        volume_ratio = _safe_num(data.get('volume_ratio'), 1.0)

        # Indikator boleh hilang (NaN/None) - masing-masing di-skip dari
        # scoring dan dicatat, bukan digantikan nilai karangan.
        rsi_raw = finite_or_none(data.get('rsi'))
        rsi = rsi_raw if rsi_raw is not None else 50.0
        ma_f = finite_or_none(data.get('ma_fast'))
        ma_s = finite_or_none(data.get('ma_slow'))
        macd = finite_or_none(data.get('macd'))
        macd_signal = finite_or_none(data.get('macd_signal'))
        macd_hist = finite_or_none(data.get('macd_hist'))
        bb_upper = finite_or_none(data.get('bb_upper'))
        bb_lower = finite_or_none(data.get('bb_lower'))
        vwap = finite_or_none(data.get('vwap'))
        htf_trend = data.get('htf_trend')
        missing: List[str] = []

        buy_score = 0
        sell_score = 0
        reasons: List[str] = []

        # Regime pasar (dari ADX) menentukan aturan mana yang dipakai.
        # Tanpa ADX -> 'none' -> skor persis seperti sebelumnya.
        regime = detect_regime(data.get('adx'))
        mult = REGIME_MULTIPLIERS[regime]
        price_above_ma = _trend_alignment(price, ma_s)

        # RSI Score (weight: 25%)
        if rsi_raw is None:
            missing.append('rsi')
        else:
            b, s, r = score_rsi(rsi, (30, 40), (70, 60), (25, 10), (25, 10))
            if regime == REGIME_TREND and price_above_ma is not None:
                # RSI ekstrem berlawanan arah dengan tren = jebakan klasik
                # "menangkap pisau jatuh", bukan pembalikan.
                if b and not price_above_ma:
                    b = _scale_points(b, COUNTER_TREND_FACTOR)
                    reasons.append('RSI ekstrem saat tren turun - counter-trend, dikurangi')
                elif s and price_above_ma:
                    s = _scale_points(s, COUNTER_TREND_FACTOR)
                    reasons.append('RSI ekstrem saat tren naik - counter-trend, dikurangi')
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # MA Score (weight: 20%)
        if ma_f is None or ma_s is None:
            missing.append('ma')
        else:
            b, s, r = score_ma(ma_f, ma_s)
            b = _scale_points(b, mult['ma'])
            s = _scale_points(s, mult['ma'])
            if regime == REGIME_TREND and price_above_ma is not None:
                # Golden cross di tengah penurunan (harga di bawah MA lambat)
                # sering berbalik - kurangi poinnya.
                if b and not price_above_ma:
                    b = _scale_points(b, COUNTER_TREND_FACTOR)
                    reasons.append('MA cross saat tren turun - counter-trend, dikurangi')
                elif s and price_above_ma:
                    s = _scale_points(s, COUNTER_TREND_FACTOR)
                    reasons.append('MA cross saat tren naik - counter-trend, dikurangi')
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # MACD Score (weight: 25%)
        if macd is None or macd_signal is None or macd_hist is None:
            missing.append('macd')
        else:
            b, s, r = score_macd(macd, macd_signal, macd_hist, (25, 15))
            b = _scale_points(b, mult['macd'])
            s = _scale_points(s, mult['macd'])
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Bollinger Bands Score (weight: 15%)
        if bb_upper is None or bb_lower is None:
            missing.append('bb')
        else:
            b, s, r = score_bb(price, bb_upper, bb_lower, 15)
            b = _scale_points(b, mult['bb'])
            s = _scale_points(s, mult['bb'])
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Volume Score (weight: 15%) - volume_ratio selalu punya nilai netral 1.0
        b, s, r = score_volume(volume_ratio, buy_score, sell_score, 15, 8, 1.5, 1.2)
        buy_score += b; sell_score += s; reasons.append(r) if r else None

        # VWAP Score (weight: 10%) - komponen baru; hanya kalau VWAP ada.
        if vwap is not None:
            if price > vwap:
                buy_score += 10
                reasons.append('Above VWAP')
            else:
                sell_score += 10
                reasons.append('Below VWAP')

        # Konfirmasi trend besar (proxy dari data yang sama, tanpa request baru)
        if FEATURES['htf_trend'] and htf_trend in ('up', 'down'):
            b, s, r = score_htf_trend(htf_trend,
                                      'BUY' if buy_score >= sell_score else 'SELL',
                                      aligned_pts=10, against_pts=10)
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Determine signal
        signal, quality = determine_signal(buy_score, sell_score)

        # Calculate TP/SL with timeframe-adjusted multipliers
        effective_atr = max(atr, price * 0.003)
        timeframe = data.get('timeframe', '5')
        tpsl = calc_tPSL(signal, price, effective_atr, timeframe,
                         support=finite_or_none(data.get('support')),
                         resistance=finite_or_none(data.get('resistance')))

        # Cap kualitas: data tidak lengkap + volatilitas ekstrem
        atr_pct = (effective_atr / price) * 100 if price else 0
        quality = _apply_caps(quality, [
            _quality_cap(missing, bool(data.get('degraded'))),
            _volatility_cap(atr_pct, is_crypto=False),
        ], reasons)

        return {
            'signal': signal,
            'reason': ', '.join(reasons) if reasons else 'No signal',
            'entry': price,
            'tp1': tpsl['tp1'], 'tp2': tpsl['tp2'], 'tp3': tpsl['tp3'],
            'sl': tpsl['sl'],
            'rsi': rsi,
            'atr': effective_atr,
            'atr_pct': atr_pct,
            'quality': quality,
            'buy_score': buy_score,
            'sell_score': sell_score,
            'macd_hist': _safe_num(macd_hist, 0),
            'volume_ratio': volume_ratio,
            'vwap': vwap,
            'htf_trend': htf_trend,
            'regime': regime,
            'missing_indicators': sorted(set(missing)),
        }

    @staticmethod
    def generate_crypto_signal(data: Optional[StockDataDict]) -> SignalDict:
        """Generate crypto signal using weighted multi-indicator scoring system"""
        if not data:
            return {'signal': 'HOLD', 'reason': 'No data'}

        price = data['price']
        atr = _safe_num(data.get('atr'), price * 0.025)
        change = _safe_num(data.get('change'), 0)

        missing: List[str] = []
        rsi_raw = finite_or_none(data.get('rsi'))
        rsi = rsi_raw if rsi_raw is not None else 50.0
        ma_f = finite_or_none(data.get('ma_fast'))
        ma_s = finite_or_none(data.get('ma_slow'))
        macd = finite_or_none(data.get('macd'))
        macd_signal = finite_or_none(data.get('macd_signal'))
        macd_hist = finite_or_none(data.get('macd_hist'))
        bb_upper = finite_or_none(data.get('bb_upper'))
        bb_lower = finite_or_none(data.get('bb_lower'))
        volume_ratio = _safe_num(data.get('volume_ratio'), 1.0)
        vwap = finite_or_none(data.get('vwap'))
        stoch_k = _safe_num(data.get('stoch_k'), 50)
        stoch_oversold = data.get('stoch_oversold', False)
        stoch_overbought = data.get('stoch_overbought', False)
        stoch_bullish_cross = data.get('stoch_bullish_cross', False)
        adx = _safe_num(data.get('adx'), 25)
        plus_di = _safe_num(data.get('plus_di'), 25)
        minus_di = _safe_num(data.get('minus_di'), 25)
        adx_strong = data.get('adx_strong', False)
        ichi_bullish = data.get('ichi_bullish', False)
        ichi_bearish = data.get('ichi_bearish', False)
        ichi_cloud_above = data.get('ichi_cloud_above', False)
        htf_trend = data.get('htf_trend')

        buy_score = 0
        sell_score = 0
        reasons: List[str] = []

        # Regime pasar (dari ADX) menentukan aturan mana yang dipakai.
        regime = detect_regime(data.get('adx'))
        mult = REGIME_MULTIPLIERS[regime]
        price_above_ma = _trend_alignment(price, ma_s)

        # RSI Score (weight: 20%) - INCREASED from 15%
        # Option A: More aggressive RSI oversold scoring
        if rsi_raw is None:
            missing.append('rsi')
        else:
            rsi_buy = 0
            rsi_sell = 0
            if rsi < 30:
                rsi_buy = 20
                reasons.append(f'RSI {rsi:.0f} STRONG oversold')
            elif rsi < 35:
                rsi_buy = 15
                reasons.append(f'RSI {rsi:.0f} oversold')
            elif rsi < 45:
                rsi_buy = 10
                reasons.append(f'RSI {rsi:.0f} bullish')
            elif rsi < 50:
                rsi_buy = 5
                reasons.append(f'RSI {rsi:.0f} near oversold')
            elif rsi > 70:
                rsi_sell = 20
                reasons.append(f'RSI {rsi:.0f} STRONG overbought')
            elif rsi > 65:
                rsi_sell = 15
                reasons.append(f'RSI {rsi:.0f} overbought')
            elif rsi > 60:
                rsi_sell = 8
                reasons.append(f'RSI {rsi:.0f} bearish')

            if regime == REGIME_TREND and price_above_ma is not None:
                # RSI ekstrem berlawanan arah dengan tren = jebakan klasik
                # "menangkap pisau jatuh", bukan pembalikan.
                if rsi_buy and not price_above_ma:
                    rsi_buy = _scale_points(rsi_buy, COUNTER_TREND_FACTOR)
                    reasons.append('RSI ekstrem saat tren turun - counter-trend, dikurangi')
                elif rsi_sell and price_above_ma:
                    rsi_sell = _scale_points(rsi_sell, COUNTER_TREND_FACTOR)
                    reasons.append('RSI ekstrem saat tren naik - counter-trend, dikurangi')

            buy_score += rsi_buy
            sell_score += rsi_sell

        # MA Score (weight: 10%) - same as stock
        if ma_f is None or ma_s is None:
            missing.append('ma')
        else:
            b, s, r = score_ma(ma_f, ma_s)
            b = _scale_points(b, mult['ma'])
            s = _scale_points(s, mult['ma'])
            if regime == REGIME_TREND and price_above_ma is not None:
                if b and not price_above_ma:
                    b = _scale_points(b, COUNTER_TREND_FACTOR)
                    reasons.append('MA cross saat tren turun - counter-trend, dikurangi')
                elif s and price_above_ma:
                    s = _scale_points(s, COUNTER_TREND_FACTOR)
                    reasons.append('MA cross saat tren naik - counter-trend, dikurangi')
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # MACD Score (weight: 15%)
        if macd is None or macd_signal is None or macd_hist is None:
            missing.append('macd')
        else:
            b, s, r = score_macd(macd, macd_signal, macd_hist, (15, 8))
            b = _scale_points(b, mult['macd'])
            s = _scale_points(s, mult['macd'])
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Bollinger Bands Score (weight: 10%)
        if bb_upper is None or bb_lower is None:
            missing.append('bb')
        else:
            b, s, r = score_bb(price, bb_upper, bb_lower, 10)
            b = _scale_points(b, mult['bb'])
            s = _scale_points(s, mult['bb'])
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
        # Dulu VWAP yang hilang diganti nilai = harga, sehingga
        # `price > vwap` selalu False dan seluruh jalur scan CoinGecko
        # menerima +10 poin SELL gratis. Sekarang VWAP tanpa data = netral.
        if not FEATURES['vwap_crypto']:
            vwap_effective = vwap if vwap is not None else price
            if price > vwap_effective:
                buy_score += 10
                reasons.append('Above VWAP')
            else:
                sell_score += 10
                reasons.append('Below VWAP')
        elif vwap is not None:
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

        # Konfirmasi trend besar (sparkline 7d / candle yang sudah ada)
        if FEATURES['htf_trend'] and htf_trend in ('up', 'down'):
            b, s, r = score_htf_trend(htf_trend,
                                      'BUY' if buy_score >= sell_score else 'SELL',
                                      aligned_pts=8, against_pts=8)
            buy_score += b; sell_score += s; reasons.append(r) if r else None

        # Volume + Momentum Score (weight: 10%)
        if change >= 3:
            momentum_pts = _scale_points(8, mult['momentum'])
            if buy_score > sell_score:
                buy_score += momentum_pts
                reasons.append(f'Momentum +{change:.1f}%')
            else:
                sell_score += momentum_pts
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
        reversal_reasons: List[str] = []

        if rsi_raw is not None and rsi < 40 and change > 1:
            is_reversal = True
            reversal_reasons.append(f'RSI oversold ({rsi:.0f})')
            reversal_reasons.append(f'Harga naik +{change:.1f}%')
            if macd_hist is not None and macd_hist > 0:
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
        tpsl = calc_tPSL(signal, price, effective_atr, timeframe,
                         support=finite_or_none(data.get('support')),
                         resistance=finite_or_none(data.get('resistance')))

        # Cap kualitas: data tidak lengkap + volatilitas ekstrem
        atr_pct = (effective_atr / price) * 100 if price else 0
        quality = _apply_caps(quality, [
            _quality_cap(missing, bool(data.get('degraded'))),
            _volatility_cap(atr_pct, is_crypto=True),
        ], reasons)

        return {
            'signal': signal,
            'reason': ', '.join(reasons) if reasons else 'No signal',
            'entry': price,
            'tp1': tpsl['tp1'], 'tp2': tpsl['tp2'], 'tp3': tpsl['tp3'],
            'sl': tpsl['sl'],
            'rsi': rsi,
            'atr': effective_atr,
            'atr_pct': atr_pct,
            'quality': quality,
            'buy_score': buy_score,
            'sell_score': sell_score,
            'macd_hist': _safe_num(macd_hist, 0),
            'volume_ratio': volume_ratio,
            'stoch_k': stoch_k,
            'adx': adx,
            'vwap': vwap,
            'htf_trend': htf_trend,
            'regime': regime,
            'missing_indicators': sorted(set(missing)),
            'ichi_bullish': ichi_bullish,
            'ichi_bearish': ichi_bearish,
            'fib_levels': data.get('fib_levels', {}),
            # Option B: REVERSAL signal info
            'is_reversal': is_reversal,
            'reversal_reasons': reversal_reasons,
        }


# Singleton instance
signal_service = SignalService()
