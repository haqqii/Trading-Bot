"""Miscellaneous maintenance jobs and the job registration entry point.

Owns:
- ``auto_save_data``   — periodic user-data persistence
- ``cleanup_caches``   — periodic cache and signal cleanup
- ``register_jobs``    — single place that wires all jobs to ``app.job_queue``
"""
import logging

from db import db
from utils.cache import _price_cache, _signal_cache, _market_cache, _usd_cache

from handlers.scheduler._common import cleanup_old_signals
from handlers.scheduler.jobs_stocks import (
    reset_stock_circuit_breaker,
    check_stock_signals,
    check_stock_tp_sl,
    check_favorit_alerts,
    check_alerts,
    prefetch_stock_cache,
)
from handlers.scheduler.jobs_crypto import (
    check_crypto_signals,
    check_crypto_tp_sl,
    check_crypto_favorit_alerts,
    prefetch_crypto_cache,
)
from handlers.scheduler.jobs_bsjp_morning import (
    check_bsjp_signals,
    check_morning_notification,
)

logger = logging.getLogger(__name__)

# APScheduler hanya menjalankan job yang telat paling MISFIRE_GRACE_TIME
# detik. Default-nya 1 DETIK, jadi job yang telat sedikit saja langsung
# di-skip ("was missed by 0:00:30"). Nilai ini harus diberikan saat job
# dibuat - pengaturan global setelah semua job terdaftar tidak berlaku untuk job
# yang sudah ada, karena nilainya dibaca waktu job dibuat.
MISFIRE_GRACE_TIME = 120


def _job_kwargs() -> dict:
    """Opsi APScheduler yang dipakai semua job background."""
    return {'misfire_grace_time': MISFIRE_GRACE_TIME}


async def auto_save_data(app):
    """Auto-save user data periodically."""
    try:
        # Import here to avoid module caching issues
        from handlers.command_handlers import save_user_data
        save_user_data()
        logger.debug("User data auto-saved")
    except Exception as e:
        logger.error(f"Auto-save error: {e}", exc_info=True)


async def cleanup_caches(app):
    """Periodic cache and signal cleanup."""
    try:
        _price_cache.cleanup()
        _signal_cache.cleanup()
        _market_cache.cleanup()
        _usd_cache.cleanup()
        cleanup_old_signals()  # Cleanup old signals (max 7 days)
        db.checkpoint()       # Truncate WAL file to keep disk usage small
        logger.debug("Caches, signals, and WAL checkpoint done")
    except Exception as e:
        logger.error(f"Cleanup error: {e}", exc_info=True)


def register_jobs(app):
    """Register all background jobs to the application."""
    # === MARKET OPEN RESET (run first) ===
    # Reset circuit breakers at 09:00 WIB so stock scanning starts fresh
    app.job_queue.run_repeating(reset_stock_circuit_breaker, interval=60, first=5,
                                job_kwargs=_job_kwargs())

    # === PREFETCH JOBS (run first to warm cache) ===
    # Prefetch stock cache every 5 minutes during market hours - reduced for rate limit
    app.job_queue.run_repeating(prefetch_stock_cache, interval=300, first=5,
                                job_kwargs=_job_kwargs())

    # Prefetch crypto cache every 15 minutes (reduced due to CoinGecko rate limits)
    app.job_queue.run_repeating(prefetch_crypto_cache, interval=900, first=10,
                                job_kwargs=_job_kwargs())

    # Favorit alerts check every 5 minutes - reduced for rate limit
    app.job_queue.run_repeating(check_favorit_alerts, interval=300, first=30,
                                job_kwargs=_job_kwargs())

    # Alerts check every minute
    app.job_queue.run_repeating(check_alerts, interval=60, first=60,
                                job_kwargs=_job_kwargs())

    # Morning notification check every minute (07:15-08:00)
    app.job_queue.run_repeating(check_morning_notification, interval=60, first=15,
                                job_kwargs=_job_kwargs())

    # BSJP check every minute (14:00-16:00)
    app.job_queue.run_repeating(check_bsjp_signals, interval=60, first=30,
                                job_kwargs=_job_kwargs())

    # Stock signals check every 15 minutes (market hours only) - reduced to avoid rate limit
    app.job_queue.run_repeating(check_stock_signals, interval=900, first=90,
                                job_kwargs=_job_kwargs())

    # Stock TP/SL tracking check every 3 minutes
    app.job_queue.run_repeating(check_stock_tp_sl, interval=180, first=60,
                                job_kwargs=_job_kwargs())

    # Crypto signals check every 15 minutes - reduced to avoid rate limit
    app.job_queue.run_repeating(check_crypto_signals, interval=900, first=90,
                                job_kwargs=_job_kwargs())

    # Crypto TP/SL tracking check every 2 minutes
    app.job_queue.run_repeating(check_crypto_tp_sl, interval=120, first=60,
                                job_kwargs=_job_kwargs())

    # Crypto favorit alerts check every 2 minutes
    app.job_queue.run_repeating(check_crypto_favorit_alerts, interval=120, first=30,
                                job_kwargs=_job_kwargs())

    # Auto-save user data every 5 minutes
    app.job_queue.run_repeating(auto_save_data, interval=300, first=30,
                                job_kwargs=_job_kwargs())

    # Cache cleanup every 5 minutes
    app.job_queue.run_repeating(cleanup_caches, interval=300, first=60,
                                job_kwargs=_job_kwargs())

    # Set APScheduler misfire behavior globally.
    # Peringatan: nilai di sini hanya berlaku untuk job yang dibuat
    # SEBELUM baris ini - job di atas sudah punya misfire_grace_time
    # sendiri (lihat _job_kwargs).
    app.job_queue.scheduler.misfire_grace_time = MISFIRE_GRACE_TIME
    app.job_queue.scheduler.coalesce = True
