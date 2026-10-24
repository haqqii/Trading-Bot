"""
Unit tests for utils/time_utils.py and the WIB timestamps in notifications.

Regression context: the bot runs in a Docker container whose local timezone is
UTC (no TZ env set), but the notification templates labelled the naive
``datetime.now()`` output as "WIB" — every message showed a timestamp exactly
7 hours behind the user's clock.
"""
import sys
import os
from datetime import datetime, timezone, timedelta

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.time_utils import WIB, now_wib, wib_strftime


class TestNowWib:
    """Tests for the WIB time helpers."""

    def test_offset_is_utc_plus_7(self):
        """now_wib() must be UTC+7."""
        assert now_wib().utcoffset() == timedelta(hours=7)

    def test_wib_constant_offset(self):
        """WIB constant must be UTC+7."""
        assert WIB.utcoffset(None) == timedelta(hours=7)

    def test_matches_utc_plus_seven(self):
        """now_wib() must equal UTC + 7h regardless of the host timezone."""
        expected = (datetime.now(timezone.utc) + timedelta(hours=7)).replace(tzinfo=None)
        assert abs((now_wib().replace(tzinfo=None) - expected).total_seconds()) < 5

    def test_is_timezone_aware(self):
        """Result must be timezone-aware (not naive local time)."""
        assert now_wib().tzinfo is not None

    def test_wib_strftime_format(self):
        """wib_strftime must honour the given format string."""
        assert len(wib_strftime('%Y-%m-%d')) == 10


def _ts_line(msg: str) -> str:
    """Extract the ⏰ timestamp line from a notification message."""
    for line in msg.splitlines():
        if line.startswith('⏰'):
            return line.replace('⏰', '').strip()
    return ''


class TestNotificationTimestampsAreWib:
    """Notifications must show WIB time, not the container's local clock."""

    @staticmethod
    def _crypto_message():
        from utils.formatters import format_unified_crypto_notification
        return format_unified_crypto_notification(
            notif_type='BUY',
            ticker='BTC-USD',
            name='Bitcoin',
            entry=100.0,
            current_price=100.0,
            tp1=110.0,
            tp2=120.0,
            tp3=130.0,
            sl=90.0,
        )

    @staticmethod
    def _stock_message():
        from utils.formatters import format_unified_stock_notification
        return format_unified_stock_notification(
            notif_type='BUY',
            ticker='BBCA.JK',
            name='BBCA',
            entry=100.0,
            current_price=100.0,
            tp1=110.0,
            tp2=120.0,
            tp3=130.0,
            sl=90.0,
        )

    def test_crypto_timestamp_matches_wib_clock(self):
        """Crypto notification timestamp must be the current WIB time."""
        before = wib_strftime('%d %b %Y pukul %H:%M WIB')
        msg = self._crypto_message()
        after = wib_strftime('%d %b %Y pukul %H:%M WIB')
        ts = _ts_line(msg)
        assert ts, 'timestamp line not found in crypto message'
        assert ts in (before, after)

    def test_stock_timestamp_matches_wib_clock(self):
        """Stock notification timestamp must be the current WIB time."""
        before = wib_strftime('%d %b %Y pukul %H:%M WIB')
        msg = self._stock_message()
        after = wib_strftime('%d %b %Y pukul %H:%M WIB')
        ts = _ts_line(msg)
        assert ts, 'timestamp line not found in stock message'
        assert ts in (before, after)

    def test_crypto_message_uses_wib_helper(self, monkeypatch):
        """Crypto message must source its timestamp from the WIB helper."""
        from utils import formatters
        monkeypatch.setattr(formatters, 'wib_strftime', lambda fmt: 'WIB-SENTINEL')
        msg = self._crypto_message()
        assert 'WIB-SENTINEL' in msg

    def test_stock_message_uses_wib_helper(self, monkeypatch):
        """Stock message must source its timestamp from the WIB helper."""
        from utils import formatters
        monkeypatch.setattr(formatters, 'wib_strftime', lambda fmt: 'WIB-SENTINEL')
        msg = self._stock_message()
        assert 'WIB-SENTINEL' in msg
