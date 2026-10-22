"""
Unit test untuk utils/log_tag.py.

Dua bot jalan di container yang sama dan lognya bercampur di stdout.
Field "Object" (nama logger) harus menunjuk bot mana yang menulis baris
itu: "trading.<logger>" vs "aimapps.<logger>".
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def restore_factory():
    """Balikkan log record factory global setelah test."""
    previous = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(previous)


class TestLogTag:
    """Tests for install()."""

    def test_prefixes_logger_name(self, restore_factory):
        """Nama logger dapat prefix 'trading.'."""
        from utils.log_tag import install
        install('trading')
        record = logging.getLogRecordFactory()(
                'some/path.py', logging.INFO, 'f.py', 10, 'pesan', None, None)
        assert record.name == 'trading.some/path.py'

    def test_not_double_prefixed(self, restore_factory):
        """Prefix yang sudah ada tidak boleh ditambahkan dua kali."""
        from utils.log_tag import install
        install('trading')
        install('trading')
        record = logging.getLogRecordFactory()(
                'some/path.py', logging.INFO, 'f.py', 10, 'pesan', None, None)
        assert record.name == 'trading.some/path.py'

    def test_different_tag(self, restore_factory):
        """Tag lain menghasilkan prefix sendiri."""
        from utils.log_tag import install
        install('aimapps')
        record = logging.getLogRecordFactory()(
                'bot.py', logging.INFO, 'f.py', 10, 'pesan', None, None)
        assert record.name == 'aimapps.bot.py'

    def test_empty_tag_is_ignored(self, restore_factory):
        """Tag kosong tidak mengubah apa-apa."""
        from utils.log_tag import install
        install('')
        record = logging.getLogRecordFactory()(
                'some/path.py', logging.INFO, 'f.py', 10, 'pesan', None, None)
        assert record.name == 'some/path.py'

    def test_logger_name_itself_unchanged(self, restore_factory):
        """Logger asli tidak berubah -- hanya record.name yang diberi prefix."""
        from utils.log_tag import install
        install('trading')
        logger = logging.getLogger('handlers.scheduler.jobs_stocks')
        assert logger.name == 'handlers.scheduler.jobs_stocks'
        # Prefix ada di record, bukan di nama logger.
        record = logger.makeRecord(
            'handlers.scheduler.jobs_stocks', logging.INFO, 'f.py', 1,
            'pesan', None, None, 'func')
        assert record.name == 'trading.handlers.scheduler.jobs_stocks'

    def test_log_output_contains_tag(self, restore_factory, caplog):
        """Baris log yang keluar memuat prefix di field Object."""
        from utils.log_tag import install
        install('trading')
        stream = logging.StreamHandler()
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        capture = _Capture()
        root = logging.getLogger()
        root.addHandler(capture)
        try:
            logging.getLogger('services.stock_service').error('boom')
        finally:
            root.removeHandler(capture)
        assert records
        assert records[-1].name == 'trading.services.stock_service'

