"""Isi kolom `signals.name` untuk baris yang masih kosong.

Nama aset diambil dari sumber yang sama dengan yang dipakai scanner:
- crypto: services.crypto_service.crypto_pairs (nama dari CoinGecko)
- stock: services.stock_service.load_stocks() (nama dari TradingView/IDX)

Kalau nama tidak ditemukan, pakai ticker sebagai fallback supaya kolomnya
tidak kosong lagi (kolom kosong bikin notifikasi menampilkan ticker mentah).

Usage (di dalam container):
    python /trading/scripts/backfill_signal_names.py
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else '/trading/data/ochobot.db'


def _stock_name(stocks, ticker):
    """Ambil nama saham dari cache (nilainya bisa string atau dict)."""
    value = stocks.get(ticker) if isinstance(stocks, dict) else None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get('name') or value.get('description') or ''
    return ''


def main():
    from services.crypto_service import crypto_service
    from services.stock_service import stock_service

    # Nama crypto (CoinGecko). Kalau gagal, crypto_pairs berisi daftar
    # hardcoded - tetap dipakai, ticker jadi fallback.
    try:
        crypto_service.load_crypto_pairs()
    except Exception as e:
        print(f'Peringatan: gagal memuat daftar crypto: {e}')
    crypto_names = crypto_service.crypto_pairs or {}

    try:
        stocks = stock_service.load_stocks() or {}
    except Exception as e:
        print(f'Peringatan: gagal memuat daftar saham: {e}')
        stocks = {}

    print(f'Nama crypto: {len(crypto_names)} | Nama saham: {len(stocks)}')

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, key, ticker, asset_type, extra_data FROM signals "
        "WHERE name IS NULL OR name = ''"
    ).fetchall()
    print(f'Sinyal dengan nama kosong: {len(rows)}\n')

    updated = 0
    for row in rows:
        ticker = row['ticker']
        name = ''
        if row['asset_type'] == 'crypto':
            name = crypto_names.get(ticker) or ''
        if not name:
            name = _stock_name(stocks, ticker)
        if not name:
            try:
                extra = json.loads(row['extra_data']) if row['extra_data'] else {}
            except Exception:
                extra = {}
            name = extra.get('name') or ''
        if not name:
            name = ticker

        conn.execute("UPDATE signals SET name = ? WHERE id = ?", (name, row['id']))
        print(f'  {row["key"]}: -> {name}')
        updated += 1

    conn.commit()
    print(f'\n{updated} sinyal diperbarui')
    return 0


if __name__ == '__main__':
    sys.exit(main())
