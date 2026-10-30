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
                   help='Jangan jalankan mesin klasik; cukup mesin v2 saja')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    from utils.backtest import run_backtest, summarize, compare_features

    fee_pct = args.fee if args.fee is not None else (0.30 if args.market == 'stock' else 0.16)
    timeframe = INTERVAL_TO_KEY.get(args.interval, '60')

    all_classic: list[pd.DataFrame] = []
    all_v2: list[pd.DataFrame] = []
    raw_trades = 0

    for ticker in [t.strip() for t in args.tickers.split(',') if t.strip()]:
        print(f"\n=== {ticker} [{args.interval} / {args.period}] ===")
        df = fetch_df(ticker, args.interval, args.period)
        if df is None or len(df) < 150:
            print(f"  Data tidak cukup ({0 if df is None else len(df)} bar), lewati.")
            continue
        print(f"  {len(df)} bar, horizon {args.horizon} bar, fee {fee_pct}%/putaran")

        if args.no_compare:
            trades = run_backtest(df, args.market, timeframe,
                                  args.horizon, args.cooldown)
            table = summarize(trades, fee_pct)
            raw_trades += len(trades)
        else:
            result = compare_features(df, args.market, timeframe,
                                      args.horizon, args.cooldown, fee_pct)
            table = result['v2']
            all_classic.append(result['classic'])
            raw_trades += len(result['v2_trades'])

        if table.empty:
            print("  Tidak ada sinyal terbentuk.")
            continue
        print(table.to_string(index=False))
        all_v2.append(table)

    if not all_v2 and not all_classic:
        print("\nTidak ada data yang bisa dibacktest.")
        return 1

    if not args.no_compare and all_classic:
        print("\n=== GABUNGAN: klasik vs v2 (baris TOTAL tiap ticker dirata-rata) ===")
        classic_total = _merge_totals(all_classic)
        v2_total = _merge_totals(all_v2)
        combined = pd.concat(
            [classic_total.rename(columns=lambda c: f"klasik_{c}" if c != 'signal' else c),
             v2_total.rename(columns=lambda c: f"v2_{c}" if c != 'signal' else c)],
            axis=1)
        print(combined.to_string(index=False))

    print(f"\nTotal trade: {raw_trades}")
    print("Asumsi: keluar penuh di TP1/SL; bar yang menyentuh dua-duanya dihitung SL (pesimis); timeout = 0.")
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
