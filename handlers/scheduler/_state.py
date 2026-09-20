"""Module-level state and setters used by the scheduler package.

Owned state:
- ``ALL_STOCKS`` — dict of all IDX stock codes, written by ``set_all_stocks``
  (called by ``main.py`` during startup).

Removed dead state (previously exported for backward compat but never read
by any scheduler job):
- ``last_prices`` / ``set_last_prices`` — empty dict, never used
- ``last_crypto_prices`` / ``set_last_crypto_prices`` — empty dict, never used
- ``last_buy_signals`` / ``set_last_buy_signals`` — scheduler reads directly from
  ``command_handlers`` via ``_get_last_buy_signals()`` instead
- ``get_market_snapshot`` — defined but never called by any job
"""
import logging

logger = logging.getLogger(__name__)

ALL_STOCKS: dict[str, str] = {}


def set_all_stocks(stocks):
    """Set stocks reference (called by main.py during startup)."""
    global ALL_STOCKS
    ALL_STOCKS = stocks


def set_user_db(db):
    """Set user database reference (kept for backward compatibility).

    Jobs read user data directly from ``handlers.command_handlers`` via
    ``_get_user_db``; this setter logs the call so callers can verify wiring.
    """
    logger.info(f"[SCHEDULER] set_user_db called with {len(db)} users (now reading directly from command_handlers)")
    for uid, u in db.items():
        logger.info(f"[SCHEDULER]   User {uid}: notif_saham={u.get('notif_saham')}, notif_crypto={u.get('notif_crypto')}")
