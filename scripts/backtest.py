"""CLI backtest mesin sinyal - ukur win rate, jangan menebak.

Menjalankan mesin sinyal bot di atas data historis (yfinance, di luar
limiter bot - script ini berdiri sendiri) dan melaporkan seberapa sering
TP1 tersentuh sebelum SL, per kualitas sinyal. Secara default membandingkan
mesin klasik vs mesin v2 (A/B) supaya dampak tiap fitur terlihat.

Contoh:
    python scripts/backtest.py --market crypto --tickers BTC-USD,ETH-USD --interval 1h --period 90d
    python scripts/backtest.py --market stock --tickers BBCA.JK,BBRI.JK --interval 1h --period 6mo
    python scripts/backtest.py --market crypto --tickers BTC-USD --interval 1h --period 90d --no-compare

Catatan interval yfinance: '1h' cukup sampai 730 hari; '15m' hanya 60 hari;
'5m' hanya 60 hari dan tiap request terbatas 1 ticker.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from config.settings import INTERVAL_TO_KEY


def fetch_df(ticker: str, interval: str, period: str) -> pd.DataFrame | None:
    """Ambil OHLCV historis lewat yfinance langsung (bukan limiter bot)."""
    import yfinance as yf

    df = yf.Ticker(ticker).history(interval=interval, period=period,
                                   auto_adjust=False)
    if df.empty:
        return None
    df = df.rename(columns=str.title)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--market', choices=['stock', 'crypto'], default='crypto')
    p.add_argument('--tickers', required=True,
                   help='Daftar ticker dipisah koma (BTC-USD atau BBCA.JK)')
    p.add_argument('--interval', default='1h')
    p.add_argument('--period', default='90d')
    p.add_argument('--horizon', type=int, default=40,
                   help='Bar maksimum simulasi maju untuk mencari TP1/SL')
    p.add_argument('--cooldown', type=int, default=12,
                   help='Bar minimum antara dua sinyal searah (anti duplikat)')
    p.add_argument('--fee', type=float, default=None,
                   help='Fee per putaran dalam persen (default: 0.3 saham IDX, 0.16 crypto)')
    p.add_argument('--no-compare', action='store_true',
                   help='Jangan jalankan perbandingan; cukup mesin v2 saja')
    p.add_argument('--compare', choices=['features', 'regime', 'exit', 'gate'],
                   default='features',
                   help=('A/B yang dijalankan: features (klasik vs v2), '
                         'regime (ADX trend/range on/off), '
                         'exit (SL diam vs SL bergerak), '
                         'gate (semua sinyal vs gerbang kualitas/arah)'))
    p.add_argument('--min-quality', choices=['WEAK', 'MODERATE', 'STRONG'],
                   default=None,
                   help=('Gerbang kualitas: kualitas minimum yang dikirim. '
                         'Tanpa flag ini semua kualitas lewat; mode --compare gate '
                         'memakai STRONG kalau tidak diisi'))
    p.add_argument('--long-only', action=argparse.BooleanOptionalAction,
                   default=None,
                   help=('Gerbang arah: hanya sinyal BUY. Tanpa flag ini kedua '
                         'arah lewat; mode --compare gate memakai BUY saja '
                         'kalau tidak diisi'))
    p.add_argument('--htf-align', action=argparse.BooleanOptionalAction,
                   default=None,
                   help=('Gerbang tren searah: BUY hanya kalau tren HTF up, '
                         'SELL hanya kalau tren HTF down'))
    p.add_argument('--htf-counter', action=argparse.BooleanOptionalAction,
                   default=None,
                   help=('Gerbang tren berlawanan: BUY hanya kalau tren HTF '
                         'down (beli dip), SELL hanya kalau tren HTF up'))
    p.add_argument('--max-sl-pct', type=float, default=None,
                   help=('Batas jarak SL dari entry (persen) - SL yang lebih '
                         'jauh dicapit ke batas ini'))
    p.add_argument('--trend-gate', action='store_true',
                   help=('Pakai aturan gerbang tren yang sama dengan bot live '
                         '(utils/strategy): di timeframe harian hanya sinyal '
                         'SELL saat tren harian naik'))
    p.add_argument('--exit-model', choices=['tp1', 'signal', 'ladder'], default='tp1',
                   help=("Model exit simulasi: 'tp1' (keluar di TP1, model lama), "
                         "'signal' (tahan sampai TP3/SL) atau 'ladder' "
                         "(partial profit di TP1/TP2 - paling dekat dengan bot)"))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    from utils.backtest import (
        run_backtest, summarize, compare_features, compare_regime_gate,
        compare_exit_mgmt, compare_gate,
    )

    fee_pct = args.fee if args.fee is not None else (0.30 if args.market == 'stock' else 0.16)
    timeframe = INTERVAL_TO_KEY.get(args.interval, '60')
    # A/B aturan keluar dan gerbang memakai model 'ladder' (partial profit
    # di TP1/TP2), karena di model itu efeknya terlihat paling dekat dengan
    # pemakaian bot.
    exit_model = 'ladder' if args.compare in ('exit', 'gate') else args.exit_model
    # Gerbang tren: 'with' (searah tren), 'counter' (berlawanan), atau None
    htf_align = None
    if args.htf_counter:
        htf_align = 'counter'
    elif args.htf_align:
        htf_align = True

    arms: dict[str, list[pd.DataFrame]] = {}
    raw_trades = 0

    for ticker in [t.strip() for t in args.tickers.split(',') if t.strip()]:
        print(f"\n=== {ticker} [{args.interval} / {args.period}] ===")
        df = fetch_df(ticker, args.interval, args.period)
        if df is None or len(df) < 150:
            print(f"  Data tidak cukup ({0 if df is None else len(df)} bar), lewati.")
            continue
        print(f"  {len(df)} bar, horizon {args.horizon} bar, fee {fee_pct}%/putaran")

        pairs: list[tuple[str, pd.DataFrame]] = []
        if args.no_compare:
            trades = run_backtest(df, args.market, timeframe,
                                  args.horizon, args.cooldown,
                                  exit_model=exit_model,
                                  min_quality=args.min_quality,
                                  long_only=bool(args.long_only),
                                  htf_align=htf_align,
                                  trend_gate=args.trend_gate,
                                  max_sl_pct=args.max_sl_pct)
            table = summarize(trades, fee_pct)
            pairs = [('v2', table)]
            raw_trades += len(trades)
        elif args.compare == 'features':
            result = compare_features(df, args.market, timeframe,
                                      args.horizon, args.cooldown, fee_pct)
            table = result['v2']
            pairs = [('klasik', result['classic']), ('v2', result['v2'])]
            raw_trades += len(result['v2_trades'])
        elif args.compare == 'regime':
            result = compare_regime_gate(df, args.market, timeframe,
                                          args.horizon, args.cooldown, fee_pct)
            table = result['dengan_regime']
            pairs = [('tanpa_regime', result['tanpa_regime']),
                     ('dengan_regime', result['dengan_regime'])]
            raw_trades += len(result['trades_on'])
        elif args.compare == 'gate':
            result = compare_gate(df, args.market, timeframe,
                                  args.horizon, args.cooldown, fee_pct,
                                  exit_model=exit_model,
                                  min_quality=args.min_quality or 'STRONG',
                                  long_only=True if args.long_only is None
                                  else args.long_only,
                                  htf_align=htf_align,
                                  max_sl_pct=args.max_sl_pct)
            table = result['gerbang']
            pairs = [('semua', result['semua']),
                     ('gerbang', result['gerbang'])]
            raw_trades += len(result['trades_gerbang'])
        else:
            result = compare_exit_mgmt(df, args.market, timeframe,
                                       args.horizon, args.cooldown, fee_pct)
            table = result['dengan_exit_mgmt']
            pairs = [('tanpa_exit_mgmt', result['tanpa_exit_mgmt']),
                     ('dengan_exit_mgmt', result['dengan_exit_mgmt'])]
            raw_trades += len(result['trades_on'])

        if table.empty:
            print("  Tidak ada sinyal terbentuk.")
            continue
        print(table.to_string(index=False))
        for label, tbl in pairs:
            arms.setdefault(label, []).append(tbl)

    if not arms:
        print("\nTidak ada data yang bisa dibacktest.")
        return 1

    print("\n=== GABUNGAN (baris TOTAL tiap ticker dirata-rata) ===")
    merged = []
    for label, tables in arms.items():
        total = _merge_totals(tables)
        if total.empty:
            continue
        merged.append(total.rename(
            columns=lambda c: f"{label}_{c}" if c != 'signal' else c))
    if merged:
        print(pd.concat(merged, axis=1).to_string(index=False))

    print(f"\nTotal trade: {raw_trades}")
    if exit_model == 'signal':
        print("Asumsi (model 'signal'): posisi ditahan sampai TP3/SL/timeout; "
              "SL dicek sebelum TP di bar yang sama (pesimis); "
              "1R = jarak entry ke SL awal.")
    elif exit_model == 'ladder':
        print("Asumsi (model 'ladder'): 1/3 posisi ditutup di TP1, 1/3 di TP2, "
              "sisanya di TP3/SL/timeout; SL dicek sebelum TP di bar yang sama "
              "(pesimis); 1R = jarak entry ke SL awal.")
    else:
        print("Asumsi (model 'tp1'): keluar penuh di TP1/SL; bar yang menyentuh "
              "dua-duanya dihitung SL (pesimis); timeout = 0.")
    return 0


def _merge_totals(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """Ambil baris TOTAL tiap tabel ticker, lalu rata-ratakan antar ticker."""
    rows = []
    for t in tables:
        if not t.empty:
            total = t[t['signal'] == 'TOTAL']
            if not total.empty:
                rows.append(total.iloc[0])
    if not rows:
        return pd.DataFrame()

    avg: dict = {'signal': 'TOTAL(avg)'}
    for col in rows[0].index:
        if col == 'signal':
            continue
        text = str(rows[0][col])
        suffix = '%' if text.endswith('%') else ''
        numeric = pd.to_numeric(
            pd.Series([str(r[col]).rstrip('%') for r in rows]),
            errors='coerce').dropna()
        if len(numeric):
            decimals = 2 if '.' in text and not suffix else 0
            avg[col] = f"{numeric.mean():.{decimals}f}{suffix}"
        else:
            avg[col] = text
    return pd.DataFrame([avg])


if __name__ == '__main__':
    sys.exit(main())
