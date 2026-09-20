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
    app.job_queue.run_repeating(reset_stock_circuit_breaker, interval=60, first=5)

    # === PREFETCH JOBS (run first to warm cache) ===
    # Prefetch stock cache every 5 minutes during market hours - reduced for rate limit
    app.job_queue.run_repeating(prefetch_stock_cache, interval=300, first=5)

    # Prefetch crypto cache every 15 minutes (reduced due to CoinGecko rate limits)
    app.job_queue.run_repeating(prefetch_crypto_cache, interval=900, first=10)

    # Favorit alerts check every 5 minutes - reduced for rate limit
    app.job_queue.run_repeating(check_favorit_alerts, interval=300, first=30)

    # Alerts check every minute
    app.job_queue.run_repeating(check_alerts, interval=60, first=60)

    # Morning notification check every minute (07:15-08:00)
    app.job_queue.run_repeating(check_morning_notification, interval=60, first=15)

    # BSJP check every minute (14:00-16:00)
    app.job_queue.run_repeating(check_bsjp_signals, interval=60, first=30)

    # Stock signals check every 15 minutes (market hours only) - reduced to avoid rate limit
    app.job_queue.run_repeating(check_stock_signals, interval=900, first=90)

    # Stock TP/SL tracking check every 3 minutes
    app.job_queue.run_repeating(check_stock_tp_sl, interval=180, first=60)

    # Crypto signals check every 15 minutes - reduced to avoid rate limit
    app.job_queue.run_repeating(check_crypto_signals, interval=900, first=90)

    # Crypto TP/SL tracking check every 2 minutes
    app.job_queue.run_repeating(check_crypto_tp_sl, interval=120, first=60)

    # Crypto favorit alerts check every 2 minutes
    app.job_queue.run_repeating(check_crypto_favorit_alerts, interval=120, first=30)

    # Auto-save user data every 5 minutes
    app.job_queue.run_repeating(auto_save_data, interval=300, first=30)

    # Cache cleanup every 5 minutes
    app.job_queue.run_repeating(cleanup_caches, interval=300, first=60)

    # Set APScheduler misfire behavior globally
    app.job_queue.scheduler.misfire_grace_time = 120
    app.job_queue.scheduler.coalesce = True
