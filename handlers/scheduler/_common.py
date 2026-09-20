"""Shared utilities used by multiple job families in the scheduler package.

Includes:
- timezone helpers (WIB, now_wib)
- timeframe maps (TF_TO_INTERVAL, CRYPTO_TF_TO_INTERVAL)
- result / cache helpers (_unwrap_stock_result, get_*_data_with_fallback)
- per-signal user-data accessors (_get_user_db, _get_last_buy_signals, _remove_signal)
- signal retention (cleanup_old_signals, SIGNAL_MAX_*)
- cross-job scheduling (_schedule_followup_scan) — uses lazy imports to avoid cycles.
- bot send-with-retry helper (_send_bot_with_retry)
- signal metadata helpers (compute_trend, compute_reliability)
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, cast

from services.stock_service import stock_service, StockDataResult
from services.crypto_service import crypto_service
from services.signal_service import calc_tPSL
from utils.cache import _price_cache

logger = logging.getLogger(__name__)

# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))

# Timeframe key -> (interval, period) mapping for stock data fetch
TF_TO_INTERVAL = {
    '1': ('1m', '1d'),
    '5': ('5m', '5d'),
    '15': ('15m', '5d'),
    '30': ('30m', '5d'),
    '60': ('1h', '1mo'),
    '240': ('4h', '1mo'),
    '1440': ('1d', '3mo'),
}

# Crypto timeframe key -> (interval, period) mapping
CRYPTO_TF_TO_INTERVAL = {
    '1': ('1m', '1d'),
    '5': ('5m', '5d'),
    '15': ('15m', '5d'),
    '30': ('30m', '5d'),
    '60': ('1h', '1mo'),
    '240': ('4h', '1mo'),
    '1440': ('1d', '3mo'),
}

# Signal retention settings
SIGNAL_MAX_AGE_DAYS = 7
SIGNAL_MAX_PER_TYPE = 50  # Max signals per type (stock/crypto)


def now_wib():
    """Get current time in WIB timezone (UTC+7).

    Always converts from UTC to ensure correct WIB time regardless of
    system timezone setting.
    """
    return datetime.now(timezone.utc).astimezone(WIB)


def _safe_time_diff(t1, t2):
    """Compute (t1 - t2) in seconds, handling naive/aware mismatch.

    If one is naive, treat it as UTC (since SQLite CURRENT_TIMESTAMP
    returns naive UTC). Returns total seconds as float.
    """
    from datetime import timezone as _tz
    if t1.tzinfo is None and t2.tzinfo is not None:
        t1 = t1.replace(tzinfo=_tz.utc)
    elif t2.tzinfo is None and t1.tzinfo is not None:
        t2 = t2.replace(tzinfo=_tz.utc)
    return (t1 - t2).total_seconds()


def _unwrap_stock_result(result) -> dict | None:
    """Unwrap StockDataResult or dict to get the actual data dict.

    Handles both new StockDataResult return type and old dict return type
    for backward compatibility during migration.
    """
    if result is None:
        return None
    if isinstance(result, StockDataResult):
        return result.data if result.success else None
    # Legacy: return dict as-is
    return cast(dict[Any, Any], result)


async def _send_bot_with_retry(bot, chat_id: int, text: str, retries: int = 5, delay: int = 3, **kwargs):
    """Send message via bot with retry on timeout. Returns True if successful."""
    from telegram.error import TimedOut
    for attempt in range(retries):
        try:
            await asyncio.wait_for(
                bot.send_message(chat_id=chat_id, text=text, **kwargs),
                timeout=180
            )
            return True
        except TimedOut:
            if attempt < retries - 1:
                logger.warning(f"Send timeout (attempt {attempt+1}/{retries}), retrying in {delay}s")
                await asyncio.sleep(delay)
                delay *= 2
            else:
                logger.error(f"Send failed after {retries} attempts")
                return False
        except Exception as e:
            logger.error(f"Send error: {e}", exc_info=True)
            return False
    return False


def get_stock_data_with_fallback(ticker: str, interval: str = '5m', period: str = '3d'):
    """Get stock data with stale cache fallback.

    Returns (data, is_stale) tuple. Uses same cache key format as
    ``stock_service``: ``{ticker}:{interval}:{period}``.
    """
    # Use same cache key format as stock_service for cache hits
    cache_key = f"{ticker}:{interval}:{period}"

    # Check cache first - use stock_service's cache key format
    # Also handle case where cached value is a StockDataResult (old cached data)
    cached = _price_cache.get(cache_key)
    if cached:
        # Unwrap if cached value is StockDataResult
        if isinstance(cached, StockDataResult):
            cached = cached.data if cached.success else None
        if cached and cached.get('candles', 0) >= 5:
            return cached, False

    # Try fresh data
    result = stock_service.get_stock_data_combined(ticker, interval, period)
    d = _unwrap_stock_result(result)
    if d and d.get('candles', 0) >= 5:
        # Also cache with fallback key format for other callers
        fallback_key = f"stock_{ticker}_{interval}_{period}"
        _price_cache.set(fallback_key, d, ttl=180)  # 3 min cache
        return d, False

    # Try stale cache (check both key formats)
    stale_d = _price_cache.get_stale(cache_key)
    if stale_d:
        if isinstance(stale_d, StockDataResult):
            stale_d = stale_d.data if stale_d.success else None
        if stale_d and stale_d.get('candles', 0) >= 5:
            logger.warning(f"Using stale cache for {ticker} (API may be down)")
            return stale_d, True

    fallback_key = f"stock_{ticker}_{interval}_{period}"
    stale_d = _price_cache.get_stale(fallback_key)
    if stale_d:
        if isinstance(stale_d, StockDataResult):
            stale_d = stale_d.data if stale_d.success else None
        if stale_d and stale_d.get('candles', 0) >= 5:
            logger.warning(f"Using stale cache for {ticker} (API may be down)")
            return stale_d, True

    return None, False


def get_crypto_data_with_fallback(ticker: str, interval: str = '1h', period: str = '1d'):
    """Get crypto data with stale cache fallback.

    Returns (data, is_stale) tuple.
    """
    cache_key = f"crypto_{ticker}_{interval}_{period}"

    # Check cache first before API call
    cached = _price_cache.get(cache_key)
    if cached:
        # Handle case where cached value is a StockDataResult (shouldn't happen for crypto, but be safe)
        if isinstance(cached, StockDataResult):
            cached = cached.data if cached.success else None
        if cached and cached.get('candles', 0) >= 5:
            return cached, False

    # Try fresh data
    d = crypto_service.get_crypto_data_combined(ticker, interval, period)
    if d and d.get('candles', 0) >= 5:
        _price_cache.set(cache_key, d, ttl=300)  # 5 min cache
        return d, False

    # Try stale cache
    stale_d = _price_cache.get_stale(cache_key)
    if stale_d:
        if isinstance(stale_d, StockDataResult):
            stale_d = stale_d.data if stale_d.success else None
        if stale_d and stale_d.get('candles', 0) >= 5:
            logger.warning(f"Using stale crypto cache for {ticker} (API may be down)")
            return stale_d, True

    return None, False


def _get_user_db():
    """Get user database - reads directly from command_handlers to ensure fresh data."""
    import handlers.command_handlers as ch
    return ch.user_data_db


def _get_last_buy_signals():
    """Get last buy signals - reads directly from command_handlers."""
    import handlers.command_handlers as ch
    return ch.last_buy_signals


def _remove_signal(key: str):
    """Remove signal from persisted storage."""
    import handlers.command_handlers as ch
    if key in ch.last_buy_signals:
        del ch.last_buy_signals[key]
        logger.debug(f"Signal removed from storage: {key}")


# Quality base for reliability scoring
_QUALITY_BASE = {
    'STRONG':   50,
    'MODERATE': 35,
    'WEAK':     20,
    'EARLY':    15,  # crypto-only
}


def compute_reliability(
    quality: str,
    reasons: list,
    patterns: list,
    volume_ratio: float,
) -> int:
    """Compute a dynamic reliability score (0-95) for a signal.

    Combines:
    - Base score from signal quality (STRONG/MODERATE/WEAK/EARLY)
    - +5 per indicator confirmation (capped at +30)
    - +15 if a chart pattern was detected
    - +5 if volume confirms the move (ratio > 1.5)

    Result is clamped to [15, 95].
    """
    base = _QUALITY_BASE.get(quality, 20)
    confirmations = min(len(reasons or []) * 5, 30)
    pattern_bonus = 15 if patterns else 0
    volume_bonus = 5 if volume_ratio and volume_ratio > 1.5 else 0
    score = base + confirmations + pattern_bonus + volume_bonus
    return max(15, min(score, 95))


def compute_trend(d: dict, s: dict, change_threshold: float = 1.5) -> str:
    """Classify the current chart trend from signal + market data.

    Priority (most actionable first):
    1. BREAKOUT  — recent change > +change_threshold %
    2. PULLBACK  — recent change < -change_threshold %
    3. UPTREND   — MACD histogram positive AND fast MA above slow MA
    4. DOWNTREND — MACD histogram negative AND fast MA below slow MA
    5. NEUTRAL   — anything else (mixed signals)

    The MA-cross fallback replaces the old "macd_hist > 0 AND rsi < 50"
    rule, which rarely fired because momentum and oversold rarely
    coincide.
    """
    change = d.get('change', 0)
    if change > change_threshold:
        return 'BREAKOUT'
    if change < -change_threshold:
        return 'PULLBACK'

    macd_hist = s.get('macd_hist', 0)
    ma_fast = d.get('ma_fast', 0)
    ma_slow = d.get('ma_slow', 0)

    if macd_hist > 0 and ma_fast > ma_slow:
        return 'UPTREND'
    if macd_hist < 0 and ma_fast < ma_slow:
        return 'DOWNTREND'

    return 'NEUTRAL'


def _schedule_followup_scan(app, kind: str, delay: int = 60):
    """After a TP3 close, fire the matching signal scan once with a short delay
    so the user doesn't wait up to 5 minutes for a fresh BUY.
    """
    target = None
    if kind == 'crypto':
        from handlers.scheduler.jobs_crypto import check_crypto_signals
        target = check_crypto_signals
    elif kind == 'stock':
        from handlers.scheduler.jobs_stocks import check_stock_signals
        target = check_stock_signals
    if target is None:
        return
    try:
        app.job_queue.run_once(target, when=delay)
        logger.info(f"Follow-up {kind} scan scheduled in {delay}s after TP3")
    except Exception as e:
        logger.error(f"Failed to schedule follow-up {kind} scan: {e}", exc_info=True)


def cleanup_old_signals():
    """Remove signals older than SIGNAL_MAX_AGE_DAYS to prevent memory leak.

    Also enforces max signals per type limit.
    """
    signals = _get_last_buy_signals()

    now = datetime.now()
    removed_count = 0
    cutoff_time = now.timestamp() - (SIGNAL_MAX_AGE_DAYS * 24 * 3600)

    # Separate by type
    stock_signals = {k: v for k, v in signals.items() if v.get('type') == 'stock'}
    crypto_signals = {k: v for k, v in signals.items() if v.get('type') == 'crypto'}

    # Clean stock signals
    for key in list(stock_signals.keys()):
        signal_time = stock_signals[key].get('time')
        if isinstance(signal_time, str):
            try:
                signal_time = datetime.fromisoformat(signal_time)
            except (ValueError, TypeError) as e:
                logger.debug(f"Bad datetime for stock signal {key}: {e}; using now()")
                signal_time = now

        # Remove if too old
        if signal_time.timestamp() < cutoff_time:
            del signals[key]
            removed_count += 1
            continue

    # Clean crypto signals
    for key in list(crypto_signals.keys()):
        signal_time = crypto_signals[key].get('time')
        if isinstance(signal_time, str):
            try:
                signal_time = datetime.fromisoformat(signal_time)
            except (ValueError, TypeError) as e:
                logger.debug(f"Bad datetime for crypto signal {key}: {e}; using now()")
                signal_time = now

        if signal_time.timestamp() < cutoff_time:
            del signals[key]
            removed_count += 1

    # Enforce max limit per type (keep newest)
    for sig_type, sig_dict in [('stock', stock_signals), ('crypto', crypto_signals)]:
        prefix = sig_type.upper()
        type_keys = [k for k in signals if k.startswith(prefix)]

        if len(type_keys) > SIGNAL_MAX_PER_TYPE:
            # Sort by time, keep newest
            sorted_keys = sorted(
                type_keys,
                key=lambda k: (
                    signals[k].get('time', datetime.min)
                    if isinstance(signals[k].get('time'), datetime)
                    else datetime.fromisoformat(signals[k].get('time', datetime.min.isoformat()))
                    if isinstance(signals[k].get('time'), str)
                    else datetime.min
                ),
                reverse=True
            )
            # Remove oldest beyond limit
            for key in sorted_keys[SIGNAL_MAX_PER_TYPE:]:
                del signals[key]
                removed_count += 1

    if removed_count > 0:
        logger.info(f"Cleanup: removed {removed_count} old signals, {len(signals)} remaining")

    return removed_count
