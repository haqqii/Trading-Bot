"""
Telegram admin alert handler for critical log events.
Sends short alerts to the admin bot owner when errors occur.
"""
import os
import re
import asyncio
import hashlib
import threading
import queue
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

WIB = timezone(timedelta(hours=7))

# Deduplication: key -> last_alert_timestamp (UTC)
_alert_history: dict[str, datetime] = {}
_alert_lock = threading.Lock()
_alert_queue: queue.Queue = queue.Queue()
_dedup_window = timedelta(minutes=30)


def _alert_worker(bot_ref, admin_chat_id: int):
    """Background worker that sends Telegram messages from queue."""
    while True:
        try:
            msg = _alert_queue.get(timeout=1.0)
            if msg is None:
                break
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(bot_ref.bot.send_message(
                    chat_id=admin_chat_id,
                    text=msg,
                    read_timeout=10,
                    connect_timeout=10,
                ))
                loop.close()
            except Exception:
                pass  # Silently skip send failures — log already captured in error.log
            finally:
                _alert_queue.task_done()
        except queue.Empty:
            continue


class TelegramLogHandler(logging.Handler):
    """
    Sends ERROR/CRITICAL log events as Telegram messages to the admin.

    Deduplication: same error fingerprint within 30 minutes triggers only one alert.
    Falls back to logging if ADMIN_CHAT_ID is not set.
    """

    def __init__(self, app, admin_chat_id: Optional[int] = None):
        super().__init__(level=logging.ERROR)
        self.app = app
        self.admin_chat_id = admin_chat_id

        if admin_chat_id and not hasattr(TelegramLogHandler, '_worker_started'):
            t = threading.Thread(
                target=_alert_worker,
                args=(app, admin_chat_id),
                daemon=True,
                name='TelegramAlertWorker'
            )
            t.start()
            TelegramLogHandler._worker_started = True

    def emit(self, record: logging.LogRecord):
        if not self.admin_chat_id:
            return

        # Build dedup key from logger name + short error fingerprint
        fingerprint = self._fingerprint(record)
        now = datetime.now(timezone.utc)

        with _alert_lock:
            last = _alert_history.get(fingerprint)
            if last and (now - last) < _dedup_window:
                return
            _alert_history[fingerprint] = now

        msg = self._format_message(record)
        _alert_queue.put_nowait(msg)

    def _fingerprint(self, record: logging.LogRecord) -> str:
        key = f"{record.name}:{record.levelname}"
        if record.exc_text:
            key += ":" + record.exc_text.strip().split('\n')[-1]
        elif record.getMessage():
            key += ":" + record.getMessage()
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _format_message(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=WIB).strftime('%d/%m %H:%M')
        module = record.name.split('.')[-1]  # e.g. "scheduler", "stock_service"
        emoji = "🔴" if record.levelno >= logging.CRITICAL else "🟠"
        level = record.levelname.ljust(8)
        msg = record.getMessage()[:120]

        parts = [f"{emoji} {ts} | {module} | {level}"]
        parts.append(f"   {msg}")

        if record.exc_text:
            # Include first line of traceback
            exc_line = record.exc_text.strip().split('\n')[-1][:100]
            parts.append(f"   └ {exc_line}")

        return '\n'.join(parts)
