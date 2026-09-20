"""Module-level state and setters used by the scheduler package.

These globals are owned here and written by the setter functions exported
through the package ``__init__``. They are kept for backward compatibility
with ``main.py`` even where nothing in the scheduler reads them today.
"""
import logging

logger = logging.getLogger(__name__)

# Public, mutable containers.
ALL_STOCKS = {}
last_prices = {}
last_crypto_prices = {}
market_cache = {}
last_buy_signals = {}  # written by set_last_buy_signals; not currently read by jobs


def set_all_stocks(stocks):
    """Set stocks reference."""
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


def set_last_prices(prices):
    """Set last prices reference."""
    global last_prices
    last_prices = prices


def set_last_crypto_prices(prices):
    """Set last crypto prices reference."""
    global last_crypto_prices
    last_crypto_prices = prices


def set_last_buy_signals(signals):
    """Set last buy signals reference."""
    global last_buy_signals
    last_buy_signals = signals


def get_market_snapshot():
    """Get cached market snapshot data."""
    global market_cache
    return market_cache
