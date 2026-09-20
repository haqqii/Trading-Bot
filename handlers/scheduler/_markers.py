"""Sent-notification markers (DB-first with file fallback) and legacy file-based helpers.

The current code path is DB-backed via ``db.check/mark_notification_sent_today``;
the file fallback and the two ``_check/_mark_sent_today(filepath)`` helpers remain
for backward compatibility with tests and any pre-DB callers.
"""
import logging
import os

from db import db

from handlers.scheduler._common import now_wib

logger = logging.getLogger(__name__)

# Legacy filenames — kept for tests that mock these via tmp_path.
MORNING_SENT_FILE = 'morning_sent.txt'  # Legacy - kept for migration
BSJP_SENT_FILE = 'bsjp_sent.txt'  # Legacy - kept for migration


def _check_notification_sent_today(marker_type: str) -> bool:
    """Check if notification was already sent today (DB-first, file fallback)."""
    try:
        # Try DB first
        return db.check_notification_sent_today(marker_type)
    except Exception as e:
        logger.debug(f"DB check failed for {marker_type}, falling back to file: {e}")

    # Fallback to file-based for legacy compatibility
    filepath = MORNING_SENT_FILE if marker_type == 'morning' else BSJP_SENT_FILE
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                last_sent = f.read().strip()
            today = now_wib().date().isoformat()
            return last_sent == today
    except Exception as e:
        logger.warning(f"Failed to read sent-marker {filepath}: {e}")
    return False


def _mark_notification_sent_today(marker_type: str):
    """Mark notification as sent today (DB-first, file fallback)."""
    try:
        db.mark_notification_sent_today(marker_type)
        return
    except Exception as e:
        logger.debug(f"DB mark failed for {marker_type}, falling back to file: {e}")

    # Fallback to file-based for legacy compatibility
    filepath = MORNING_SENT_FILE if marker_type == 'morning' else BSJP_SENT_FILE
    try:
        with open(filepath, 'w') as f:
            f.write(now_wib().date().isoformat())
    except Exception as e:
        logger.warning(f"Failed to write sent-marker {filepath}: {e}")


def _check_morning_sent_today():
    """Backward compatibility wrapper for the morning marker."""
    return _check_notification_sent_today('morning')


def _mark_morning_sent():
    """Backward compatibility wrapper for the morning marker."""
    _mark_notification_sent_today('morning')


def _check_sent_today(filepath: str) -> bool:
    """Check if notification was already sent today (file-based only).

    Used only by tests today; kept for backward compatibility.
    """
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                last_sent = f.read().strip()
            today = now_wib().date().isoformat()
            return last_sent == today
    except Exception as e:
        logger.warning(f"Failed to read sent-marker {filepath}: {e}")
    return False


def _mark_sent_today(filepath: str):
    """Mark notification as sent today (file-based only).

    Used only by tests today; kept for backward compatibility.
    """
    try:
        with open(filepath, 'w') as f:
            f.write(now_wib().date().isoformat())
    except Exception as e:
        logger.warning(f"Failed to write sent-marker {filepath}: {e}")
