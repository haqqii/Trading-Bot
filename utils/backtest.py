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
    FEATURES,
)
from services.exit_manager import update_sl

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
        calculate_volume_metrics, calculate_sr_levels, calculate_adx,
    )

    rsi = calculate_rsi(close).iloc[-1]
    macd_data = calculate_macd(close)
    bb_data = calculate_bollinger_bands(close)
    atr = calculate_atr(high, low, close, 14).iloc[-1]
    vwap_data = calculate_vwap(high, low, close, volume)
    volume_ratio = calculate_volume_metrics(volume)['ratio']
    htf_trend = calculate_htf_trend(close)
    adx_data = calculate_adx(high, low, close)

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
        'adx': adx_data['adx_current'],
        'plus_di': adx_data['plus_di_current'],
        'minus_di': adx_data['minus_di_current'],
        'adx_strong': adx_data['strong_trend'],
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


def _exit_fill_price(stop_level: float, close: float, is_short: bool) -> float:
    """Harga isi exit SL - realistic, sama seperti bot live.

    Bot live menutup posisi di harga yang terlihat saat polling, bukan
    di level SL. Kalau bar ditutup di sisi yang lebih buruk dari stop,
    harga itu yang dilihat bot - jadi exit dicatat di harga close
    (lebih buruk dari stop). Kalau bar ditutup di sisi yang lebih baik,
    asumsikan exit terjadi tepat di level stop (konservatif).

    Tanpa ini, backtest selalu mencatat breakeven sebagai 0,00%
    padahal bot live jarang sekali menutup persis di harga entry.
    """
    if is_short:
        return close if close > stop_level else stop_level
    return close if close < stop_level else stop_level


def simulate_exit(df: pd.DataFrame, i: int, direction: str,
                  entry: float, tp1: float, tp2: float, tp3: float,
                  sl: float, atr: float, horizon: int,
                  manage_exit: bool) -> tuple[str, float, int]:
    """Simulasi keluar seperti bot live: posisi ditahan sampai TP3/SL/timeout.

    Bot tidak menutup posisi di TP1 - dia cuma memberi tahu, lalu SL
    digeser ke breakeven. Jadi model 'tp1' (keluar di TP1) tidak
    menggambarkan produksi. Model ini yang dipakai untuk mengukur dampak
    aturan keluar.

    `manage_exit` menyalakan aturan SL bergerak:
    - TP1 kena -> SL ke harga entry (breakeven)
    - TP2 kena -> SL trailing (minimal di level TP1)

    Bar yang menyentuh SL dan TP sekaligus dihitung SL dulu (pesimis).
    SL trailing yang baru dibuat di bar yang sama tidak dievaluasi di bar
    itu juga (SL hanya diperiksa sebelum TP pada bar tersebut).

    Returns (exit_reason, exit_price, bars_held); exit_reason =
    'tp3' | 'sl' | 'beakeven' | 'trailing' | 'timeout'.
    """
    end = min(i + 1 + horizon, len(df))
    is_short = direction == 'SELL'
    sl_level = float(sl)
    tp1_hit = False
    tp2_hit = False

    for j in range(i + 1, end):
        bar = df.iloc[j]
        high = float(bar['High'])
        low = float(bar['Low'])
        close = float(bar['Close'])

        # SL dicek lebih dulu (asumsi pesimis, sama seperti bot live).
        if sl_level:
            hit_sl = high >= sl_level if is_short else low <= sl_level
            if hit_sl:
                if tp2_hit:
                    reason = 'trailing'
                elif tp1_hit:
                    reason = 'beakeven'
                else:
                    reason = 'sl'
                return reason, _exit_fill_price(sl_level, close, is_short), j - i

        if not tp1_hit:
            hit = low <= tp1 if is_short else high >= tp1
            if hit:
                tp1_hit = True
                if manage_exit:
                    sl_level = update_sl(sl_level, entry, direction,
                                         {'tp1': True, 'tp2': False},
                                         close, atr, tp1=tp1)

        if not tp2_hit:
            hit = low <= tp2 if is_short else high >= tp2
            if hit:
                tp2_hit = True
                if manage_exit:
                    # Jangkar trailing pakai ekstrem bar (high untuk BUY),
                    # supaya SL mengikuti harga tertinggi yang sudah tercapai.
                    anchor = low if is_short else high
                    sl_level = update_sl(sl_level, entry, direction,
                                         {'tp1': True, 'tp2': True},
                                         anchor, atr, tp1=tp1)

        hit3 = low <= tp3 if is_short else high >= tp3
        if hit3:
            return 'tp3', float(tp3), j - i

    last_close = float(df.iloc[end - 1]['Close'])
    return 'timeout', last_close, end - (i + 1)


def simulate_ladder(df: pd.DataFrame, i: int, direction: str,
                    entry: float, tp1: float, tp2: float, tp3: float,
                    sl: float, atr: float, horizon: int,
                    manage_exit: bool) -> tuple[str, float, float, int]:
    """Simulasi ladder: jual 1/3 di TP1, 1/3 di TP2, sisanya di TP3/SL.

    Ini yang paling dekat dengan kenyataan bot: pesan TP1/TP2/TP3
    dipakai untuk menutup sebagian posisi, sisanya ikut aturan SL.

    `manage_exit` menyalakan SL bergerak (breakeven setelah TP1, trailing
    setelah TP2) - bedanya dengan model tanpa aturan keluar inilah yang
    diukur.

    Returns (exit_reason, realized_return, r_multiple, bars_held);
    realized_return dalam satuan harga (bukan persen).
    """
    end = min(i + 1 + horizon, len(df))
    is_short = direction == 'SELL'
    sl_level = float(sl)
    risk = abs(entry - float(sl))
    tp1_hit = False
    tp2_hit = False
    remaining = 1.0
    realized = 0.0

    def ret(price: float) -> float:
        return (price - entry) if not is_short else (entry - price)

    def r_mult(value: float) -> float:
        return value / risk if risk else 0.0

    for j in range(i + 1, end):
        bar = df.iloc[j]
        high = float(bar['High'])
        low = float(bar['Low'])
        close = float(bar['Close'])

        if remaining > 0 and sl_level:
            hit_sl = high >= sl_level if is_short else low <= sl_level
            if hit_sl:
                realized += remaining * ret(_exit_fill_price(sl_level, close, is_short))
                if tp2_hit:
                    reason = 'trailing'
                elif tp1_hit:
                    reason = 'beakeven'
                else:
                    reason = 'sl'
                return reason, realized, r_mult(realized), j - i

        if not tp1_hit and (low <= tp1 if is_short else high >= tp1):
            tp1_hit = True
            realized += (1 / 3) * ret(tp1)
            remaining -= (1 / 3)
            if manage_exit:
                sl_level = update_sl(sl_level, entry, direction,
                                     {'tp1': True, 'tp2': False},
                                     close, atr, tp1=tp1)

        if not tp2_hit and (low <= tp2 if is_short else high >= tp2):
            tp2_hit = True
            realized += (1 / 3) * ret(tp2)
            remaining -= (1 / 3)
            if manage_exit:
                anchor = low if is_short else high
                sl_level = update_sl(sl_level, entry, direction,
                                     {'tp1': True, 'tp2': True},
                                     anchor, atr, tp1=tp1)

        if (low <= tp3) if is_short else (high >= tp3):
            realized += remaining * ret(tp3)
            remaining = 0.0
            return 'tp3', realized, r_mult(realized), j - i

    realized += remaining * ret(float(df.iloc[end - 1]['Close']))
    return 'timeout', realized, r_mult(realized), end - (i + 1)


def run_backtest(df: pd.DataFrame, market: str = 'stock', timeframe: str = '60',
                 horizon: int = 40, cooldown_bars: int = 12,
                 features: Optional[Dict[str, bool]] = None,
                 exit_model: str = 'tp1') -> List[Dict[str, Any]]:
    """Jalankan mesin sinyal bar demi bar; kembalikan daftar trade.

    features: flag mesin yang di-set selama backtest lalu dikembalikan ke
    semula (untuk A/B - lihat set_features di signal_service).

    exit_model:
    - 'tp1'    : keluar penuh di TP1 atau SL (model lama, tidak dipakai
                 untuk mengukur aturan keluar).
    - 'signal' : ditahan sampai TP3/SL/timeout, seperti bot live.
    - 'ladder' : jual 1/3 di TP1, 1/3 di TP2, sisanya di TP3/SL - paling
                 dekat dengan pemakaian bot (partial profit saat TP kena).
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

            m = get_tPSL_multipliers(timeframe)
            tp1_ret = (s['tp1'] - data['price']) / data['price'] * 100
            if direction == 'SELL':
                tp1_ret = -tp1_ret
            sl_ret = (s['sl'] - data['price']) / data['price'] * 100
            if direction == 'SELL':
                sl_ret = -sl_ret

            trade: Dict[str, Any] = {
                'bar': i,
                'signal': signal,
                'quality': s.get('quality', 'WEAK'),
                'buy_score': s.get('buy_score', 0),
                'sell_score': s.get('sell_score', 0),
                'htf_trend': data.get('htf_trend'),
                'regime': s.get('regime', 'none'),
                'tp1_ret_pct': tp1_ret,
                'sl_ret_pct': sl_ret,
                'risk_reward': abs(tp1_ret / sl_ret) if sl_ret else 0,
                'max_tp_mult': m['tp1'],
            }

            if exit_model == 'signal':
                reason, exit_price, bars = simulate_exit(
                    df, i, direction, data['price'],
                    float(s['tp1']), float(s['tp2']), float(s['tp3']),
                    float(s['sl']), float(data.get('atr') or 0), horizon,
                    manage_exit=FEATURES['exit_mgmt'])
                risk = abs(float(s['sl']) - data['price'])
                if direction == 'SELL':
                    r_mult = (data['price'] - exit_price) / risk if risk else 0.0
                else:
                    r_mult = (exit_price - data['price']) / risk if risk else 0.0
                trade.update({
                    'outcome': reason,
                    'bars': bars,
                    'exit_price': exit_price,
                    'r_multiple': r_mult,
                    'exit_ret_pct': ((exit_price - data['price']) / data['price'] * 100
                                     if direction == 'BUY'
                                     else (data['price'] - exit_price) / data['price'] * 100),
                })
            elif exit_model == 'ladder':
                reason, realized, r_mult, bars = simulate_ladder(
                    df, i, direction, data['price'],
                    float(s['tp1']), float(s['tp2']), float(s['tp3']),
                    float(s['sl']), float(data.get('atr') or 0), horizon,
                    manage_exit=FEATURES['exit_mgmt'])
                trade.update({
                    'outcome': reason,
                    'bars': bars,
                    'exit_price': data['price'] + realized,
                    'r_multiple': r_mult,
                    'exit_ret_pct': realized / data['price'] * 100,
                })
            else:
                outcome, bars = simulate_outcome(df, i, direction,
                                                 float(s['tp1']), float(s['sl']),
                                                 horizon)
                trade.update({'outcome': outcome, 'bars': bars})

            trades.append(trade)
    finally:
        if saved:
            set_features(**saved)
    return trades


def summarize(trades: List[Dict[str, Any]], fee_pct: float = 0.0) -> pd.DataFrame:
    """Rekap per sinyal & kualitas: win rate, ekspektansi neto per trade.

    Dua model exit didukung:

    - model lama ('tp1'): keluar penuh di TP1 (profit) atau SL (rugi).
      Timeout = 0.
    - model 'signal': keluar di TP3/SL/timeout seperti bot live. PnL
      diambil dari harga exit, dan expectancy juga dihitung dalam satuan R
      (1R = jarak entry ke SL awal) supaya bisa dibandingkan antar simbol
      yang punya jarak SL berbeda-beda.
    """
    if not trades:
        return pd.DataFrame()

    live_model = 'exit_ret_pct' in trades[0]

    def pnl(t: Dict[str, Any]) -> float:
        if live_model:
            return float(t.get('exit_ret_pct', 0.0)) - fee_pct
        if t['outcome'] == 'tp1':
            return t['tp1_ret_pct'] - fee_pct
        if t['outcome'] == 'sl':
            return t['sl_ret_pct'] - fee_pct
        return 0.0

    def is_win(t: Dict[str, Any]) -> bool:
        if live_model:
            # Trade yang masih hidup (timeout) belum diputuskan - jangan
            # masuk hitungan win/loss.
            if t['outcome'] == 'timeout':
                return False
            # Win/loss ditentukan dari hasil P/L, bukan dari jenis exit.
            # Exit breakeven/trailing bisa menutup di harga yang lebih
            # rendah dari entry (harga meloncat melewati level SL), jadi
            # jenis exit tidak boleh jadi penentu: trade yang sama bisa
            # tercatat 'sl' di satu lengan A/B dan 'beakeven' di lengan
            # lain - win rate-nya jadi tidak bisa dibandingkan.
            return float(t.get('exit_ret_pct', 0.0)) > 0
        return t['outcome'] == 'tp1'

    def is_loss(t: Dict[str, Any]) -> bool:
        if live_model:
            if t['outcome'] == 'timeout':
                return False
            # Semua exit yang sudah menutup posisi masuk hitungan: yang
            # tutup di profit = win, sisanya (rugi atau impas) = loss.
            return float(t.get('exit_ret_pct', 0.0)) <= 0
        return t['outcome'] == 'sl'

    rows = []
    df = pd.DataFrame(trades)
    df['pnl'] = df.apply(pnl, axis=1)
    if live_model:
        df['win'] = df.apply(is_win, axis=1)
        df['loss'] = df.apply(is_loss, axis=1)
    else:
        df['win'] = df['outcome'] == 'tp1'
        df['loss'] = df['outcome'] == 'sl'

    # Kolom 'tp1'/'sl' di model lama memang apa adanya; di model live
    # isinya sudah bukan 'kena TP1' dan 'kena SL', tapi win dan loss -
    # judulnya ikut diganti supaya tidak menyesatkan.
    win_col, loss_col = ('win', 'loss') if live_model else ('tp1', 'sl')

    for (signal, quality), g in df.groupby(['signal', 'quality']):
        n = len(g)
        wins = int(g['win'].sum())
        losses = int(g['loss'].sum())
        timeouts = int((g['outcome'] == 'timeout').sum())
        decided = wins + losses
        row = {
            'signal': signal,
            'quality': quality,
            'n': n,
            win_col: f"{wins / n * 100:.0f}%",
            loss_col: f"{losses / n * 100:.0f}%",
            'timeout': f"{timeouts / n * 100:.0f}%",
            'win_rate_decided': f"{wins / decided * 100:.0f}%" if decided else '-',
            'avg_RR': f"{g['risk_reward'].mean():.2f}",
            'exp_net_%': f"{g['pnl'].mean():+.2f}",
            'total_pnl_%': f"{g['pnl'].sum():+.1f}",
        }
        if live_model:
            row['exp_R'] = f"{g['r_multiple'].mean():+.2f}"
        rows.append(row)
    out = pd.DataFrame(rows)
    # Total keseluruhan di baris terakhir
    wins = int(df['win'].sum())
    losses = int(df['loss'].sum())
    decided = wins + losses
    total = {
        'signal': 'TOTAL', 'quality': '-', 'n': len(df),
        win_col: f"{wins / len(df) * 100:.0f}%",
        loss_col: f"{losses / len(df) * 100:.0f}%",
        'timeout': f"{int((df['outcome'] == 'timeout').sum()) / len(df) * 100:.0f}%",
        'win_rate_decided': f"{wins / decided * 100:.0f}%" if decided else '-',
        'avg_RR': f"{df['risk_reward'].mean():.2f}",
        'exp_net_%': f"{df['pnl'].mean():+.2f}",
        'total_pnl_%': f"{df['pnl'].sum():+.1f}",
    }
    if live_model:
        total['exp_R'] = f"{df['r_multiple'].mean():+.2f}"
    out.loc[len(out)] = total
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
        'htf_trend', 'sr_tpsl', 'vol_gate', 'degraded_cap', 'vwap_crypto',
        'regime_gate', 'exit_mgmt')}
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


def compare_regime_gate(df: pd.DataFrame, market: str = 'stock',
                         timeframe: str = '60', horizon: int = 40,
                         cooldown_bars: int = 12,
                         fee_pct: float = 0.0) -> Dict[str, Any]:
    """A/B regime gate (ADX): aturan trend/range aktif vs tidak.

    Pakai model exit 'tp1' supaya win rate-nya bisa dibandingkan langsung
    dengan angka yang sudah pernah dilaporkan (klasik vs v2).
    """
    off = run_backtest(df, market, timeframe, horizon, cooldown_bars,
                       features={'regime_gate': False})
    on = run_backtest(df, market, timeframe, horizon, cooldown_bars,
                      features={'regime_gate': True})
    return {
        'tanpa_regime': summarize(off, fee_pct),
        'dengan_regime': summarize(on, fee_pct),
        'trades_off': off,
        'trades_on': on,
    }


def compare_exit_mgmt(df: pd.DataFrame, market: str = 'stock',
                      timeframe: str = '60', horizon: int = 40,
                      cooldown_bars: int = 12,
                      fee_pct: float = 0.0,
                      exit_model: str = 'ladder') -> Dict[str, Any]:
    """A/B aturan keluar: SL diam vs SL bergerak (breakeven + trailing).

    Dua lengan memakai model exit yang sama (ladder: partial profit di
    TP1/TP2), jadi bedanya murni dari aturan SL - bukan dari sinyal entry.
    """
    off = run_backtest(df, market, timeframe, horizon, cooldown_bars,
                       features={'exit_mgmt': False}, exit_model=exit_model)
    on = run_backtest(df, market, timeframe, horizon, cooldown_bars,
                      features={'exit_mgmt': True}, exit_model=exit_model)
    return {
        'tanpa_exit_mgmt': summarize(off, fee_pct),
        'dengan_exit_mgmt': summarize(on, fee_pct),
        'trades_off': off,
        'trades_on': on,
    }
