"""
Unit tests for handlers/scheduler.py
Tests scheduler utility functions (now_wib, sent markers, time helpers).
"""
import sys
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNowWib:
    """Tests for now_wib function."""

    def test_returns_datetime(self):
        """Should return a datetime object."""
        from handlers.scheduler import now_wib
        result = now_wib()
        assert isinstance(result, datetime)

    def test_wib_timezone_offset(self):
        """Should be in WIB timezone (UTC+7)."""
        from handlers.scheduler import now_wib
        result = now_wib()
        # WIB is UTC+7
        assert result.utcoffset() == timedelta(hours=7)

    def test_consistent_with_utc(self):
        """WIB time should be UTC + 7 hours."""
        from handlers.scheduler import now_wib
        wib = now_wib()
        utc = datetime.now(timezone.utc)
        # Strip tz info for comparison
        diff = (wib.replace(tzinfo=None) - utc.replace(tzinfo=None)).total_seconds()
        # Should be approximately 7 hours = 25200 seconds
        assert abs(diff - 25200) < 2  # Allow 2 second tolerance


class TestSentFileMarkers:
    """Tests for sent file marker helpers."""

    def test_check_sent_today_file_not_exists(self, monkeypatch, tmp_path):
        """Should return False when file doesn't exist."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today
        result = _check_sent_today('nonexistent.txt')
        assert result is False

    def test_check_sent_today_match(self, monkeypatch, tmp_path):
        """Should return True when file matches today's date."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today, _mark_sent_today
        _mark_sent_today('test_sent.txt')
        assert _check_sent_today('test_sent.txt') is True

    def test_check_sent_today_mismatch(self, monkeypatch, tmp_path):
        """Should return False when file has different date."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today
        # Write yesterday's date
        with open('test_sent.txt', 'w') as f:
            f.write('2020-01-01')
        assert _check_sent_today('test_sent.txt') is False

    def test_check_sent_today_empty_file(self, monkeypatch, tmp_path):
        """Should handle empty file."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today
        with open('test_sent.txt', 'w') as f:
            f.write('')
        assert _check_sent_today('test_sent.txt') is False

    def test_check_sent_today_with_whitespace(self, monkeypatch, tmp_path):
        """Should handle whitespace in file."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today, _mark_sent_today
        _mark_sent_today('test_sent.txt')
        # Add extra whitespace
        with open('test_sent.txt', 'r') as f:
            content = f.read()
        with open('test_sent.txt', 'w') as f:
            f.write('  ' + content + '\n')
        # Should still match after stripping
        assert _check_sent_today('test_sent.txt') is True

    def test_mark_sent_today_creates_file(self, monkeypatch, tmp_path):
        """Should create file with today's date."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _mark_sent_today
        _mark_sent_today('test_sent.txt')
        assert os.path.exists('test_sent.txt')

    def test_mark_sent_today_writes_iso_date(self, monkeypatch, tmp_path):
        """Should write ISO format date."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _mark_sent_today
        _mark_sent_today('test_sent.txt')
        with open('test_sent.txt', 'r') as f:
            content = f.read().strip()
        # Should be parseable as ISO date
        parsed = datetime.fromisoformat(content)
        assert isinstance(parsed, datetime)

    def test_mark_sent_today_overwrites_existing(self, monkeypatch, tmp_path):
        """Should overwrite existing file."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _mark_sent_today
        # Write old date first
        with open('test_sent.txt', 'w') as f:
            f.write('2020-01-01')
        # Mark today
        _mark_sent_today('test_sent.txt')
        with open('test_sent.txt', 'r') as f:
            content = f.read().strip()
        # Should be today's date, not 2020
        assert content != '2020-01-01'


class TestSentFileConstants:
    """Tests for sent file constants."""

    def test_morning_sent_file_constant(self):
        """MORNING_SENT_FILE constant should be set."""
        from handlers.scheduler import MORNING_SENT_FILE
        assert MORNING_SENT_FILE == 'morning_sent.txt'

    def test_bsjp_sent_file_constant(self):
        """BSJP_SENT_FILE constant should be set."""
        from handlers.scheduler import BSJP_SENT_FILE
        assert BSJP_SENT_FILE == 'bsjp_sent.txt'

    def test_files_are_different(self):
        """Morning and BSJP files should be different."""
        from handlers.scheduler import MORNING_SENT_FILE, BSJP_SENT_FILE
        assert MORNING_SENT_FILE != BSJP_SENT_FILE


class TestSchedulerImports:
    """Tests for module imports."""

    def test_can_import_scheduler(self):
        """Scheduler module should be importable."""
        from handlers import scheduler
        assert scheduler is not None

    def test_can_import_helper_functions(self):
        """Helper functions should be importable."""
        from handlers.scheduler import (
            now_wib,
            _check_sent_today,
            _mark_sent_today
        )
        assert callable(now_wib)
        assert callable(_check_sent_today)
        assert callable(_mark_sent_today)

    def test_async_functions_exist(self):
        """Async scheduler functions should exist."""
        from handlers import scheduler
        # Check some key async functions exist
        for func_name in ['check_alerts', 'check_morning_notification',
                         'check_bsjp_signals', 'check_stock_signals',
                         'check_stock_tp_sl', 'check_crypto_signals',
                         'check_crypto_tp_sl', 'check_favorit_alerts',
                         'check_crypto_favorit_alerts', 'reset_stock_circuit_breaker',
                         'prefetch_stock_cache']:
            assert hasattr(scheduler, func_name), f"Missing {func_name}"


class TestEdgeCases:
    """Edge case tests for scheduler utilities."""

    def test_check_sent_handles_io_error(self, monkeypatch, tmp_path):
        """Should return False on IO error."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today

        # Create a file that's actually a directory (will cause IO error)
        os.mkdir('weird.txt')
        result = _check_sent_today('weird.txt')
        # Should return False gracefully
        assert result is False

    def test_check_sent_with_binary_file(self, monkeypatch, tmp_path):
        """Should handle binary/non-utf8 files gracefully."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _check_sent_today
        # Write binary content
        with open('test_sent.txt', 'wb') as f:
            f.write(b'\xff\xfe\x00\x01garbage')
        # Should not crash
        result = _check_sent_today('test_sent.txt')
        # Will return False (date won't match)
        assert result is False

    def test_now_wib_in_business_hours(self):
        """Test now_wib during business hours returns valid WIB time."""
        from handlers.scheduler import now_wib
        result = now_wib()
        # Should have valid hour (0-23)
        assert 0 <= result.hour <= 23
        assert 0 <= result.minute <= 59
        assert 0 <= result.second <= 59


class TestIntegration:
    """Integration tests for scheduler utilities."""

    def test_mark_then_check_workflow(self, monkeypatch, tmp_path):
        """Mark then check should return True."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _mark_sent_today, _check_sent_today

        filepath = 'integration_test.txt'
        # Initially should be False
        assert _check_sent_today(filepath) is False
        # Mark as sent
        _mark_sent_today(filepath)
        # Now should be True
        assert _check_sent_today(filepath) is True

    def test_multiple_files_independent(self, monkeypatch, tmp_path):
        """Multiple sent files should be independent."""
        monkeypatch.chdir(tmp_path)
        from handlers.scheduler import _mark_sent_today, _check_sent_today

        # Mark only one
        _mark_sent_today('file_a.txt')
        # file_a should be True
        assert _check_sent_today('file_a.txt') is True
        # file_b should be False
        assert _check_sent_today('file_b.txt') is False
