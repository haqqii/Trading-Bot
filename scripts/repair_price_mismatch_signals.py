"""Tandai sinyal yang harganya tidak cocok dengan CoinGecko sebagai 'invalid'.

Yahoo Finance punya namespace ticker sendiri ({SYM}-USD). Simbol yang sama
belum tentu aset yang sama: JUP-USD di Yahoo = aset lain ($0.00026) sementara
JUP di CoinGecko = Jupiter ($0.30). Sinyal yang dibangun di atas harga seperti
itu salah sasaran, jadi harus dimarking supaya tidak lagi dipantau TP/SL.

Script ini dibandingkan dengan harga acuan CoinGecko yang dipakai bot, jadi
pemetaan simbol -> id persis sama dengan yang dipakai scanner.

Usage (di dalam container):
    python /trading/scripts/repair_price_mismatch_signals.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else '/trading/data/ochobot.db'

# Toleransi yang sama dengan services/crypto_service.py
RATIO_LIMIT = 5.0


def main():
    import sqlite3

    from services.crypto_service import crypto_service

    crypto_service.load_crypto_pairs()
    crypto_service.load_price_reference()
    ref = crypto_service.cg_price_ref
    print(f'Harga acuan: {len(ref)} pair')

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, key, ticker, price, outcome FROM signals "
        "WHERE outcome IS NULL OR outcome = 'open'"
    ).fetchall()
    print(f'Sinyal aktif yang diperiksa: {len(rows)}\n')

    marked = 0
    for row in rows:
        ticker = row['ticker']
        price = row['price']
        reference = ref.get(ticker)
        if not reference or not price:
            continue
        ratio = price / reference
        if 1 / RATIO_LIMIT <= ratio <= RATIO_LIMIT:
            continue
        print(f'  {row["key"]}: harga={price:.8f} acuan={reference:.8f} '
              f'rasio={ratio:.4f}x -> INVALID')
        conn.execute(
            "UPDATE signals SET outcome = 'invalid', closed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row['id'])
        )
        marked += 1

    conn.commit()
    print(f'\n{marked} sinyal ditandai invalid')


if __name__ == '__main__':
    main()
