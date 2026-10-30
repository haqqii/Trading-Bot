"""Walk-forward backtest untuk mesin sinyal.

Menjalankan mesin sinyal yang sama dengan yang dipakai bot di atas data
historis (bar demi bar, tanpa melihat masa depan), lalu mensimulasikan
ke depan: apakah TP1 tersentuh sebelum SL dalam horizon N bar.

Hasil per kualitas sinyal (WEAK/MODERATE/STRONG) menjadi ukuran objektif
untuk klaim "akurat" - dan mode A/B (fitur mesin v2 on/off) mengukur
dampak tiap fitur terhadap win rate. Lihat scripts/backtest.py untuk CLI.

Batasan yang jujur:
- Outcome TP1-vs-SL memakai asumsi pesimis: di bar yang sama, SL dicek
  lebih dulu (kalau High menyentuh TP dan Low menyentuh SL di bar yang
  sama, dihitung SL).
- Tidak memodelkan slippage, gapping antar bar di luar High/Low, atau
  likuiditas. Fee dimodelkan sebagai persentase flat per putaran.
- Sinyal dalam jendela cooldown yang sama dihitung sekali, meniru
  anti-duplikat bot.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

import pandas as pd

from services.signal_service import (
    SignalService,
    set_features,
    get_tPSL_multipliers,
)

logger = logging.getLogger(__name__)

# Bar minimal sebelum sinyal pertama dinilai (MA lambat HTF = 100 + buffer)
WARMUP_BARS = 110


def build_data_at(df: pd.DataFrame, i: int, market: str = 'stock',
                  timeframe: str = '60') -> Optional[Dict[str, Any]]:
    """Bangun payload data pada bar ke-i memakai hanya df[:i+1].

    Meniru bentuk payload yang dibangun stock_service/crypto_service,
    jadi mesin sinyal menilai data yang sama persis dengan yang diterimanya
    saat live.
    """
    window = df.iloc[:i + 1]
    if len(window) < WARMUP_BARS:
        return None

    close = window['Close']
    high = window['High']
    low = window['Low']
    volume = window['Volume']
    price = float(close.iloc[-1])
    if not price or price <= 0:
        return None

    from utils.indicators import (
        calculate_rsi, calculate_macd, calculate_bollinger_bands,
        calculate_atr, calculate_vwap, calculate_htf_trend,
        calculate_volume_metrics, calculate_sr_levels,
    )

    rsi = calculate_rsi(close).iloc[-1]
    macd_data = calculate_macd(close)
    bb_data = calculate_bollinger_bands(close)
    atr = calculate_atr(high, low, close, 14).iloc[-1]
    vwap_data = calculate_vwap(high, low, close, volume)
    volume_ratio = calculate_volume_metrics(volume)['ratio']
    htf_trend = calculate_htf_trend(close)

    try:
        sr_data = calculate_sr_levels(high, low, close, volume)
        support = (sr_data.get('nearest_support') or {}).get('level')
        resistance = (sr_data.get('nearest_resistance') or {}).get('level')
    except Exception:
        support = resistance = None

    prev_close = float(close.iloc[-2])
    change = (price - prev_close) / prev_close * 100 if prev_close else 0.0

    data: Dict[str, Any] = {
        'price': price,
        'change': change,
        'ma_fast': close.rolling(8).mean().iloc[-1],
        'ma_slow': close.rolling(21).mean().iloc[-1],
        'rsi': rsi,
        'volume': float(volume.iloc[-1]),
        'atr': atr,
        'candles': len(window),
        'macd': macd_data['macd'].iloc[-1],
        'macd_signal': macd_data['signal'].iloc[-1],
        'macd_hist': macd_data['histogram'].iloc[-1],
        'bb_upper': bb_data['upper'].iloc[-1],
        'bb_middle': bb_data['middle'].iloc[-1],
        'bb_lower': bb_data['lower'].iloc[-1],
        'volume_ma': volume.rolling(20).mean().iloc[-1],
        'volume_ratio': volume_ratio,
        'vwap': vwap_data['current'],
        'htf_trend': htf_trend,
        'support': support,
        'resistance': resistance,
        'timeframe': timeframe,
    }
    if market == 'crypto':
        data['source'] = 'backtest'
    return data


def simulate_outcome(df: pd.DataFrame, i: int, direction: str,
                     tp1: float, sl: float, horizon: int) -> tuple[str, int]:
    """Simulasi maju: TP1 dulu atau SL dulu dalam `horizon` bar.

    Bar yang menyentuh dua-duanya dihitung SL (asumsi pesimis).
    Returns (outcome, bars_taken); outcome = 'tp1' | 'sl' | 'timeout'.
    """
    end = min(i + 1 + horizon, len(df))
    for j in range(i + 1, end):
        bar = df.iloc[j]
        if direction == 'BUY':
            if bar['Low'] <= sl:
                return 'sl', j - i
            if bar['High'] >= tp1:
                return 'tp1', j - i
        else:  # SELL
            if bar['High'] >= sl:
                return 'sl', j - i
            if bar['Low'] <= tp1:
                return 'tp1', j - i
    return 'timeout', end - (i + 1)


def run_backtest(df: pd.DataFrame, market: str = 'stock', timeframe: str = '60',
                 horizon: int = 40, cooldown_bars: int = 12,
                 features: Optional[Dict[str, bool]] = None) -> List[Dict[str, Any]]:
    """Jalankan mesin sinyal bar demi bar; kembalikan daftar trade.

    features: flag mesin yang di-set selama backtest lalu dikembalikan ke
    semula (untuk A/B - lihat set_features di signal_service).
    """
    if market == 'crypto':
        engine = SignalService.generate_crypto_signal
    else:
        engine = SignalService.generate_stock_signal

    saved = None
    if features:
        saved = set_features(**features)

    trades: List[Dict[str, Any]] = []
    last_fire: Dict[str, int] = {}  # direction -> bar index terakhir sinyal
    try:
        for i in range(WARMUP_BARS, len(df) - 1):
            data = build_data_at(df, i, market, timeframe)
            if data is None:
                continue
            s = engine(data)
            signal = s.get('signal')
            if signal not in ('BUY', 'SELL'):
                continue
            if not s.get('tp1') or not s.get('sl'):
                continue

            direction = signal
            if i - last_fire.get(direction, -9999) < cooldown_bars:
                continue
            last_fire[direction] = i

            outcome, bars = simulate_outcome(df, i, direction,
                                             float(s['tp1']), float(s['sl']),
                                             horizon)
            m = get_tPSL_multipliers(timeframe)
            tp1_ret = (s['tp1'] - data['price']) / data['price'] * 100
            if direction == 'SELL':
                tp1_ret = -tp1_ret
            sl_ret = (s['sl'] - data['price']) / data['price'] * 100
            if direction == 'SELL':
                sl_ret = -sl_ret
            trades.append({
                'bar': i,
                'signal': signal,
                'quality': s.get('quality', 'WEAK'),
                'buy_score': s.get('buy_score', 0),
                'sell_score': s.get('sell_score', 0),
                'htf_trend': data.get('htf_trend'),
                'outcome': outcome,
                'bars': bars,
                'tp1_ret_pct': tp1_ret,
                'sl_ret_pct': sl_ret,
                'risk_reward': abs(tp1_ret / sl_ret) if sl_ret else 0,
                'max_tp_mult': m['tp1'],
            })
    finally:
        if saved:
            set_features(**saved)
    return trades


def summarize(trades: List[Dict[str, Any]], fee_pct: float = 0.0) -> pd.DataFrame:
    """Rekap per sinyal & kualitas: win rate, ekspektansi neto per trade.

    Ekspektansi = rata-rata PnL per trade mengasumsikan keluar penuh di
    TP1 (profit) atau SL (rugi), dikurangi fee. Timeout dihitung 0.
    """
    if not trades:
        return pd.DataFrame()

    def pnl(t: Dict[str, Any]) -> float:
        if t['outcome'] == 'tp1':
            return t['tp1_ret_pct'] - fee_pct
        if t['outcome'] == 'sl':
            return t['sl_ret_pct'] - fee_pct
        return 0.0

    rows = []
    df = pd.DataFrame(trades)
    df['pnl'] = df.apply(pnl, axis=1)
    for (signal, quality), g in df.groupby(['signal', 'quality']):
        n = len(g)
        wins = int((g['outcome'] == 'tp1').sum())
        losses = int((g['outcome'] == 'sl').sum())
        timeouts = int((g['outcome'] == 'timeout').sum())
        decided = wins + losses
        rows.append({
            'signal': signal,
            'quality': quality,
            'n': n,
            'tp1': f"{wins / n * 100:.0f}%",
            'sl': f"{losses / n * 100:.0f}%",
            'timeout': f"{timeouts / n * 100:.0f}%",
            'win_rate_decided': f"{wins / decided * 100:.0f}%" if decided else '-',
            'avg_RR': f"{g['risk_reward'].mean():.2f}",
            'exp_net_%': f"{g['pnl'].mean():+.2f}",
            'total_pnl_%': f"{g['pnl'].sum():+.1f}",
        })
    out = pd.DataFrame(rows)
    # Total keseluruhan di baris terakhir
    wins = int((df['outcome'] == 'tp1').sum())
    losses = int((df['outcome'] == 'sl').sum())
    decided = wins + losses
    out.loc[len(out)] = {
        'signal': 'TOTAL', 'quality': '-', 'n': len(df),
        'tp1': f"{wins / len(df) * 100:.0f}%",
        'sl': f"{losses / len(df) * 100:.0f}%",
        'timeout': f"{(len(df) - decided) / len(df) * 100:.0f}%",
        'win_rate_decided': f"{wins / decided * 100:.0f}%" if decided else '-',
        'avg_RR': f"{df['risk_reward'].mean():.2f}",
        'exp_net_%': f"{df['pnl'].mean():+.2f}",
        'total_pnl_%': f"{df['pnl'].sum():+.1f}",
    }
    return out


def compare_features(df: pd.DataFrame, market: str = 'stock',
                     timeframe: str = '60', horizon: int = 40,
                     cooldown_bars: int = 12,
                     fee_pct: float = 0.0) -> Dict[str, Any]:
    """A/B: jalankan mesin klasik (semua fitur v2 mati) vs mesin v2.

    Returns dict: rekap ter-whittle {'classic', 'v2'} dan trade mentah
    {'classic_trades', 'v2_trades'}.
    """
    classic_flags = {k: False for k in (
        'htf_trend', 'sr_tpsl', 'vol_gate', 'degraded_cap', 'vwap_crypto')}
    classic_trades = run_backtest(df, market, timeframe, horizon, cooldown_bars,
                                  features=classic_flags)
    v2_trades = run_backtest(df, market, timeframe, horizon, cooldown_bars,
                             features=None)
    return {
        'classic': summarize(classic_trades, fee_pct),
        'v2': summarize(v2_trades, fee_pct),
        'classic_trades': classic_trades,
        'v2_trades': v2_trades,
    }
