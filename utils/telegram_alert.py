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

from telegram import Bot
from telegram.error import BadRequest, NetworkError

WIB = timezone(timedelta(hours=7))

# Deduplication: key -> last_alert_timestamp (UTC)
_alert_history: dict[str, datetime] = {}
_alert_lock = threading.Lock()
_alert_queue: queue.Queue = queue.Queue()
_dedup_window = timedelta(minutes=30)

# BadRequest yang wajar dan tidak perlu bother admin.
_BENIGN_BAD_REQUEST = (
    "message is not modified",
    "query is too old",
    "message to edit not found",
    "message identifier is not specified",
)

# Error transport yang muncul sebagai TEKS di dalam pesan log, bukan sebagai
# exception PTB. Filter berbasis tipe (exc_info) tidak bisa menangkapnya:
# service me-log error Yahoo/CoinGecko/TradingView dengan f-string tanpa
# exc_info, jadi record.exc_info kosong dan tipenya tidak pernah dicek.
# Contoh: "Yahoo v8 error for RDTX.JK: HTTPSConnectionPool(...): Read timed out."
_TRANSPORT_NOISE_MARKERS = (
    "timed out",
    "timeout",
    "connection aborted",
    "connection refused",
    "connection reset",
    "connection error",
    "failed to establish a new connection",
    "max retries exceeded",
    "newconnectionerror",
    "protocolerror",
    "sslerror",
    "name or service not known",
    "temporary failure in name resolution",
    "remote end closed connection",
    "incomplete read",
    "event loop is closed",
)


def _is_noise(record: logging.LogRecord) -> bool:
    """True kalau error ini tidak perlu dikirim ke admin.

    Yang disaring:
      - masalah transport (jaringan, timeout, event loop closed). Alert-nya
        dikirim lewat jalur yang sama, jadi tidak akan menolong.
      - kegagalan kirim pesan. Kalau sinyal gagal terkirim ke user, itu
        penting — tapi bukan lewat alert yang bocor ke semua orang.
      - BadRequest yang wajar (pesan tidak berubah, tombol sudah lewat).

    Semuanya tetap ditulis ke error.log, cuma tidak dikirimi alert.
    """
    exc_type = record.exc_info[0] if record.exc_info else None
    if record.exc_info:
        exc_text = logging.Formatter().formatException(record.exc_info)
    else:
        exc_text = ''
    text = f"{record.getMessage()}\n{exc_text}".lower()

    # Kegagalan kirim pesan. Kalau sinyal gagal terkirim ke user, itu
    # penting — tapi bukan lewat alert yang bocor ke semua orang.
    if "failed to send message" in text:
        return True

    # Ticker yang tidak punya data harga. Bukan bug: bot sudah menandainya
    # dan melewatinya, jadi tidak perlu bother admin.
    if "no price data found" in text or "possibly delisted" in text:
        return True

    # BadRequest checked dulu: di PTB, BadRequest anak dari NetworkError,
    # jadi kalau NetworkError dicek lebih dulu, semua BadRequest ikut
    # tersaring — termasuk bug parse Markdown yang justru perlu dilaporkan.
    if exc_type is not None and issubclass(exc_type, BadRequest):
        return any(marker in text for marker in _BENIGN_BAD_REQUEST)

    # Masalah transport: jaringan, timeout, atau event loop closed. Alert-nya
    # dikirim lewat jalur yang sama, jadi tidak akan menolong.
    if exc_type is not None and issubclass(exc_type, NetworkError):
        return True

    # Error transport yang tertulis sebagai teks di dalam pesan log (tanpa
    # exc_info). Tetap masuk error.log, tapi tidak dikirimi alert.
    if any(marker in text for marker in _TRANSPORT_NOISE_MARKERS):
        return True

    return False


def _alert_worker(token: str, admin_chat_id: int):
    """Background worker that sends Telegram messages from queue.

    Worker ini memakai Bot sendiri dan satu event loop yang hidup selama
    worker berjalan. Bot aplikasi TIDAK boleh dipakai di sini: httpx
    mengikat connection pool ke event loop tempat client dibuat, jadi
    memakai client yang sama dari loop lain membuat connection pool ikut
    mati begitu loop itu ditutup. Request berikutnya dari loop aplikasi
    gagal dengan "Event loop is closed" — artinya sinyal ke user ikut
    gagal terkirim.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot = Bot(token=token)
    try:
        loop.run_until_complete(bot.initialize())
        while True:
            try:
                msg = _alert_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if msg is None:
                break
            try:
                loop.run_until_complete(bot.send_message(
                    chat_id=admin_chat_id,
                    text=msg,
                    read_timeout=10,
                    connect_timeout=10,
                ))
            except Exception as e:
                # Kegagalan kirim alert dicatat, bukan ditelan. Sebelumnya
                # error ini hilang tanpa jejak, jadi tidak ada yang tahu
                # kalau jalur alertnya sendiri rusak.
                logging.getLogger(__name__).warning(
                    f"Gagal kirim alert ke admin: {type(e).__name__}: {e}")
            finally:
                _alert_queue.task_done()
    finally:
        try:
            loop.run_until_complete(bot.shutdown())
        except Exception:
            pass
        loop.close()


class TelegramLogHandler(logging.Handler):
    """
    Sends ERROR/CRITICAL log events as Telegram messages to the admin.

    Deduplication: same error fingerprint within 30 minutes triggers only one alert.
    Falls back to logging if ADMIN_CHAT_ID is not set.
    """

    _worker_started: bool = False

    def __init__(self, app, admin_chat_id: Optional[int] = None):
        super().__init__(level=logging.ERROR)
        self.app = app
        self.admin_chat_id = admin_chat_id

        if admin_chat_id and not TelegramLogHandler._worker_started:
            t = threading.Thread(
                target=_alert_worker,
                args=(app.bot.token, admin_chat_id),
                daemon=True,
                name='TelegramAlertWorker'
            )
            t.start()
            TelegramLogHandler._worker_started = True

    def emit(self, record: logging.LogRecord):
        if not self.admin_chat_id:
            return

        # Error transport/jaringan tidak dikirim ke admin: alert-nya dikirim
        # lewat koneksi yang sama, jadi kalau koneksi itu yang bermasalah,
        # alert hanya menambah derau. Semuanya tetap ada di error.log.
        if _is_noise(record):
            return

        # Build dedup key from logger name + short error fingerprint
        fingerprint = self._fingerprint(record)
        now = datetime.now(timezone.utc)

        with _alert_lock:
            # Periodic cleanup: remove entries older than 2x dedup window
            # (prevents unbounded growth of _alert_history)
            cutoff = now - 2 * _dedup_window
            stale_keys = [k for k, v in _alert_history.items() if v < cutoff]
            for k in stale_keys:
                del _alert_history[k]

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
