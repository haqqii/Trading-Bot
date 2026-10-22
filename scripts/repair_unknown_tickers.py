"""Perbaiki baris 'signals' yang ticker-nya tertimpa 'UNKNOWN'.

Key signal punya format {TYPE}_{TICKER}_{USER_ID}, jadi ticker bisa
dipulihkan dari key. Dipakai sekali untuk memperbaiki data yang sudah
rusak oleh bug auto-save.
"""
import json
import os
import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else '/trading/data/ochobot.db'
STATE_FILE = sys.argv[2] if len(sys.argv) > 2 else '/trading/data/ticker_unavailable.json'

PLACEHOLDERS = {'', 'UNKNOWN', 'NONE', 'NULL', 'N/A', '-'}
PLACEHOLDER_SET = {p.upper() for p in PLACEHOLDERS}


def ticker_from_key(key):
    """Ambil ticker dari key signal: STOCK_AREA_7730539872 -> AREA."""
    parts = key.split('_')
    if len(parts) < 3:
        return None
    ticker = parts[1]
    if not ticker or ticker.upper() in PLACEHOLDER_SET:
        return None
    return ticker


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    fixed = 0
    for row in conn.execute('SELECT id, key, ticker FROM signals').fetchall():
        current = (row['ticker'] or '').upper()
        if current not in PLACEHOLDER_SET:
            continue
        ticker = ticker_from_key(row['key'])
        if not ticker:
            print(f'  LEWATI {row["key"]}: ticker tidak bisa dipulihkan dari key')
            continue
        conn.execute('UPDATE signals SET ticker = ? WHERE id = ?', (ticker, row['id']))
        print(f'  {row["key"]}: {row["ticker"]} -> {ticker}')
        fixed += 1
    conn.commit()

    print(f'\nSinyal diperbaiki: {fixed}')
    print('Isi tabel signals sekarang:')
    for row in conn.execute('SELECT key, ticker, asset_type FROM signals'):
        print(f'  {row["key"]:30s} ticker={row["ticker"]:10s} {row["asset_type"]}')
    conn.close()

    # Bersihkan placeholder dari registry ticker tanpa data.
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cleaned = {k: v for k, v in data.items()
                   if k.upper() not in PLACEHOLDER_SET}
        if len(cleaned) != len(data):
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cleaned, f, indent=2, sort_keys=True)
            print(f'\nPlaceholder dihapus dari {STATE_FILE}')
        else:
            print(f'\n{STATE_FILE}: tidak ada placeholder')
        print(f'Isi registry: {cleaned}')


if __name__ == '__main__':
    main()
