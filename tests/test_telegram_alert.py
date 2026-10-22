"""
Unit tests for utils/telegram_alert.py
Tests the noise filter that decides which errors are pushed to the admin.
"""
import sys
import os
import logging

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.telegram_alert import _is_noise


def make_record(msg, exc=None, name='jobs_stocks', level=logging.ERROR):
    """Buat LogRecord dengan exception info, seperti yang dihasilkan logging."""
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except Exception:
            exc_info = sys.exc_info()
    return logging.LogRecord(name, level, 'file.py', 1, msg, None, exc_info)


class TestIsNoise:
    """Tests for _is_noise filter."""

    def test_network_error_is_noise(self):
        """Masalah transport tidak boleh dikirimi alert."""
        rec = make_record(
            "[STOCK] Failed to send message for SAPX: Unknown error in HTTP "
            "implementation: RuntimeError('Event loop is closed')",
            NetworkError("Unknown error in HTTP implementation: "
                         "RuntimeError('Event loop is closed')"),
        )
        assert _is_noise(rec) is True

    def test_timed_out_is_noise(self):
        """Timeout juga noise."""
        rec = make_record(
            "[STOCK] Failed to send message for BBCA: Timed out",
            TimedOut(),
        )
        assert _is_noise(rec) is True

    def test_failed_to_send_message_is_noise(self):
        """Kegagalan kirim pesan tidak bocor ke admin."""
        rec = make_record("[CRYPTO] Failed to send message for BTCUSDT: boom")
        assert _is_noise(rec) is True

    def test_benign_bad_request_is_noise(self):
        """'Message is not modified' itu no-op, tidak perlu dilaporkan."""
        rec = make_record(
            "Unhandled exception in handler:",
            BadRequest("Message is not modified"),
        )
        assert _is_noise(rec) is True

    def test_parse_entities_bad_request_is_reported(self):
        """Bug parse Markdown itu bug sungguhan, harus tetap dilaporkan.

        Di PTB, BadRequest anak dari NetworkError. Kalau NetworkError dicek
        lebih dulu, semua BadRequest ikut tersaring dan bug ini hilang
        tanpa jejak.
        """
        rec = make_record(
            "Unhandled exception in handler:",
            BadRequest("Can't parse entities: can't find end of the entity"),
        )
        assert _is_noise(rec) is False

    def test_plain_error_is_reported(self):
        """Error biasa harus tetap sampai ke admin."""
        rec = make_record("Telegram bot token not found!")
        assert _is_noise(rec) is False

    def test_handler_error_without_text_is_reported(self):
        """Error tanpa pesan tetap dilaporkan."""
        rec = make_record("Unhandled exception in handler:")
        assert _is_noise(rec) is False
