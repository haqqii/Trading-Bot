import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else '/trading/data/ochobot.db'
c = sqlite3.connect(path)
c.row_factory = sqlite3.Row

# Sinyal yang sudah tutup tidak dimuat lagi ke memori, jadi auto-save tidak
# pernah menyentuhnya. Samakan manual supaya kolomnya konsisten.
rows = c.execute(
    'SELECT key, target_price, tp3 FROM signals '
    'WHERE tp3 IS NOT NULL AND tp3 > 0 AND (target_price IS NULL OR target_price != tp3)'
).fetchall()
for r in rows:
    print(f'  {r["key"]}: target_price {r["target_price"]} -> {r["tp3"]}')
    c.execute('UPDATE signals SET target_price = ? WHERE key = ?',
              (r['tp3'], r['key']))
c.commit()
print(f'Diperbarui: {len(rows)} baris')
