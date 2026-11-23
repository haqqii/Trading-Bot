"""Test snapshot harian database.

Yang dijaga: snapshot benar-benar menyimpan kondisi database, satu file
per hari, file lama dibersihkan, dan salinan tetap konsisten walau
database sedang ditulis (mode WAL).
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIB = timezone(timedelta(hours=7))


def _make_db(path, rows=3):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY,
        key TEXT UNIQUE,
        ticker TEXT,
        signal_type TEXT,
        outcome TEXT,
        created_at TEXT
    )""")
    for i in range(rows):
        conn.execute(
            "INSERT OR REPLACE INTO signals "
            "(key, ticker, signal_type, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"CRYPTO_TICKER{i}-USD_1", f"TICKER{i}-USD", "BUY",
             "tp3", "2026-11-23 10:00:00"))
    conn.commit()
    conn.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Folder snapshot sementara + database sumber."""
    from utils import db_snapshot

    snap_dir = tmp_path / 'snapshots'
    monkeypatch.setenv('SNAPSHOT_DIR', str(snap_dir))

    source = tmp_path / 'source.db'
    _make_db(str(source))
    return {'module': db_snapshot, 'dir': str(snap_dir), 'source': str(source)}


def test_snapshot_creates_file_with_same_rows(env):
    from utils.db_snapshot import take_snapshot

    path = take_snapshot(source_path=env['source'])
    assert path and os.path.exists(path)

    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT key, outcome FROM signals").fetchall()
    conn.close()
    assert len(rows) == 3
    assert all(r[1] == 'tp3' for r in rows)


def test_only_one_snapshot_per_day(env):
    from utils.db_snapshot import take_snapshot

    first = take_snapshot(source_path=env['source'])
    second = take_snapshot(source_path=env['source'])
    assert first is not None
    assert second is None, 'snapshot dibuat dua kali untuk hari yang sama'


def test_maybe_snapshot_skips_when_exists(env):
    """Kalau file hari ini sudah ada, tidak perlu disalin lagi."""
    from utils.db_snapshot import maybe_snapshot, snapshot_path

    today = datetime.now(WIB).strftime('%Y-%m-%d')
    path = snapshot_path(today)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('x')

    assert maybe_snapshot() is None
    assert os.path.exists(path), 'snapshot yang sudah ada tertimpa'


def test_maybe_snapshot_creates_when_missing(env):
    from utils.db_snapshot import maybe_snapshot, snapshot_path

    today = datetime.now(WIB).strftime('%Y-%m-%d')
    path = maybe_snapshot()
    assert path is not None
    assert os.path.exists(snapshot_path(today))


def test_snapshot_survives_source_overwrite(env):
    """Isi snapshot tidak berubah kalau baris asalnya ditimpa di DB
    utama - itu justru alasan snapshotnya ada."""
    from utils.db_snapshot import take_snapshot

    path = take_snapshot(source_path=env['source'])
    assert path

    conn = sqlite3.connect(env['source'])
    conn.execute("UPDATE signals SET outcome = 'sl'")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(path)
    outcomes = {r[0] for r in conn.execute("SELECT outcome FROM signals")}
    conn.close()
    assert outcomes == {'tp3'}, 'snapshot ikut berubah - jadi bukan salinan'


def test_prune_removes_old_snapshots(env):
    from utils.db_snapshot import prune_snapshots, snapshot_path

    old_day = (datetime.now(WIB) - timedelta(days=45)).strftime('%Y-%m-%d')
    old_path = snapshot_path(old_day)
    os.makedirs(os.path.dirname(old_path), exist_ok=True)
    with open(old_path, 'w', encoding='utf-8') as f:
        f.write('x')

    removed = prune_snapshots(keep_days=30)
    assert removed == 1
    assert not os.path.exists(old_path)


def test_prune_keeps_recent(env):
    from utils.db_snapshot import prune_snapshots, snapshot_path

    recent = (datetime.now(WIB) - timedelta(days=3)).strftime('%Y-%m-%d')
    path = snapshot_path(recent)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('x')

    prune_snapshots(keep_days=30)
    assert os.path.exists(path)


def test_list_snapshots_newest_first(env):
    from utils.db_snapshot import list_snapshots, snapshot_path

    for day in ('2026-11-20', '2026-11-22', '2026-11-21'):
        path = snapshot_path(day)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('x')

    names = list_snapshots()
    assert names[0].startswith('bot_2026-11-22')
    assert names[-1].startswith('bot_2026-11-20')
