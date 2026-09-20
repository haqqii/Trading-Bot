"""Background job scheduler package.

Re-exports the full public API of the legacy ``handlers/scheduler.py`` module so
existing consumers (``main.py``, ``handlers/commands/health.py``, and
``tests/test_scheduler.py``) continue to work without modification.

Internal layout:
    _state.py            — module-level state + setter functions called by main.py
    _common.py           — shared utilities (now_wib, fetch helpers, signal helpers)
    _markers.py          — sent-notification markers (DB-first with file fallback)
    jobs_stocks.py       — stock jobs (signals, tp_sl, favorit, alerts, prefetch)
    jobs_bsjp_morning.py — BSJP and morning-watchlist jobs
    jobs_crypto.py       — crypto jobs (signals, tp_sl, favorit, prefetch)
    jobs_misc.py         — auto_save, cleanup_caches, register_jobs
"""
# Setters / state (main.py API)
from ._state import (
    set_all_stocks,
    set_user_db,
    set_last_prices,
    set_last_crypto_prices,
    set_last_buy_signals,
    get_market_snapshot,
)

# Shared utilities
from ._common import now_wib

# Marker helpers (used by bsjp + morning)
from ._markers import (
    MORNING_SENT_FILE,
    BSJP_SENT_FILE,
    _check_notification_sent_today,
    _mark_notification_sent_today,
    _check_morning_sent_today,
    _mark_morning_sent,
    _check_sent_today,
    _mark_sent_today,
)

# Jobs (async) — re-exported for tests using hasattr(scheduler, name)
from .jobs_stocks import (
    reset_stock_circuit_breaker,
    check_stock_signals,
    check_stock_tp_sl,
    check_favorit_alerts,
    check_alerts,
    prefetch_stock_cache,
)
from .jobs_bsjp_morning import (
    check_bsjp_signals,
    check_morning_notification,
)
from .jobs_crypto import (
    check_crypto_signals,
    check_crypto_tp_sl,
    check_crypto_favorit_alerts,
    prefetch_crypto_cache,
)
from .jobs_misc import (
    auto_save_data,
    cleanup_caches,
    cleanup_old_signals,
    register_jobs,
)


__all__ = [
    # Setters / register
    'register_jobs',
    'set_all_stocks',
    'set_user_db',
    'set_last_prices',
    'set_last_crypto_prices',
    'set_last_buy_signals',
    'get_market_snapshot',
    # Helpers
    'now_wib',
    '_check_sent_today',
    '_mark_sent_today',
    '_check_notification_sent_today',
    '_mark_notification_sent_today',
    '_check_morning_sent_today',
    '_mark_morning_sent',
    # Constants
    'MORNING_SENT_FILE',
    'BSJP_SENT_FILE',
    # Async jobs
    'reset_stock_circuit_breaker',
    'check_stock_signals',
    'check_stock_tp_sl',
    'check_favorit_alerts',
    'check_alerts',
    'prefetch_stock_cache',
    'check_bsjp_signals',
    'check_morning_notification',
    'check_crypto_signals',
    'check_crypto_tp_sl',
    'check_crypto_favorit_alerts',
    'prefetch_crypto_cache',
    'auto_save_data',
    'cleanup_caches',
    'cleanup_old_signals',
]
