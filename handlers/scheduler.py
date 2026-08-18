"""
Background job schedulers for the Telegram bot.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from services.stock_service import stock_service, FINNHUB_API_KEY
from services.crypto_service import crypto_service
from services.signal_service import signal_service, calc_tPSL
from utils.formatters import format_unified_crypto_notification, format_unified_stock_notification
from db import db

# NOTE: Import save_user_data inside functions to avoid module caching issues

# Import caches for cleanup
from utils.cache import _price_cache, _signal_cache, _market_cache, _usd_cache
from utils.rate_limiter import _circuit_breakers, APIState

# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))


def now_wib():
    """Get current time in WIB timezone (UTC+7)

    Always converts from UTC to ensure correct WIB time regardless of
    system timezone setting.
    """
    return datetime.now(timezone.utc).astimezone(WIB)


async def reset_stock_circuit_breaker(app):
    """Reset Yahoo stock circuit breaker at market open (09:00 WIB).

    Clears any stale OPEN state from the previous trading day so stock
    fetching starts fresh. Also clears the shared Yahoo breaker to avoid
    crypto scanner interference.
    """
    try:
        now = now_wib()
        if now.weekday() >= 5:
            return  # Skip weekends

        # Only act around 09:00 WIB (02:00 UTC)
        if now.hour != 9 or now.minute > 5:
            return

        breaker = _circuit_breakers.get('yahoo_stock')
        if breaker:
            breaker.state = APIState.CLOSED
            breaker.failure_count = 0
            breaker.half_open_calls = 0
            logger.info("[MARKET OPEN] Yahoo stock circuit breaker RESET")

        # Also reset shared Yahoo breaker to avoid crypto→stock interference
        yahoo_breaker = _circuit_breakers.get('yahoo')
        if yahoo_breaker:
            yahoo_breaker.state = APIState.CLOSED
            yahoo_breaker.failure_count = 0
            yahoo_breaker.half_open_calls = 0
            logger.info("[MARKET OPEN] Shared Yahoo circuit breaker RESET")

    except Exception as e:
        logger.error(f"[MARKET OPEN] Circuit breaker reset failed: {e}", exc_info=True)


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



def get_stock_data_with_fallback(ticker: str, interval: str = '5m', period: str = '3d'):
    """
    Get stock data with stale cache fallback.
    Returns (data, is_stale) tuple.
    Uses same cache key format as stock_service: {ticker}:{interval}:{period}
    """
    # Use same cache key format as stock_service for cache hits
    cache_key = f"{ticker}:{interval}:{period}"

    # Check cache first - use stock_service's cache key format
    cached = _price_cache.get(cache_key)
    if cached and cached.get('candles', 0) >= 5:
        return cached, False

    # Try fresh data
    d = stock_service.get_stock_data_combined(ticker, interval, period)
    if d and d.get('candles', 0) >= 5:
        # Also cache with fallback key format for other callers
        fallback_key = f"stock_{ticker}_{interval}_{period}"
        _price_cache.set(fallback_key, d, ttl=180)  # 3 min cache
        return d, False

    # Try stale cache (check both key formats)
    stale_d = _price_cache.get_stale(cache_key)
    if stale_d and stale_d.get('candles', 0) >= 5:
        logger.warning(f"Using stale cache for {ticker} (API may be down)")
        return stale_d, True

    fallback_key = f"stock_{ticker}_{interval}_{period}"
    stale_d = _price_cache.get_stale(fallback_key)
    if stale_d and stale_d.get('candles', 0) >= 5:
        logger.warning(f"Using stale cache for {ticker} (API may be down)")
        return stale_d, True

    return None, False


def get_crypto_data_with_fallback(ticker: str, interval: str = '1h', period: str = '1d'):
    """
    Get crypto data with stale cache fallback.
    Returns (data, is_stale) tuple.
    """
    cache_key = f"crypto_{ticker}_{interval}_{period}"

    # Check cache first before API call
    cached = _price_cache.get(cache_key)
    if cached and cached.get('candles', 0) >= 5:
        return cached, False

    # Try fresh data
    d = crypto_service.get_crypto_data_combined(ticker, interval, period)
    if d and d.get('candles', 0) >= 5:
        _price_cache.set(cache_key, d, ttl=300)  # 5 min cache
        return d, False

    # Try stale cache
    stale_d = _price_cache.get_stale(cache_key)
    if stale_d and stale_d.get('candles', 0) >= 5:
        logger.warning(f"Using stale crypto cache for {ticker} (API may be down)")
        return stale_d, True

    return None, False

logger = logging.getLogger(__name__)

# Global state
ALL_STOCKS = {}
last_prices = {}
last_crypto_prices = {}
market_cache = {}

# Yahoo rate-limit cooldown - skip stock scans when Yahoo is overloaded
# This prevents wasting time on scans that will just timeout
_yahoo_stock_cooldown = 0  # Number of scan cycles to skip
_stock_timeout_count = [0]  # Timeout counter for this scan (list for mutability)


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


def _get_user_db():
    """Get user database - reads directly from command_handlers to ensure fresh data"""
    import handlers.command_handlers as ch
    return ch.user_data_db


def _get_last_buy_signals():
    """Get last buy signals - reads directly from command_handlers"""
    import handlers.command_handlers as ch
    return ch.last_buy_signals

def _remove_signal(key: str):
    """Remove signal from persisted storage"""
    import handlers.command_handlers as ch
    if key in ch.last_buy_signals:
        del ch.last_buy_signals[key]
        logger.debug(f"Signal removed from storage: {key}")

# Signal retention settings
SIGNAL_MAX_AGE_DAYS = 7
SIGNAL_MAX_PER_TYPE = 50  # Max signals per type (stock/crypto)


def cleanup_old_signals():
    """
    Remove signals older than SIGNAL_MAX_AGE_DAYS to prevent memory leak.
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


def _schedule_followup_scan(app, kind: str, delay: int = 60):
    """After a TP3 close, fire the matching signal scan once with a short delay
    so the user doesn't wait up to 5 minutes for a fresh BUY."""
    target = None
    if kind == 'crypto':
        target = check_crypto_signals
    elif kind == 'stock':
        target = check_stock_signals
    if target is None:
        return
    try:
        app.job_queue.run_once(target, when=delay)
        logger.info(f"Follow-up {kind} scan scheduled in {delay}s after TP3")
    except Exception as e:
        logger.error(f"Failed to schedule follow-up {kind} scan: {e}", exc_info=True)


def set_all_stocks(stocks):
    """Set stocks reference"""
    global ALL_STOCKS
    ALL_STOCKS = stocks


def set_user_db(db):
    """Set user database reference (kept for backward compatibility)"""
    logger.info(f"[SCHEDULER] set_user_db called with {len(db)} users (now reading directly from command_handlers)")
    for uid, u in db.items():
        logger.info(f"[SCHEDULER]   User {uid}: notif_saham={u.get('notif_saham')}, notif_crypto={u.get('notif_crypto')}")


def set_last_prices(prices):
    """Set last prices reference"""
    global last_prices
    last_prices = prices


def set_last_crypto_prices(prices):
    """Set last crypto prices reference"""
    global last_crypto_prices
    last_crypto_prices = prices


def set_last_buy_signals(signals):
    """Set last buy signals reference"""
    global last_buy_signals
    last_buy_signals = signals


def get_market_snapshot():
    """Get cached market snapshot data"""
    global market_cache
    return market_cache


async def check_favorit_alerts(app):
    """Check favorit stocks and send alerts when target price is reached"""
    try:
        now = now_wib()
        is_weekend = now.weekday() >= 5
        is_market_hours = 8 <= now.hour < 16

        if is_weekend or not is_market_hours:
            return

        for uid, u in _get_user_db().items():
            favorit = u.get('favorit', {})
            if not favorit:
                continue

            for ticker, target_price in list(favorit.items()):
                if target_price is None:
                    continue

                try:
                    d = stock_service.get_stock_data_combined(ticker + ".JK", '5m', '1d')
                    if not d:
                        continue

                    current_price = d['price']

                    # Check if price reached target
                    if current_price >= target_price:
                        name = ALL_STOCKS.get(ticker, ticker)
                        emoji = "🎯"

                        msg = f"{emoji} *TARGET TERCAPAI: {name} ({ticker})*\n\n"
                        msg += f"💰 Target: Rp {target_price:,.0f}\n"
                        msg += f"📈 Current: Rp {current_price:,.0f}\n"
                        msg += f"📊 Profit: {((current_price - target_price) / target_price * 100):+.2f}%\n\n"
                        msg += "🎉 Harga sudah menyentuh target!\n"
                        msg += "Saatnya take profit atau hold?"

                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"Favorit alert: {ticker} reached target {target_price}")

                        # Remove from favorit after alert
                        del favorit[ticker]
                        logger.info(f"Removed {ticker} from favorit after target reached")

                except Exception as e:
                    logger.error(f"Error checking favorit {ticker}: {e}", exc_info=True)
                    continue

    except Exception as e:
        logger.error(f"Error in check_favorit_alerts: {e}", exc_info=True)


async def check_bsjp_signals(app):
    """Check BSJP (Beli Sore Jual Pagi) signals and send notifications"""
    try:
        now = now_wib()

        if now.weekday() >= 5:
            return  # Skip weekends

        # Window: 14:00 - 16:00 WIB (market afternoon before close)
        if not (14 <= now.hour < 16):
            return

        # Check if already sent today
        if _check_notification_sent_today('bsjp'):
            logger.info("[BSJP] Already sent today - skipping")
            return

        # Check if any user has notif_bsjp enabled
        bsjp_users = [uid for uid, u in _get_user_db().items() if u.get('notif_bsjp', False)]

        if not bsjp_users:
            logger.info(f"[BSJP] No users with notif_bsjp enabled")
            return

        logger.info(f"[BSJP] Scanning for {len(bsjp_users)} users...")

        # Scan stocks for BSJP signals (parallel fetch)
        bsjp_signals = []
        # Scan more stocks for better coverage
        tickers = list(ALL_STOCKS.keys())[:200]

        # Track if using stale data
        using_stale_data = [False]

        def analyze_bsjp(ticker):
            """Blocking BSJP analysis - runs in thread pool"""
            try:
                # Try 1h interval first for intraday momentum
                d, is_stale = get_stock_data_with_fallback(ticker + ".JK", '1h', '5d')

                # If no fresh data, try ANY cached data even if very stale
                if not d:
                    # Try to get from price cache directly, any format
                    cache_key = f"{ticker}.JK:1h:5d"
                    d = _price_cache.get(cache_key)
                    if not d:
                        cache_key2 = f"stock_{ticker}.JK_1h_5d"
                        d = _price_cache.get(cache_key2)
                    if not d:
                        cache_key3 = f"{ticker}.JK:5m:3d"
                        d = _price_cache.get(cache_key3)
                    if d:
                        logger.warning(f"[BSJP] Using very stale cache for {ticker}")
                        using_stale_data[0] = True

                if not d or d.get('candles', 0) < 10:
                    return None

                if is_stale:
                    using_stale_data[0] = True

                price = d['price']
                rsi = d.get('rsi', 50)
                ma_fast = d.get('ma_fast', price)
                ma_slow = d.get('ma_slow', price)
                macd_hist = d.get('macd_hist', 0)
                change = d.get('change', 0)
                volume_ratio = d.get('volume_ratio', 1.0)

                # BSJP criteria - more flexible:
                # 1. Price above at least MA Fast (bullish)
                # 2. RSI not overbought/oversold (flexible range)
                # 3. MACD histogram positive (momentum confirmation)
                # 4. Volume above average (optional boost)

                score = 0
                reasons = []

                # MA condition: price above MA fast is minimum
                if price > ma_fast:
                    score += 2
                    reasons.append("Above MA Fast")
                if ma_fast > ma_slow:
                    score += 1
                    reasons.append("Golden Cross")

                # RSI: wider range (25-75)
                if 25 < rsi < 75:
                    score += 1
                    reasons.append(f"RSI {rsi:.0f} OK")

                # MACD momentum
                if macd_hist > 0:
                    score += 1
                    reasons.append("MACD Bullish")

                # Volume confirmation
                if volume_ratio > 1.2:
                    score += 1
                    reasons.append(f"Vol {volume_ratio:.1f}x")

                # Change bonus
                if change > 0:
                    score += 1
                    reasons.append(f"+{change:.1f}%")

                # Minimum score threshold
                if score >= 3:
                    return {
                        'ticker': ticker,
                        'name': ALL_STOCKS.get(ticker, ticker),
                        'price': price,
                        'rsi': rsi,
                        'change': change,
                        'score': score,
                        'macd': macd_hist,
                        'volume_ratio': volume_ratio,
                        'reasons': ', '.join(reasons),
                        'tp': price * 1.02,
                        'sl': price * 0.985
                    }
            except Exception as e:
                logger.error(f"[BSJP] analyze inner failure for {ticker}: {e}", exc_info=True)
            return None

        # Increase semaphore for faster scanning
        semaphore = asyncio.Semaphore(50)
        async def fetch_with_semaphore(ticker):
            async with semaphore:
                return await asyncio.to_thread(analyze_bsjp, ticker)

        tasks = [fetch_with_semaphore(t) for t in tickers]
        results = await asyncio.gather(*tasks)
        bsjp_signals = [r for r in results if r is not None]

        # Send notifications
        if bsjp_signals:
            bsjp_signals.sort(key=lambda x: x['score'], reverse=True)

            for uid in bsjp_users:
                try:
                    msg = "🌙 *BSJP - Beli Sore Jual Pagi*\n"
                    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    msg += f"🕐 {now.strftime('%d %b %H:%M')}\n"
                    msg += f"📊 {len(bsjp_signals)} sinyal ditemukan\n\n"

                    for s in bsjp_signals[:10]:
                        msg += f"🟢 *{s['ticker']}* - {s['name']}\n"
                        msg += f"   💰 Entry: Rp {s['price']:,.0f}\n"
                        msg += f"   📊 RSI: {s['rsi']:.1f} | {s['reasons']}\n"
                        msg += f"   🎯 TP: {s['tp']:,.0f} | SL: {s['sl']:,.0f}\n\n"

                    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    msg += "💡 Beli jam 14-16, jual besok pagi\n"
                    msg += "⚠️ Trading risiko tanggung sendiri"

                    # Use retry helper for reliable delivery
                    sent = await _send_bot_with_retry(app.bot, int(uid), msg, parse_mode='Markdown')
                    if sent:
                        logger.info(f"[BSJP] Sent {len(bsjp_signals)} signals to user {uid}")
                    else:
                        logger.error(f"[BSJP] Failed to send to user {uid} after retries")

                except Exception as e:
                    logger.error(f"Failed to send BSJP to user {uid}: {e}", exc_info=True)

            # Mark as sent today ONLY if signals were found AND we have users to send to
            # (Don't mark if all sends failed - will retry next cycle)
            if bsjp_signals and bsjp_users:
                _mark_notification_sent_today('bsjp')
                logger.info("[BSJP] Marked as sent for today")

        # Only mark as sent if we genuinely found no signals (not due to errors)
        # If bsjp_signals is empty because of errors, we'll retry in the next cycle
        if not bsjp_signals:
            logger.info(f"[BSJP] Scan complete: 0 signals (will retry next cycle if within window)")

        logger.info(f"[BSJP] Scan complete: {len(bsjp_signals)} signals")

    except Exception as e:
        logger.error(f"Error in check_bsjp_signals: {e}", exc_info=True)


async def check_stock_signals(app):
    """Check stock signals per user TF and send BUY notifications with per-TF TP/SL.

    Optimized flow:
    1. Scan each ticker once per UNIQUE interval (deduplicated across TF groups)
    2. Reuse cached scan data when sending notifications (no re-fetch)
    """
    global _yahoo_stock_cooldown

    try:
        now = now_wib()

        # Check market hours
        is_weekend = now.weekday() >= 5
        is_market_hours = 8 <= now.hour < 16

        if is_weekend:
            logger.info("Weekend - skipping stock signals")
            return

        if not is_market_hours:
            logger.info(f"Outside market hours ({now.hour}:00 WIB) - skipping stock signals")
            return

        # Skip if Yahoo is in cooldown (rate-limited)
        if _yahoo_stock_cooldown > 0:
            _yahoo_stock_cooldown -= 1
            logger.info(f"[STOCK] Yahoo cooldown active ({_yahoo_stock_cooldown} scans remaining) - skipping")
            return

        # Get fresh user data
        user_db = _get_user_db()

        # Group users by their selected timeframe
        tf_groups = {}  # tf_key -> [(uid, user_data), ...]
        for uid, u in user_db.items():
            if u.get('notif_saham', False):
                tf = u.get('timeframe', '5')
                tf_groups.setdefault(tf, []).append((uid, u))

        if not tf_groups:
            logger.info("[STOCK] No users with notif_saham enabled")
            return

        total_users = sum(len(v) for v in tf_groups.values())
        logger.info(f"[STOCK SIGNALS] {total_users} users in {len(tf_groups)} TF groups")

        # Limit scan to top 30 most liquid stocks - reduced for rate limit
        all_tickers = list(ALL_STOCKS.keys())[:30]
        _stock_timeout_count[0] = 0

        # === PHASE 1: Dedup scan per UNIQUE (interval, period) ===
        # Collect unique intervals needed across all TF groups
        needed_intervals = {}  # (interval, period) -> [tf_keys]
        for tf_key in tf_groups:
            interval, period = TF_TO_INTERVAL.get(tf_key, ('5m', '5d'))
            needed_intervals.setdefault((interval, period), []).append(tf_key)

        # Scan cache: (ticker, interval, period) -> (data_dict, signal_dict)
        scan_cache = {}  # (ticker, interval, period) -> [(tf_key, signal, d)]
        semaphore = asyncio.Semaphore(25)

        async def scan_ticker(ticker, interval, period, tf_keys):
            """Fetch data once and generate signals for all TF keys needing this interval."""
            try:
                d, _ = get_stock_data_with_fallback(ticker + ".JK", interval, period)
                if not d or d.get('candles', 0) < 5:
                    return

                results = []
                for tf_key in tf_keys:
                    try:
                        # Pass TF for timeframe-aware signal generation
                        d_with_tf = dict(d)
                        d_with_tf['timeframe'] = tf_key
                        s = signal_service.generate_stock_signal(d_with_tf)
                        if not s.get('entry') or s.get('entry', 0) <= 0:
                            continue

                        is_buy_or_reversal = s['signal'] in ('BUY', 'REVERSAL')
                        if is_buy_or_reversal and s.get('buy_score', 0) >= 25:
                            results.append((tf_key, ticker, ALL_STOCKS.get(ticker, ticker), d, s))
                    except Exception as e:
                        logger.error(f"[STOCK_SIGNAL] signal gen failure for {ticker}/{tf_key}: {e}", exc_info=True)

                if results:
                    scan_cache[(ticker, interval, period)] = results
            except Exception as e:
                logger.error(f"[STOCK_SIGNAL] analyze failure for {ticker}: {e}", exc_info=True)

        async def scan_with_semaphore(ticker, interval, period, tf_keys):
            try:
                async with semaphore:
                    await asyncio.wait_for(
                        scan_ticker(ticker, interval, period, tf_keys),
                        timeout=30.0
                    )
            except asyncio.TimeoutError:
                _stock_timeout_count[0] += 1
                logger.warning(f"[STOCK] Timeout for {ticker}")
            except Exception as e:
                _stock_timeout_count[0] += 1
                logger.error(f"[STOCK] Error fetching {ticker}: {e}", exc_info=True)

        # Schedule scan tasks: one per (ticker, interval, period)
        scan_tasks = []
        for (interval, period), tf_keys in needed_intervals.items():
            for ticker in all_tickers:
                if ticker in crypto_service.crypto_pairs:
                    continue
                scan_tasks.append(scan_with_semaphore(ticker, interval, period, tf_keys))

        logger.info(f"[STOCK] Scheduling {len(scan_tasks)} scan tasks ({len(needed_intervals)} intervals × {len(all_tickers)} tickers)")
        await asyncio.gather(*scan_tasks, return_exceptions=True)

        # === PHASE 2: Build per-TF top signals from cache ===
        # scan_cache[(ticker, interval, period)] = [(tf_key, ticker, name, d, s), ...]
        # Group results by tf_key
        tf_signals = {}  # tf_key -> [(ticker, name, d, s)]
        for results in scan_cache.values():
            for tf_key, ticker, name, d, s in results:
                tf_signals.setdefault(tf_key, []).append((ticker, name, d, s))

        # === PHASE 3: Send notifications per TF group ===
        signals = _get_last_buy_signals()

        for tf_key, group_users in tf_groups.items():
            group_buy_signals = tf_signals.get(tf_key, [])
            if not group_buy_signals:
                logger.info(f"[STOCK] TF={tf_key}: No BUY signals found")
                continue

            # Dedup by ticker (might have multiple signals from different intervals)
            seen = set()
            unique_signals = []
            for sig in group_buy_signals:
                if sig[0] not in seen:
                    seen.add(sig[0])
                    unique_signals.append(sig)
            group_buy_signals = unique_signals

            # Filter out signals already sent (within 24h window)
            fresh_signals = []
            for ticker, name, d, s in group_buy_signals:
                key = f"STOCK_{ticker}_{tf_key}"
                existing = signals.get(key)
                should_send = False
                if existing is None:
                    should_send = True
                else:
                    time_diff = _safe_time_diff(now, existing.get('time', now))
                    if time_diff > 86400:
                        last_entry = existing.get('entry', 0)
                        if last_entry > 0:
                            current_price = d['price']
                            price_change = abs(current_price - last_entry) / last_entry
                            if price_change > 0.05:
                                should_send = True
                if should_send:
                    fresh_signals.append((ticker, name, d, s))

            if not fresh_signals:
                logger.info(f"[STOCK] TF={tf_key}: All signals already sent recently")
                continue

            # Sort by score and take top 3
            fresh_signals.sort(key=lambda x: x[3].get('buy_score', 0), reverse=True)
            top_signals = fresh_signals[:3]

            logger.info(f"[STOCK] TF={tf_key}: Found {len(fresh_signals)} fresh signals, sending TOP 3 to {len(group_users)} users")

            # Send signals to users in this TF group
            for uid, u in group_users:
                try:
                    for i, (ticker, name, d, s) in enumerate(top_signals):
                        if i > 0:
                            await asyncio.sleep(60)

                        # Use cached scan data (no re-fetch)
                        d = d  # Use data from scan cache
                        entry_price = d['price']
                        atr = d.get('atr', entry_price * 0.015)
                        entry_low = entry_price * 0.995
                        entry_high = entry_price * 1.005
                        s['entry'] = entry_price
                        s['entry_low'] = entry_low
                        s['entry_high'] = entry_high
                        s['atr'] = atr
                        s['rsi'] = d.get('rsi', 50)
                        # Calculate TP/SL using user's TF
                        tpsl = calc_tPSL('BUY', entry_price, atr, tf_key)
                        s['tp1'] = tpsl['tp1']
                        s['tp2'] = tpsl['tp2']
                        s['tp3'] = tpsl['tp3']
                        s['sl'] = tpsl['sl']
                        logger.info(f"[STOCK] Using cached data for {ticker}: {entry_price:,.0f}")

                        quality = s.get('quality', 'WEAK')
                        quality_reliability = {'STRONG': 75, 'MODERATE': 60, 'WEAK': 45}.get(quality, 50)

                        # Determine trend
                        trend = 'NEUTRAL'
                        if s.get('macd_hist', 0) > 0 and d.get('rsi', 50) < 50:
                            trend = 'UPTREND'
                        elif s.get('macd_hist', 0) < 0 and d.get('rsi', 50) > 50:
                            trend = 'DOWNTREND'
                        elif d.get('change', 0) > 2:
                            trend = 'BREAKOUT'
                        elif d.get('change', 0) < -2:
                            trend = 'PULLBACK'

                        # Build reasons
                        reasons = []
                        patterns_detected = []
                        if d.get('rsi', 50) < 40:
                            reasons.append(f"RSI Oversold ({d.get('rsi', 0):.0f})")
                        if d.get('ma_fast', 0) > d.get('ma_slow', 0):
                            reasons.append("MA Golden Cross")
                        if s.get('macd_hist', 0) > 0:
                            reasons.append("MACD Bullish")
                        if d.get('volume_ratio', 1) > 1.5:
                            reasons.append(f"Volume Spike ({d.get('volume_ratio', 1):.1f}x)")
                        if d.get('change', 0) > 0:
                            reasons.append(f"Price +{d.get('change', 0):.1f}%")
                        if d.get('bb_position', 0.5) < 0.3:
                            reasons.append("Near Bollinger Lower")

                        # Detect patterns
                        try:
                            from utils.patterns import detect_all_patterns
                            if d.get('candles', 0) >= 20 and 'raw_df' in d:
                                patterns = detect_all_patterns(d['raw_df'])
                                if patterns.get('patterns_found', 0) > 0:
                                    strongest = patterns.get('strongest_pattern')
                                    if strongest:
                                        patterns_detected.append({
                                            'name': strongest.get('name', ''),
                                            'strength': strongest.get('strength', 0),
                                            'description': strongest.get('description', '')
                                        })
                        except Exception as e:
                            logger.debug(f"Pattern detection failed: {e}")

                        analysis_data = {
                            'pattern': {'type': trend, 'reliability': quality_reliability},
                            'patterns': patterns_detected,
                            'indicators': {
                                'rsi': d.get('rsi', 0),
                                'macd': s.get('macd_hist', 0),
                                'atr': s.get('atr', 0),
                            },
                            'reasons': reasons,
                            'score': s.get('buy_score', 0),
                            'quality': quality,
                            'rsi': d.get('rsi', 0),
                            'volume_ratio': d.get('volume_ratio', 1),
                            'change': d.get('change', 0),
                            'ma_fast': d.get('ma_fast', 0),
                            'ma_slow': d.get('ma_slow', 0),
                            'sr': d.get('sr', {}),
                            'support': d.get('support'),
                            'resistance': d.get('resistance'),
                        }

                        try:
                            msg = format_unified_stock_notification(
                                notif_type='BUY',
                                ticker=ticker,
                                name=name,
                                entry=s['entry'],
                                current_price=d['price'],
                                tp1=s['tp1'],
                                tp2=s['tp2'],
                                tp3=s['tp3'],
                                sl=s['sl'],
                                analysis_data=analysis_data,
                                change_pct=d.get('change', 0),
                                profit_loss=1.0,
                                entry_low=s.get('entry_low', 0),
                                entry_high=s.get('entry_high', 0)
                            )
                            await app.bot.send_message(
                                chat_id=int(uid), text=msg, parse_mode='Markdown',
                                read_timeout=10, connect_timeout=10
                            )
                            logger.info(f"[STOCK] Sent BUY [{tf_key}] for {ticker} to user {uid}")

                            # Store signal for TP/SL tracking
                            key = f"STOCK_{ticker}_{uid}"
                            signal_type = s['signal'] if s.get('signal') in ('BUY', 'REVERSAL') else 'BUY'
                            signals[key] = {
                                'name': name,
                                'entry': s['entry'],
                                'tp1': s['tp1'], 'tp2': s['tp2'], 'tp3': s['tp3'],
                                'sl': s['sl'], 'time': now_wib(),
                                'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
                                'type': 'stock', 'direction': 'LONG', 'ticker_raw': ticker,
                                'buy_score': s.get('buy_score', 0),
                                'quality': s.get('quality', 'WEAK'),
                                'signal_type': signal_type,
                                'is_reversal': s.get('is_reversal', False),
                                'atr': s.get('atr', 0),
                                'user_id': uid,
                                'timeframe': tf_key,
                            }
                            # Persist to DB for recovery after restart
                            db.save_active_signal(
                                key=key,
                                ticker=ticker,
                                asset_type='stock',
                                signal_type=signal_type,
                                price=s['entry'],
                                tp1=s['tp1'], tp2=s['tp2'], tp3=s['tp3'],
                                sl=s['sl'],
                                score=s.get('buy_score', 0),
                                quality=s.get('quality', 'WEAK'),
                                reason=s.get('reason', ''),
                                extra_data={
                                    'name': name,
                                    'is_reversal': s.get('is_reversal', False),
                                    'atr': s.get('atr', 0),
                                    'user_id': uid,
                                    'timeframe': tf_key,
                                }
                            )
                        except Exception as e:
                            logger.error(f"[STOCK] Failed to send message for {ticker}: {e}", exc_info=True)

                    logger.info(f"[STOCK] Sent TOP 3 [{tf_key}] signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send signals to user {uid}: {e}", exc_info=True)

        # Cooldown check
        timeout_pct = _stock_timeout_count[0] / len(all_tickers) * 100 if all_tickers else 0
        if _stock_timeout_count[0] >= len(all_tickers) // 2:
            _yahoo_stock_cooldown = 3
            logger.warning(f"[STOCK] High timeout rate ({_stock_timeout_count[0]}/{len(all_tickers)} = {timeout_pct:.0f}%) - enabling cooldown")

    except Exception as e:
        logger.error(f"Error in check_stock_signals: {e}", exc_info=True)


async def check_stock_tp_sl(app):
    """Check and notify TP/SL hits for tracked stock signals"""
    try:
        now = now_wib()
        is_weekend = now.weekday() >= 5
        is_market_hours = 8 <= now.hour < 16

        # Only run during market hours
        if is_weekend or not is_market_hours:
            logger.debug(f"[STOCK TP/SL] Outside market hours ({now.hour}:{now.minute:02d} WIB) - skipping")
            return

        for uid, u in _get_user_db().items():
            if not u.get('notif_saham', False):
                continue

            signals = _get_last_buy_signals()
            for key, signal_data in list(signals.items()):
                if signal_data.get('type') != 'stock':
                    continue

                # Skip signals not owned by this user (per-user TP/SL tracking)
                if signal_data.get('user_id') != uid:
                    continue

                ticker = signal_data.get('ticker_raw')
                if not ticker:
                    continue

                try:
                    # Get user's TF from their settings for per-user TP/SL calculation
                    user_tf = u.get('timeframe', '5')
                    interval, period = TF_TO_INTERVAL.get(user_tf, ('5m', '5d'))
                    d = stock_service.get_stock_data_combined(ticker + ".JK", interval, period)
                    if not d:
                        continue

                    current_price = d['price']
                    entry = signal_data.get('entry', 0)
                    atr = signal_data.get('atr', 0)

                    # Recalculate TP/SL based on user's TF (per-user TP/SL)
                    if entry > 0 and atr > 0:
                        tpsl = calc_tPSL('BUY', entry, atr, user_tf)
                        tp1 = tpsl['tp1']
                        tp2 = tpsl['tp2']
                        tp3 = tpsl['tp3']
                        sl = tpsl['sl']
                    else:
                        # Fallback to stored values if ATR not available
                        tp1 = signal_data.get('tp1', 0)
                        tp2 = signal_data.get('tp2', 0)
                        tp3 = signal_data.get('tp3', 0)
                        sl = signal_data.get('sl', 0)
                    tp_hit = signal_data.get('tp_hit', {'tp1': False, 'tp2': False, 'tp3': False})

                    if entry <= 0:
                        continue

                    tp_analysis = {
                        'indicators': {
                            'atr': entry * 0.02,
                        }
                    }

                    # === CHECK SL FIRST ===
                    # If SL hit, send SL notification and delete signal immediately.
                    # Skip TP checks to avoid sending TP after SL.
                    if current_price <= sl > 0:
                        profit_pct = ((current_price - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_stock_notification(
                            notif_type='SL',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"SL hit: {ticker} at {current_price} - Position closed")

                        db.save_signal_outcome(key, 'sl', closed_price=current_price)

                        del signals[key]
                        _remove_signal(key)  # Also remove from persisted storage
                        continue  # Skip TP checks for this signal

                    # === CHECK TP (only if SL not hit) ===
                    # Check TP1 hit
                    if not tp_hit.get('tp1') and current_price >= tp1 > 0:
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp1')
                        profit_pct = ((tp1 - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_stock_notification(
                            notif_type='TP1',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"TP1 hit: {ticker} at {current_price}")

                    # Check TP2 hit
                    if not tp_hit.get('tp2') and current_price >= tp2 > 0:
                        tp_hit['tp2'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp2')
                        profit_pct = ((tp2 - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_stock_notification(
                            notif_type='TP2',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"TP2 hit: {ticker} at {current_price}")

                    # Check TP3 hit
                    if not tp_hit.get('tp3') and current_price >= tp3 > 0:
                        tp_hit['tp3'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp3', closed_price=current_price)
                        profit_pct = ((tp3 - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_stock_notification(
                            notif_type='TP3',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"TP3 hit: {ticker} at {current_price} - Position closed (target achieved)")

                        # Auto-remove from tracking after TP3 (position closed)
                        del signals[key]
                        _remove_signal(key)  # Also remove from persisted storage

                        # Trigger a fresh signal scan within ~60s so user gets a new BUY quickly
                        _schedule_followup_scan(app, 'stock', delay=60)
                        continue  # Skip remaining checks for this signal

                except Exception as e:
                    logger.error(f"TP/SL check error for {key}: {e}", exc_info=True)
                    continue

    except Exception as e:
        logger.error(f"Error in check_stock_tp_sl: {e}", exc_info=True)


async def check_alerts(app):
    """Check price alerts and notify users"""
    try:
        now = now_wib()
        if now.hour < 8 or now.hour > 16:
            return

        for uid, u in _get_user_db().items():
            alerts = u.get('alerts', {})
            if not alerts:
                continue

            tickers_to_remove = []

            for ticker, a in list(alerts.items()):
                try:
                    d = stock_service.get_stock_data_combined(ticker + ".JK", '1m', '1d')
                    if not d:
                        continue

                    current = d['price']
                    target = a['price']
                    alert_type = a['type']

                    triggered = False
                    if alert_type == 'BUY' and current <= target:
                        triggered = True
                        msg = f"🟢 *ALERT BUY!*\n\n"
                        msg += f"{ticker} sudah turun ke Rp {current:,.0f}\n"
                        msg += f"Target: Rp {target:,.0f}\n\n"
                        msg += "Saatnya buy!"

                    elif alert_type == 'SELL' and current >= target:
                        triggered = True
                        msg = f"🔴 *ALERT SELL!*\n\n"
                        msg += f"{ticker} sudah naik ke Rp {current:,.0f}\n"
                        msg += f"Target: Rp {target:,.0f}\n\n"
                        msg += "Saatnya sell!"

                    if triggered:
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        tickers_to_remove.append(ticker)
                        logger.info(f"Alert triggered: {ticker} at {current}")

                except Exception as e:
                    logger.error(f"Alert error for {ticker}: {e}", exc_info=True)

            for t in tickers_to_remove:
                if t in alerts:
                    del alerts[t]

    except Exception as e:
        logger.error(f"Check alerts error: {e}", exc_info=True)


import os

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


# Backward compatibility wrappers
def _check_morning_sent_today():
    return _check_notification_sent_today('morning')


def _mark_morning_sent():
    _mark_notification_sent_today('morning')


# Legacy file-based function names (still used by some tests)
def _check_sent_today(filepath: str) -> bool:
    """Check if notification was already sent today (file-based only)."""
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
    """Mark notification as sent today (file-based only)."""
    try:
        with open(filepath, 'w') as f:
            f.write(now_wib().date().isoformat())
    except Exception as e:
        logger.warning(f"Failed to write sent-marker {filepath}: {e}")


async def check_morning_notification(app):
    """Send morning signals between 07:15-08:00 WIB"""
    try:
        now = now_wib()

        # Skip weekends
        if now.weekday() >= 5:
            return

        # Only send between 07:15-08:00 WIB (before market open)
        if now.hour == 7 and now.minute < 15:
            return
        if now.hour < 7 or now.hour > 8:
            return

        # Check if already sent today (file-based)
        if _check_morning_sent_today():
            logger.info("[MORNING] Already sent today - skipping")
            return

        # Check if any user has notif_morning enabled
        morning_users = [uid for uid, u in _get_user_db().items() if u.get('notif_morning', False)]

        if not morning_users:
            logger.info("[MORNING] No users with notif_morning enabled")
            return

        logger.info(f"[MORNING] Window open at {now.strftime('%H:%M')} - scanning for {len(morning_users)} users...")

        # Scan stocks for morning signals (parallel fetch)
        morning_signals = []
        tickers = list(ALL_STOCKS.keys())[:100]

        # Track if using stale data
        using_stale_data = [False]

        def analyze_stock(ticker):
            """Blocking stock analysis - runs in thread pool"""
            try:
                d, is_stale = get_stock_data_with_fallback(ticker + ".JK", '1h', '3d')

                # If no fresh data, try ANY cached data even if very stale
                if not d:
                    cache_key = f"{ticker}.JK:1h:3d"
                    d = _price_cache.get(cache_key)
                    if not d:
                        cache_key2 = f"stock_{ticker}.JK_1h_3d"
                        d = _price_cache.get(cache_key2)
                    if not d:
                        cache_key3 = f"{ticker}.JK:5m:3d"
                        d = _price_cache.get(cache_key3)
                    if d:
                        using_stale_data[0] = True

                if not d or d.get('candles', 0) < 10:
                    return None

                if is_stale:
                    using_stale_data[0] = True

                price = d['price']
                rsi = d.get('rsi', 50)
                ma_fast = d.get('ma_fast', price)
                ma_slow = d.get('ma_slow', price)
                change = d.get('change', 0)

                score = 0
                reasons = []

                if rsi < 35:
                    score += 3
                    reasons.append(f"RSI {rsi:.0f} oversold")
                elif rsi < 45:
                    score += 2
                    reasons.append(f"RSI {rsi:.0f} bullish")

                if price > ma_fast > ma_slow:
                    score += 2
                    reasons.append("Above MA")
                elif price > ma_fast:
                    score += 1
                    reasons.append("Above Fast MA")

                if change > 1:
                    score += 1
                    reasons.append(f"+{change:.1f}%")

                if score >= 3:
                    return {
                        'ticker': ticker,
                        'name': ALL_STOCKS.get(ticker, ticker),
                        'price': price,
                        'rsi': rsi,
                        'change': change,
                        'score': score,
                        'reasons': ', '.join(reasons),
                        'tp': price * 1.03,
                        'sl': price * 0.98
                    }
            except Exception as e:
                logger.error(f"[MORNING] analyze inner failure for {ticker}: {e}", exc_info=True)
            return None

        semaphore = asyncio.Semaphore(20)
        async def fetch_with_semaphore(ticker):
            async with semaphore:
                return await asyncio.to_thread(analyze_stock, ticker)

        tasks = [fetch_with_semaphore(t) for t in tickers]
        results = await asyncio.gather(*tasks)
        morning_signals = [r for r in results if r is not None]

        # Send notifications
        if morning_signals:
            morning_signals.sort(key=lambda x: x['score'], reverse=True)

            for uid in morning_users:
                try:
                    msg = "☀️ *SARAN PAGI*\n"
                    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    msg += f"🕐 {now.strftime('%d %b %H:%M')}\n"
                    msg += f"📊 {len(morning_signals)} saham potensial\n\n"

                    for s in morning_signals[:10]:
                        emoji = "🟢" if s['score'] >= 6 else "🟡"
                        msg += f"{emoji} *{s['ticker']}* - {s['name']}\n"
                        msg += f"   💰 Entry: Rp {s['price']:,.0f}\n"
                        msg += f"   📊 RSI: {s['rsi']:.1f} | {s['reasons']}\n"
                        msg += f"   🎯 TP: {s['tp']:,.0f} | SL: {s['sl']:,.0f}\n\n"

                    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    msg += "💡 Sinyal untuk hari ini\n"
                    msg += "⚠️ Trading risiko tanggung sendiri"

                    await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                    logger.info(f"[MORNING] Sent {len(morning_signals)} signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send morning to user {uid}: {e}", exc_info=True)

            # Mark as sent today AFTER all users processed (only if signals found)
            if morning_signals:
                _mark_morning_sent()
                logger.info("[MORNING] Marked as sent for today")

        # Only log if no signals found
        if not morning_signals:
            logger.info(f"[MORNING] Scan complete: 0 signals (will retry next cycle if within window)")

        logger.info(f"[MORNING] Scan complete: {len(morning_signals)} signals")

    except Exception as e:
        logger.error(f"Error in check_morning_notification: {e}", exc_info=True)


async def check_crypto_signals(app):
    """Check crypto signals 24/7 and send notifications per-user TF.

    Groups users by timeframe, scans crypto per TF group, sends signals
    with TP/SL calculated for each user's selected timeframe.
    """
    try:
        now = now_wib()

        # Group users by their selected timeframe
        tf_groups = {}
        for uid, u in _get_user_db().items():
            if u.get('notif_crypto', False):
                tf = u.get('timeframe', '60')
                tf_groups.setdefault(tf, []).append((uid, u))

        if not tf_groups:
            logger.info("[CRYPTO] No users with notif_crypto enabled")
            return

        total_users = sum(len(v) for v in tf_groups.values())
        logger.info(f"[CRYPTO SIGNALS] {total_users} users in {len(tf_groups)} TF groups")

        # Get all crypto pairs to scan
        all_crypto = list(crypto_service.crypto_pairs.keys())

        signals = _get_last_buy_signals()

        # Process each TF group separately
        for tf_key, group_users in tf_groups.items():
            interval, period = CRYPTO_TF_TO_INTERVAL.get(tf_key, ('1h', '1mo'))
            logger.info(f"[CRYPTO] TF={tf_key} ({interval}): scanning for {len(group_users)} users")

            buy_signals = []

            def analyze_crypto_for_tf(ticker):
                """Blocking crypto analysis for specific TF"""
                try:
                    import time as _time
                    _time.sleep(0.3)

                    d, is_stale = get_crypto_data_with_fallback(ticker, interval, period)
                    if not d or d.get('candles', 0) < 5:
                        return None

                    # Pass TF for timeframe-aware signal generation
                    d['timeframe'] = tf_key
                    s = signal_service.generate_crypto_signal(d)
                    if not s.get('entry') or s['entry'] <= 0:
                        return None

                    current_price = d['price']
                    key = f"CRYPTO_{ticker}_{tf_key}"

                    # Include REVERSAL signals
                    is_buy_or_reversal = s['signal'] in ('BUY', 'REVERSAL')

                    if is_buy_or_reversal and s.get('buy_score', 0) >= 25:
                        existing = signals.get(key)
                        should_send = False

                        if existing is None:
                            should_send = True
                        else:
                            time_diff = _safe_time_diff(now, existing.get('time', now))
                            if time_diff > 86400:  # 24 hours
                                last_entry = existing.get('entry', 0)
                                if last_entry > 0:
                                    price_change = abs(current_price - last_entry) / last_entry
                                    if price_change > 0.05:
                                        should_send = True

                        if should_send:
                            return (ticker, crypto_service.crypto_pairs.get(ticker, ticker), d, s)

                    return None
                except Exception as e:
                    logger.error(f"[CRYPTO_SIGNAL] analyze failure for {ticker}: {e}", exc_info=True)
                    return None

            semaphore = asyncio.Semaphore(20)
            async def fetch_crypto(ticker):
                async with semaphore:
                    return await asyncio.to_thread(analyze_crypto_for_tf, ticker)

            tasks = [fetch_crypto(t) for t in all_crypto]
            results = await asyncio.gather(*tasks)

            for r in results:
                if r is not None:
                    ticker, name, d, s = r
                    buy_signals.append((ticker, name, d, s))
                    logger.info(f"CRYPTO [{tf_key}] Signal: {ticker} @ ${s['entry']:,.2f}")

            if not buy_signals:
                logger.info(f"[CRYPTO] TF={tf_key}: No BUY signals found")
                continue

            buy_signals.sort(key=lambda x: x[3].get('buy_score', 0), reverse=True)
            top_signals = buy_signals[:3]

            logger.info(f"[CRYPTO] TF={tf_key}: Found {len(buy_signals)} signals, sending TOP 3 to {len(group_users)} users")

            for uid, u in group_users:
                try:
                    for i, (ticker, name, d, s) in enumerate(top_signals):
                        if i > 0:
                            await asyncio.sleep(60)

                        # Fetch freshest data using user's TF
                        fresh_d, _ = get_crypto_data_with_fallback(ticker, interval, period)

                        if fresh_d:
                            d = fresh_d
                            entry_price = d['price']
                            atr = d.get('atr', entry_price * 0.02)
                            s['entry'] = entry_price
                            s['atr'] = atr
                            # Calculate TP/SL using user's TF
                            tpsl = calc_tPSL('BUY', entry_price, atr, tf_key)
                            s['tp1'] = tpsl['tp1']
                            s['tp2'] = tpsl['tp2']
                            s['tp3'] = tpsl['tp3']
                            s['sl'] = tpsl['sl']
                            logger.info(f"[CRYPTO] Fresh price for {ticker}: ${entry_price:,.2f}")
                        else:
                            logger.warning(f"[CRYPTO] Could not fetch fresh data for {ticker}")
                            continue

                        quality = s.get('quality', 'WEAK')
                        quality_reliability = {'STRONG': 75, 'MODERATE': 60, 'WEAK': 45, 'EARLY': 35}.get(quality, 50)

                        trend = 'NEUTRAL'
                        if s.get('macd_hist', 0) > 0 and d.get('rsi', 50) < 50:
                            trend = 'UPTREND'
                        elif s.get('macd_hist', 0) < 0 and d.get('rsi', 50) > 50:
                            trend = 'DOWNTREND'
                        elif d.get('change', 0) > 2:
                            trend = 'BREAKOUT'
                        elif d.get('change', 0) < -2:
                            trend = 'PULLBACK'

                        # Detect chart patterns
                        crypto_patterns = []
                        try:
                            from utils.patterns import detect_all_patterns
                            if d.get('candles', 0) >= 20 and 'raw_df' in d:
                                df = d.get('raw_df')
                                if df is not None:
                                    patterns = detect_all_patterns(df)
                                    if patterns.get('patterns_found', 0) > 0:
                                        strongest = patterns.get('strongest_pattern')
                                        if strongest:
                                            crypto_patterns.append({
                                                'name': strongest.get('name', ''),
                                                'strength': strongest.get('strength', 0),
                                                'description': strongest.get('description', '')
                                            })
                        except Exception as e:
                            logger.debug(f"Pattern detection failed: {e}")

                        notif_type = 'REVERSAL' if s.get('is_reversal', False) else 'BUY'

                        analysis_data = {
                            'pattern': {'type': trend, 'reliability': quality_reliability},
                            'patterns': crypto_patterns,
                            'leverage': 5 if quality == 'STRONG' else 3,
                            'indicators': {
                                'rsi': d.get('rsi', 0),
                                'macd': s.get('macd_hist', 0),
                                'atr': s.get('atr', 0),
                            },
                            'sr': d.get('sr', {}),
                            'support': d.get('support'),
                            'resistance': d.get('resistance'),
                            'is_reversal': s.get('is_reversal', False),
                            'reversal_reasons': s.get('reversal_reasons', []),
                        }

                        try:
                            msg = format_unified_crypto_notification(
                                notif_type=notif_type,
                                ticker=ticker,
                                name=name,
                                entry=s['entry'],
                                current_price=d['price'],
                                tp1=s['tp1'],
                                tp2=s['tp2'],
                                tp3=s['tp3'],
                                sl=s['sl'],
                                analysis_data=analysis_data,
                                change_pct=d.get('change', 0),
                                profit_loss=1.0,
                                usd_idr_rate=crypto_service.get_usd_idr_rate()
                            )
                            await app.bot.send_message(
                                chat_id=int(uid), text=msg, parse_mode='Markdown',
                                read_timeout=10, connect_timeout=10
                            )
                            logger.info(f"[CRYPTO] Sent BUY [{tf_key}] for {ticker} to user {uid}")

                            key = f"CRYPTO_{ticker}_{uid}"
                            signal_type = s['signal'] if s.get('signal') in ('BUY', 'REVERSAL') else 'BUY'
                            signals[key] = {
                                'name': name,
                                'entry': s['entry'],
                                'tp1': s['tp1'], 'tp2': s['tp2'], 'tp3': s['tp3'],
                                'sl': s['sl'], 'time': now_wib(),
                                'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
                                'type': 'crypto', 'direction': 'LONG', 'ticker_raw': ticker,
                                'buy_score': s.get('buy_score', 0),
                                'quality': s.get('quality', 'WEAK'),
                                'signal_type': signal_type,
                                'is_reversal': s.get('is_reversal', False),
                                'atr': s.get('atr', 0),
                                'user_id': uid,
                                'timeframe': tf_key,
                            }
                            # Persist to DB for recovery after restart
                            db.save_active_signal(
                                key=key,
                                ticker=ticker,
                                asset_type='crypto',
                                signal_type=signal_type,
                                price=s['entry'],
                                tp1=s['tp1'], tp2=s['tp2'], tp3=s['tp3'],
                                sl=s['sl'],
                                score=s.get('buy_score', 0),
                                quality=s.get('quality', 'WEAK'),
                                reason=s.get('reason', ''),
                                extra_data={
                                    'name': name,
                                    'is_reversal': s.get('is_reversal', False),
                                    'atr': s.get('atr', 0),
                                    'user_id': uid,
                                    'timeframe': tf_key,
                                }
                            )
                        except Exception as e:
                            logger.error(f"[CRYPTO] Failed to send message for {ticker}: {e}", exc_info=True)

                    logger.info(f"[CRYPTO] Sent TOP 3 [{tf_key}] signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send crypto signals to user {uid}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in check_crypto_signals: {e}", exc_info=True)


async def check_crypto_tp_sl(app):
    """Check and notify TP/SL hits for tracked crypto signals"""
    try:
        for uid, u in _get_user_db().items():
            if not u.get('notif_crypto', False):
                continue

            signals = _get_last_buy_signals()
            for key, signal_data in list(signals.items()):
                if signal_data.get('type') != 'crypto':
                    continue

                # Skip signals not owned by this user (per-user TP/SL tracking)
                if signal_data.get('user_id') != uid:
                    continue

                ticker = signal_data.get('ticker_raw')
                if not ticker:
                    continue

                try:
                    # Get user's TF for per-user TP/SL calculation
                    user_tf = u.get('timeframe', '60')
                    interval, period = CRYPTO_TF_TO_INTERVAL.get(user_tf, ('1h', '1mo'))
                    d = crypto_service.get_crypto_data_combined(ticker, interval, period)
                    if not d:
                        continue

                    current_price = d['price']
                    entry = signal_data.get('entry', 0)
                    atr = signal_data.get('atr', 0)

                    # Recalculate TP/SL based on user's TF (per-user TP/SL)
                    if entry > 0 and atr > 0:
                        tpsl = calc_tPSL('BUY', entry, atr, user_tf)
                        tp1 = tpsl['tp1']
                        tp2 = tpsl['tp2']
                        tp3 = tpsl['tp3']
                        sl = tpsl['sl']
                    else:
                        # Fallback to stored values if ATR not available
                        tp1 = signal_data.get('tp1', 0)
                        tp2 = signal_data.get('tp2', 0)
                        tp3 = signal_data.get('tp3', 0)
                        sl = signal_data.get('sl', 0)
                    tp_hit = signal_data.get('tp_hit', {'tp1': False, 'tp2': False, 'tp3': False})

                    if entry <= 0:
                        continue

                    # Build analysis_data for TP/SL notifications
                    tp_analysis = {
                        'indicators': {
                            'atr': entry * 0.02,
                        }
                    }

                    # === CHECK SL FIRST ===
                    # If SL hit, send SL notification and delete signal immediately.
                    # Skip TP checks to avoid sending TP after SL.
                    if current_price <= sl > 0:
                        profit_pct = ((current_price - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_crypto_notification(
                            notif_type='SL',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct,
                            usd_idr_rate=crypto_service.get_usd_idr_rate()
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"SL hit: {ticker} at {current_price} - Position closed")

                        db.save_signal_outcome(key, 'sl', closed_price=current_price)

                        # Remove from tracking
                        del signals[key]
                        _remove_signal(key)  # Also remove from persisted storage
                        continue  # Skip TP checks for this signal

                    # === CHECK TP (only if SL not hit) ===
                    # Check TP1 hit
                    if not tp_hit.get('tp1') and current_price >= tp1 > 0:
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp1')
                        profit_pct = ((tp1 - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_crypto_notification(
                            notif_type='TP1',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct,
                            usd_idr_rate=crypto_service.get_usd_idr_rate()
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"TP1 hit: {ticker} at {current_price}")

                    # Check TP2 hit
                    if not tp_hit.get('tp2') and current_price >= tp2 > 0:
                        tp_hit['tp2'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp2')
                        profit_pct = ((tp2 - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_crypto_notification(
                            notif_type='TP2',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct,
                            usd_idr_rate=crypto_service.get_usd_idr_rate()
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"TP2 hit: {ticker} at {current_price}")

                    # Check TP3 hit
                    if not tp_hit.get('tp3') and current_price >= tp3 > 0:
                        tp_hit['tp3'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp3', closed_price=current_price)
                        profit_pct = ((tp3 - entry) / entry) * 100

                        name = signal_data.get('name', ticker)
                        msg = format_unified_crypto_notification(
                            notif_type='TP3',
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct,
                            usd_idr_rate=crypto_service.get_usd_idr_rate()
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"TP3 hit: {ticker} at {current_price} - Position closed (target achieved)")

                        # Auto-remove from tracking after TP3 (position closed)
                        del signals[key]
                        _remove_signal(key)  # Also remove from persisted storage

                        # Trigger a fresh signal scan within ~60s so user gets a new BUY quickly
                        _schedule_followup_scan(app, 'crypto', delay=60)
                        continue  # Skip remaining checks for this signal

                except Exception as e:
                    logger.error(f"TP/SL check error for {key}: {e}", exc_info=True)
                    continue

    except Exception as e:
        logger.error(f"Error in check_crypto_tp_sl: {e}", exc_info=True)


async def check_crypto_favorit_alerts(app):
    """Check crypto favorit alerts and send notifications when target price is reached"""
    try:
        for uid, u in _get_user_db().items():
            crypto_favorit = u.get('crypto_favorit', {})
            if not crypto_favorit:
                continue

            tickers_to_remove = []

            for ticker, target_price in list(crypto_favorit.items()):
                if target_price is None:
                    continue

                try:
                    d = crypto_service.get_crypto_data_combined(ticker, '1h', '1d')
                    if not d:
                        continue

                    current_price = d['price']
                    name = crypto_service.crypto_pairs.get(ticker, ticker)
                    usd_idr = crypto_service.get_usd_idr_rate()

                    # Check if price reached target (for crypto, check if ABOVE target for SELL or BELOW for BUY)
                    # Default: alert when price >= target (good for take profit)
                    if current_price >= target_price:
                        msg = f"🎯 *TARGET TERCAPAI: {name} ({ticker})*\n\n"
                        msg += f"💰 Target: ${target_price:,.2f}\n"
                        msg += f"📈 Current: ${current_price:,.2f}\n"
                        msg += f"💱 Rate USD-IDR: Rp {usd_idr:,.0f}\n"
                        msg += f"📊 Profit: {((current_price - target_price) / target_price * 100):+.2f}%\n\n"
                        msg += "🎉 Harga sudah menyentuh target!\n"
                        msg += "Saatnya take profit atau hold?"

                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"Crypto favorit alert: {ticker} reached target {target_price}")

                        # Remove from favorit after alert
                        tickers_to_remove.append(ticker)

                except Exception as e:
                    logger.error(f"Error checking crypto favorit {ticker}: {e}", exc_info=True)
                    continue

            for t in tickers_to_remove:
                if t in crypto_favorit:
                    del crypto_favorit[t]

    except Exception as e:
        logger.error(f"Error in check_crypto_favorit_alerts: {e}", exc_info=True)


async def auto_save_data(app):
    """Auto-save user data periodically"""
    try:
        # Import here to avoid module caching issues
        from handlers.command_handlers import save_user_data
        save_user_data()
        logger.debug("User data auto-saved")
    except Exception as e:
        logger.error(f"Auto-save error: {e}", exc_info=True)


# === PREFETCH FOR FAST RESPONSE ===
# Prefetch top stocks & crypto to warm cache before user requests

async def prefetch_stock_cache(app):
    """
    Prefetch top stocks to warm cache.
    Runs every 2 minutes during market hours to ensure fast response.
    Cache key format: {ticker}:{interval}:{period} (matches stock_service.py)
    """
    try:
        now = now_wib()
        is_weekend = now.weekday() >= 5

        # Only prefetch during weekdays
        if is_weekend:
            return

        # Get top 30 most traded stocks for faster user response
        top_stocks = list(ALL_STOCKS.keys())[:30]

        def prefetch_ticker(ticker):
            """Prefetch single ticker data"""
            try:
                # Use same cache key format as stock_service.py: {ticker}:{interval}:{period}
                cache_key = f"{ticker}.JK:5m:3d"
                # Check if already cached (stock_service handles this, but double-check)
                if _price_cache.get(cache_key):
                    return None

                # Fetch fresh data - this will auto-cache via stock_service
                d = stock_service.get_stock_data_combined(ticker + ".JK", '5m', '3d')
                if d and d.get('candles', 0) >= 5:
                    return ticker
            except Exception as e:
                logger.debug(f"favorit stock probe failed for {ticker}: {e}")
            return None

        semaphore = asyncio.Semaphore(15)
        async def prefetch_with_limit(ticker):
            async with semaphore:
                return await asyncio.to_thread(prefetch_ticker, ticker)

        tasks = [prefetch_with_limit(t) for t in top_stocks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        cached_count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        if cached_count > 0:
            logger.info(f"[PREFETCH] Stock cache warmed: {cached_count}/{len(top_stocks)} stocks")

    except Exception as e:
        logger.error(f"Prefetch stock cache error: {e}", exc_info=True)


async def prefetch_crypto_cache(app):
    """
    Prefetch major crypto pairs to warm cache.
    Runs every 5 minutes (reduced due to CoinGecko rate limits).
    Cache key format: {ticker}_{interval}_{period} (matches crypto_service.py)
    """
    try:
        # Get top 10 major crypto only (reduced due to rate limits)
        major_crypto = list(crypto_service.crypto_pairs.keys())[:10]

        def prefetch_crypto(ticker):
            """Prefetch single crypto data"""
            try:
                # Check if already cached (crypto_service handles this)
                cache_key = f"{ticker}_1h_1d"
                if _price_cache.get(cache_key):
                    return None

                # Fetch fresh data - this will auto-cache via crypto_service
                d = crypto_service.get_crypto_data_combined(ticker, '1h', '1d')
                if d and d.get('candles', 0) >= 5:
                    return ticker
            except Exception as e:
                logger.debug(f"favorit crypto probe failed for {ticker}: {e}")
            return None

        semaphore = asyncio.Semaphore(3)  # Reduced from 10 to 3 to avoid rate limits
        async def prefetch_with_limit(ticker):
            async with semaphore:
                return await asyncio.to_thread(prefetch_crypto, ticker)

        tasks = [prefetch_with_limit(t) for t in major_crypto]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        cached_count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        if cached_count > 0:
            logger.info(f"[PREFETCH] Crypto cache warmed: {cached_count}/{len(major_crypto)} pairs")

    except Exception as e:
        logger.error(f"Prefetch crypto cache error: {e}", exc_info=True)


def register_jobs(app):
    """Register all background jobs to the application"""
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


async def cleanup_caches(app):
    """Periodic cache and signal cleanup"""
    try:
        _price_cache.cleanup()
        _signal_cache.cleanup()
        _market_cache.cleanup()
        _usd_cache.cleanup()
        cleanup_old_signals()  # Cleanup old signals (max 7 days)
        logger.debug("Caches and signals cleaned up")
    except Exception as e:
        logger.error(f"Cleanup error: {e}", exc_info=True)
