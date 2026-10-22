import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else '/trading/data/ochobot.db'
c = sqlite3.connect(path)
c.row_factory = sqlite3.Row
for r in c.execute('select key, ticker, price, target_price, tp1, tp2, tp3, '
                   'stop_loss, outcome from signals'):
    d = dict(r)
    print(f"{d['key']:30s} entry={d['price']:>12} target_price={d['target_price']:>12} "
          f"tp1={d['tp1']:>12} tp2={d['tp2']:>12} tp3={d['tp3']:>12} "
          f"sl={d['stop_loss']:>12} {d['outcome']}")
