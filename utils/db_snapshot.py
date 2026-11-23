"""Snapshot harian database bot.

Kenapa perlu: baris sinyal di database di-key per (ticker, user) dan
ditulis ulang dengan INSERT OR REPLACE. Kalau ticker yang sama dapat
sinyal lagi, baris lamanya - beserta outcome TP/SL-nya - tertimpa.
Untuk menilai hasil eksperimen beberapa hari, itu data yang hilang.

Snapshot harian menyimpan kondisi database tiap hari, jadi versi
sebelumnya masih bisa dibaca ulang dari file snapshot.

Cara kerja:
- Satu file per hari: ``bot_YYYY-MM-DD.db`` di folder ``snapshots/``.
  File-nya diambil pada eksekusi pertama bot di hari itu, jadi isinya
  adalah kondisi database sampai saat itu (termasuk semua hari
  sebelumnya). Selisih antar file = perubahan hari itu.
- Salinan dibuat dengan API backup SQLite, bukan copy file biasa.
  Database jalan dengan mode WAL, jadi copy file mentah bisa
  menghasilkan file yang tidak konsisten (baris yang belum masuk ke
  file utama hilang).
- Snapshot lama (lebih dari KEEP_DAYS hari) dibersihkan sendiri.
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

# Nama folder snapshot di root repo bot. Di dalam container, root repo
# (/trading) di-bind-mount ke host, jadi snapshot-nya kelihatan langsung
# dari PC dan tidak hilang saat container di-recreate.
SNAPSHOT_DIRNAME = 'snapshots'

# Berapa hari snapshot lama disimpan.
KEEP_DAYS = 30


def snapshot_dir() -> str:
    """Folder tujuan snapshot.

    Bisa diarahkan lewat env SNAPSHOT_DIR (dipakai test).
    """
    custom = os.environ.get('SNAPSHOT_DIR')
    if custom:
        return custom
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, SNAPSHOT_DIRNAME)


def snapshot_path(day: str) -> str:
    """Path file snapshot untuk tanggal tertentu (YYYY-MM-DD)."""
    return os.path.join(snapshot_dir(), f'bot_{day}.db')


def list_snapshots() -> list[str]:
    """Daftar file snapshot yang ada, dari yang terbaru."""
    folder = snapshot_dir()
    if not os.path.isdir(folder):
        return []
    return sorted(
        (f for f in os.listdir(folder) if f.startswith('bot_') and f.endswith('.db')),
        reverse=True,
    )


def take_snapshot(now: datetime | None = None,
                  keep_days: int = KEEP_DAYS,
                  source_path: str | None = None) -> str | None:
    """Salin database ke file snapshot untuk hari ini.

    Returns:
        Path snapshot yang dibuat, atau None kalau untuk hari ini sudah
        ada (snapshot cukup satu per hari).
    """
    from db import db

    day = (now or datetime.now(WIB)).strftime('%Y-%m-%d')
    target = snapshot_path(day)
    if os.path.exists(target):
        return None

    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)

    source = sqlite3.connect(source_path or db.db_path, timeout=30)
    try:
        dest = sqlite3.connect(target)
        try:
            # API backup: salinan konsisten walau database sedang ditulis.
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    size_kb = os.path.getsize(target) // 1024
    logger.info(f"[SNAPSHOT] Database disalin: {target} ({size_kb} KB)")
    return target


def prune_snapshots(keep_days: int = KEEP_DAYS) -> int:
    """Hapus snapshot yang lebih tua dari batas. Balikan: jumlah file dihapus."""
    removed = 0
    cutoff = (datetime.now(WIB) - timedelta(days=keep_days)).strftime('%Y-%m-%d')
    for name in list_snapshots():
        day = name[len('bot_'):-len('.db')]
        if day < cutoff:
            try:
                os.remove(os.path.join(snapshot_dir(), name))
                removed += 1
            except OSError as err:
                logger.warning(f"[SNAPSHOT] Gagal hapus {name}: {err}")
    return removed


def maybe_snapshot(now: datetime | None = None) -> str | None:
    """Buat snapshot kalau untuk hari ini belum ada.

    Dipakai job harian: memeriksa dulu, baru menyalin - supaya job yang
    jalan tiap setengah jam tidak menyalin database berulang.
    """
    day = (now or datetime.now(WIB)).strftime('%Y-%m-%d')
    if os.path.exists(snapshot_path(day)):
        return None
    path = take_snapshot(now=now)
    if path:
        prune_snapshots()
    return path
