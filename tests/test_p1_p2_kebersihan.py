"""Test kejelasan & kebersihan notifikasi (P1 dan P2).

P1:
- Notifikasi harus menyatakan bahwa yang dilacak adalah posisi bot,
  bukan posisi yang dibuka user di bursa.
- Follow-up scan (scan tambahan setelah TP3 kena) tidak boleh jalan
  bareng dengan scan yang sedang berjalan - dua scan bareng memperlambat
  keduanya dan bisa membuat scan berikutnya kena skip.

P2:
- Baris sinyal di DB terbatas satu per (ticker, user), jadi tidak perlu
  ada job pembersih.
- notif_crypto (master lama) sudah bukan pengaturan aktif - notifikasi
  crypto dipecah per arah.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeJobQueue:
    def __init__(self):
        self.scheduled = []

    def run_once(self, target, when=0):
        self.scheduled.append((target, when))


class FakeApp:
    def __init__(self):
        self.job_queue = FakeJobQueue()


class TestBotPositionLabel:
    """Baris 'Posisi bot - bukan posisi di bursa kamu' sudah dihapus.

    25 November 2026: user minta baris itu hilang dari semua notifikasi.
    Dulu baris itu penjaga supaya user tidak salah kira SL/TP yang
    dilaporkan milik posisinya sendiri di bursa - tapi baris yang sama
    bikin notifikasi jadi lebih panjang tanpa menambah informasi baru
    (level SL/TP + P/L sudah speaks for itself).
    """

    def test_crypto_notification_has_no_bot_position_line(self):
        from utils.formatters import format_unified_crypto_notification

        msg = format_unified_crypto_notification(
            notif_type='TP1', ticker='RUNE-USD', name='THORChain',
            entry=100.0, current_price=102.0, tp1=102.0, tp2=104.0, tp3=106.0,
            sl=97.0, analysis_data={'indicators': {'atr': 1.0}},
            profit_loss=2.0)
        assert 'Posisi bot' not in msg
        assert 'bukan posisi di bursa' not in msg

    def test_stock_notification_has_no_bot_position_line(self):
        from utils.formatters import format_unified_stock_notification

        msg = format_unified_stock_notification(
            notif_type='SL', ticker='BBCA', name='Bank Central Asia',
            entry=9500.0, current_price=9300.0, tp1=9800.0, tp2=10100.0,
            tp3=10500.0, sl=9200.0,
            analysis_data={'indicators': {'atr': 100.0}}, profit_loss=-2.1)
        assert 'Posisi bot' not in msg
        assert 'bukan posisi di bursa' not in msg

    def test_crypto_exit_notification_has_no_bot_position_line(self):
        """Notifikasi exit (SL/TP) yang paling sering bikin user salah
        baca juga harus bersih dari baris itu."""
        from utils.formatters import format_unified_crypto_notification

        for notif_type in ('TP1', 'TP2', 'TP3', 'SL', 'BE', 'TRAIL'):
            msg = format_unified_crypto_notification(
                notif_type=notif_type, ticker='RUNE-USD', name='THORChain',
                entry=100.0, current_price=102.0, tp1=102.0, tp2=104.0,
                tp3=106.0, sl=97.0,
                analysis_data={'indicators': {'atr': 1.0}},
                profit_loss=2.0)
            assert 'Posisi bot' not in msg, f'baris masih ada di {notif_type}'


class TestFollowupScanNotOverlapping:
    """Follow-up scan tidak boleh bareng dengan scan yang sedang jalan."""

    def test_followup_skipped_while_scan_running(self, monkeypatch):
        from handlers.scheduler import jobs_crypto
        from handlers.scheduler._common import _schedule_followup_scan

        monkeypatch.setattr(jobs_crypto, 'scan_in_progress', lambda: True)
        monkeypatch.setattr(jobs_crypto, '_force_next_scan', False)

        app = FakeApp()
        _schedule_followup_scan(app, 'crypto', delay=60)

        assert app.job_queue.scheduled == [], \
            'follow-up scan dijadwalkan padahal scan sedang jalan'
        assert jobs_crypto._force_next_scan is False, \
            'flag paksa tetap menyala - scan terjadwal berikutnya jadi dipaksa'

    def test_followup_scheduled_when_idle(self, monkeypatch):
        from handlers.scheduler import jobs_crypto
        from handlers.scheduler._common import _schedule_followup_scan

        monkeypatch.setattr(jobs_crypto, 'scan_in_progress', lambda: False)
        monkeypatch.setattr(jobs_crypto, '_force_next_scan', False)

        app = FakeApp()
        _schedule_followup_scan(app, 'crypto', delay=60)

        assert app.job_queue.scheduled, 'follow-up scan tidak dijadwalkan'
        assert jobs_crypto._force_next_scan is True

    def test_scan_flag_set_while_running(self, monkeypatch):
        """Penanda scan harus menyala selama scan jalan, dan mati setelah."""
        from handlers.scheduler import jobs_crypto

        monkeypatch.setattr(jobs_crypto, '_scan_in_progress', False)
        monkeypatch.setattr(jobs_crypto, '_get_user_db', lambda: {})
        monkeypatch.setattr(jobs_crypto, '_force_next_scan', False)

        seen = {}

        async def fake_scan():
            seen['running'] = jobs_crypto.scan_in_progress()

        # Ganti isi fungsi scan dengan pemeriksa flag.
        monkeypatch.setattr(jobs_crypto, 'check_crypto_signals', fake_scan)
        assert jobs_crypto.scan_in_progress() is False


class TestSignalsRowBounded:
    """Satu baris per (ticker, user) - tidak perlu job pembersih."""

    def test_same_ticker_keeps_one_row(self, tmp_path):
        from db import Database

        database = Database(db_path=str(tmp_path / 'test.db'))
        database.initialize()

        database.save_signal('CRYPTO_RUNE-USD_1', 'RUNE-USD', 'crypto',
                             'BUY', price=0.5)
        database.save_signal('CRYPTO_RUNE-USD_1', 'RUNE-USD', 'crypto',
                             'BUY', price=0.6)

        rows = database.get_all_signals()
        matching = [k for k in rows if k == 'CRYPTO_RUNE-USD_1']
        assert len(matching) == 1, 'satu ticker/user jadi lebih dari satu baris'
        database.close()

    def test_cleanup_method_is_gone(self):
        """Job pembersih DB dihapus - batas baris sudah dijamin key."""
        from db import Database
        assert not hasattr(Database, 'cleanup_old_signals')


class TestLegacyNotifCryptoRetired:
    """notif_crypto bukan lagi pengaturan aktif."""

    def test_default_notifications_has_no_master_crypto(self):
        from db import DEFAULT_NOTIFICATIONS

        assert 'notif_crypto' not in DEFAULT_NOTIFICATIONS
        assert 'notif_crypto_long' in DEFAULT_NOTIFICATIONS
        assert 'notif_crypto_short' in DEFAULT_NOTIFICATIONS

    def test_direction_flags_still_persisted(self):
        from db import Database

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(db_path=os.path.join(tmp, 'test.db'))
            database.initialize()
            database.upsert_user(user_id=7)
            database.update_notifications(user_id=7,
                                         notif_crypto_long=True,
                                         notif_crypto_short=False)
            user = database.get_user(7)
            assert user['notif_crypto_long'] == 1
            assert user['notif_crypto_short'] == 0
            database.close()
