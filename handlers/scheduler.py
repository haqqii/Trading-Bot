"""
Background job schedulers for the Telegram bot.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from services.stock_service import stock_service
from services.crypto_service import crypto_service
from services.signal_service import signal_service
from utils.formatters import format_unified_crypto_notification, format_unified_stock_notification

# NOTE: Import save_user_data inside functions to avoid module caching issues

# Import caches for cleanup
from utils.cache import _price_cache, _signal_cache, _market_cache, _usd_cache

# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))


def now_wib():
    """Get current time in WIB timezone (UTC+7)

    Uses local system time directly since user's timezone is set to UTC+7.
    """
    # Get local time and treat it as WIB (system timezone is UTC+7)
    return datetime.now(WIB)


def get_stock_data_with_fallback(ticker: str, interval: str = '5m', period: str = '3d'):
    """
    Get stock data with stale cache fallback.
    Returns (data, is_stale) tuple.
    """
    cache_key = f"stock_{ticker}_{interval}_{period}"

    # Check cache first before API call
    cached = _price_cache.get(cache_key)
    if cached and cached.get('candles', 0) >= 5:
        return cached, False

    # Try fresh data
    d = stock_service.get_stock_data_combined(ticker, interval, period)
    if d and d.get('candles', 0) >= 5:
        _price_cache.set(cache_key, d, ttl=180)  # 3 min cache
        return d, False

    # Try stale cache
    stale_d = _price_cache.get_stale(cache_key)
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


def _get_user_db():
    """Get user database - reads directly from command_handlers to ensure fresh data"""
    import handlers.command_handlers as ch
    return ch.user_data_db


def _get_last_buy_signals():
    """Get last buy signals - reads directly from command_handlers"""
    import handlers.command_handlers as ch
    return ch.last_buy_signals

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
            except:
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
            except:
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
                    logger.error(f"Error checking favorit {ticker}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Error in check_favorit_alerts: {e}")


async def check_bsjp_signals(app):
    """Check BSJP (Beli Sore Jual Pagi) signals and send notifications"""
    try:
        now = now_wib()
        is_weekend = now.weekday() >= 5

        # Widen window: 15:00 - 15:05 WIB (5 minute window to avoid misfires)
        is_bsjp_time = (now.hour == 15 and now.minute <= 5)

        if is_weekend:
            logger.info("[BSJP] Weekend - skipping")
            return

        # Only send BSJP at 15:00-15:05
        if not is_bsjp_time:
            logger.info(f"[BSJP] Outside BSJP time ({now.hour}:{now.minute:02d} WIB) - skipping")
            return

        # Check if already sent today (file-based)
        if _check_sent_today(BSJP_SENT_FILE):
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
        tickers = list(ALL_STOCKS.keys())[:100]

        def analyze_bsjp(ticker):
            """Blocking BSJP analysis - runs in thread pool"""
            try:
                d, _ = get_stock_data_with_fallback(ticker + ".JK", '1h', '3d')
                if not d or d.get('candles', 0) < 10:
                    return None

                price = d['price']
                rsi = d.get('rsi', 50)
                ma_fast = d.get('ma_fast', price)
                ma_slow = d.get('ma_slow', price)
                change = d.get('change', 0)

                # BSJP criteria:
                # 1. Price above MAs (bullish)
                # 2. RSI not overbought (RSI < 70 for flexibility)
                # 3. Positive change
                above_ma = price > ma_fast > ma_slow
                rsi_ok = 30 < rsi < 70

                if above_ma and rsi_ok:
                    score = 2
                    reasons = ["Above MA", f"RSI {rsi:.0f}"]
                    if change > 0:
                        score += 1
                        reasons.append(f"+{change:.1f}%")

                    return {
                        'ticker': ticker,
                        'name': ALL_STOCKS.get(ticker, ticker),
                        'price': price,
                        'rsi': rsi,
                        'change': change,
                        'score': score,
                        'reasons': ', '.join(reasons),
                        'tp': price * 1.02,
                        'sl': price * 0.985
                    }
            except:
                pass
            return None

        semaphore = asyncio.Semaphore(20)
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

                    await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                    logger.info(f"[BSJP] Sent {len(bsjp_signals)} signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send BSJP to user {uid}: {e}")

            # Mark as sent today AFTER all users processed
            _mark_sent_today(BSJP_SENT_FILE)
            logger.info("[BSJP] Marked as sent for today")

        # Mark as sent even if no signals found (to prevent re-scanning)
        if not bsjp_signals:
            _mark_sent_today(BSJP_SENT_FILE)
            logger.info("[BSJP] No signals found, marked as sent for today")

        logger.info(f"[BSJP] Scan complete: {len(bsjp_signals)} signals")

    except Exception as e:
        logger.error(f"Error in check_bsjp_signals: {e}")


async def check_stock_signals(app):
    """Check stock signals during market hours and send BUY notifications to all users with notif_saham=ON"""
    try:
        now = now_wib()

        # Debug: Log market hours status
        is_weekend = now.weekday() >= 5
        is_market_hours = 8 <= now.hour < 16

        if is_weekend:
            logger.info("Weekend - skipping stock signals")
            return

        if not is_market_hours:
            logger.info(f"Outside market hours ({now.hour}:00 WIB) - skipping stock signals")
            return

        # Get fresh user data directly from command_handlers
        user_db = _get_user_db()
        logger.info(f"[DEBUG] user_data_db has {len(user_db)} users")
        for uid, u in user_db.items():
            logger.info(f"[DEBUG] User {uid}: notif_saham={u.get('notif_saham')}")

        # Check if any user has notif_saham enabled
        stock_users = [uid for uid, u in user_db.items() if u.get('notif_saham', False)]

        if not stock_users:
            logger.info("[STOCK] No users with notif_saham enabled")
            return

        logger.info(f"[STOCK SIGNALS] Scanning stocks for {len(stock_users)} users...")

        # Limit scan to top 50 most liquid stocks for faster scanning
        # This ensures scan completes within 5 minutes
        all_tickers = list(ALL_STOCKS.keys())[:50]
        logger.info(f"[STOCK] Scanning top {len(all_tickers)} stocks (limited from {len(ALL_STOCKS)})")

        signals_found = 0
        buy_signals = []  # Collect all BUY signals first

        def analyze_stock(ticker):
            """Blocking stock analysis - runs in thread pool"""
            try:
                # Skip crypto tickers
                if ticker in crypto_service.crypto_pairs:
                    return None

                d, _ = get_stock_data_with_fallback(ticker + ".JK", '5m', '3d')
                if not d or d.get('candles', 0) < 5:
                    return None

                s = signal_service.generate_stock_signal(d)
                if not s.get('entry') or s.get('entry', 0) <= 0:
                    return None

                current_price = d['price']
                key = f"STOCK_{ticker}"

                # Include REVERSAL signals
                is_buy_or_reversal = s['signal'] in ('BUY', 'REVERSAL')

                if is_buy_or_reversal and s.get('buy_score', 0) >= 25:
                    signals = _get_last_buy_signals()
                    existing = signals.get(key)
                    should_send = False

                    if existing is None:
                        should_send = True
                    else:
                        time_diff = (now - existing.get('time', now)).total_seconds()
                        if time_diff > 21600:  # 6 hours
                            last_entry = existing.get('entry', 0)
                            if last_entry > 0:
                                price_change = abs(current_price - last_entry) / last_entry
                                if price_change > 0.02:
                                    should_send = True

                    if should_send:
                        signal_type = 'REVERSAL' if s['signal'] == 'REVERSAL' else 'BUY'
                        signals[key] = {
                            'name': ALL_STOCKS.get(ticker, ticker),
                            'entry': s['entry'],
                            'tp1': s['tp1'], 'tp2': s['tp2'], 'tp3': s['tp3'],
                            'sl': s['sl'], 'time': now,
                            'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
                            'type': 'stock', 'direction': 'LONG', 'ticker_raw': ticker,
                            'buy_score': s.get('buy_score', 0),
                            'quality': s.get('quality', 'WEAK'),
                            'signal_type': signal_type,
                            'is_reversal': s.get('is_reversal', False)
                        }
                        logger.info(f"✅ {signal_type} Signal: {ticker} - {ALL_STOCKS.get(ticker, ticker)} @ Rp{s['entry']:,.0f} (Score: {s.get('buy_score', 0)})")
                        return (ticker, ALL_STOCKS.get(ticker, ticker), d, s)

                return None
            except Exception as e:
                return None

        semaphore = asyncio.Semaphore(25)  # Max 25 concurrent
        async def fetch_with_semaphore(ticker):
            try:
                async with semaphore:
                    return await asyncio.wait_for(
                        asyncio.to_thread(analyze_stock, ticker),
                        timeout=30.0  # 30 second timeout per stock
                    )
            except asyncio.TimeoutError:
                logger.warning(f"[STOCK] Timeout for {ticker}")
                return None
            except Exception as e:
                logger.error(f"[STOCK] Error fetching {ticker}: {e}")
                return None

        tasks = [fetch_with_semaphore(t) for t in all_tickers]
        logger.info(f"[STOCK] Starting scan of {len(tasks)} stocks...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[STOCK] Scan complete, processing results...")

        # Handle results, filtering out exceptions and None values
        buy_signals = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"[STOCK] Task exception: {r}")
            elif r is not None:
                buy_signals.append(r)

        signals_found = len(buy_signals)

        # Send notifications to all users with notif_saham=ON
        if buy_signals:
            # Sort by score
            buy_signals.sort(key=lambda x: x[3].get('buy_score', 0), reverse=True)
            top_signals = buy_signals[:3]  # Max 3 signals

            logger.info(f"[STOCK] Found {len(buy_signals)} BUY signals, sending TOP 3 to {len(stock_users)} users")

            for uid in stock_users:
                try:
                    # Send each signal with 60 second delay
                    for i, (ticker, name, d, s) in enumerate(top_signals):
                        if i > 0:
                            await asyncio.sleep(60)  # Delay 60 detik antar notifikasi

                        # Fetch fresh data for current price (avoid stale data)
                        fresh_d, _ = get_stock_data_with_fallback(ticker + ".JK", '5m', '1d')
                        if fresh_d:
                            d = fresh_d
                            # Recalculate entry based on current price
                            entry_price = d['price']
                            atr = d.get('atr', entry_price * 0.015)
                            # Entry range: current price ± 0.5% spread
                            entry_low = entry_price * 0.995
                            entry_high = entry_price * 1.005
                            s['entry'] = entry_price
                            s['entry_low'] = entry_low
                            s['entry_high'] = entry_high
                            s['tp1'] = entry_price + (1 * atr)
                            s['tp2'] = entry_price + (2 * atr)
                            s['tp3'] = entry_price + (3 * atr)
                            s['sl'] = entry_price - (2 * atr)
                            s['rsi'] = d.get('rsi', 50)

                        quality = s.get('quality', 'WEAK')
                        quality_reliability = {'STRONG': 75, 'MODERATE': 60, 'WEAK': 45}.get(quality, 50)

                        # Determine trend from indicators
                        trend = 'NEUTRAL'
                        if s.get('macd_hist', 0) > 0 and d.get('rsi', 50) < 50:
                            trend = 'UPTREND'
                        elif s.get('macd_hist', 0) < 0 and d.get('rsi', 50) > 50:
                            trend = 'DOWNTREND'
                        elif d.get('change', 0) > 2:
                            trend = 'BREAKOUT'
                        elif d.get('change', 0) < -2:
                            trend = 'PULLBACK'

                        # Build reasons list
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

                        # Detect chart patterns
                        try:
                            from utils.patterns import detect_all_patterns
                            import pandas as pd

                            # Create mock df from data for pattern detection
                            if d.get('candles', 0) >= 20:
                                # Get raw data if available
                                if 'raw_df' in d:
                                    df = d['raw_df']
                                    patterns = detect_all_patterns(df)
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
                            'pattern': {
                                'type': trend,
                                'reliability': quality_reliability
                            },
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
                            # Support & Resistance
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
                            logger.info(f"[STOCK] Sent BUY signal for {ticker} to user {uid}")
                        except Exception as e:
                            logger.error(f"[STOCK] Failed to send message for {ticker}: {e}")

                    logger.info(f"[STOCK] Sent TOP 3 signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send signals to user {uid}: {e}")

        if signals_found > 0:
            logger.info(f"✅ Stock scan complete: {signals_found} BUY signals found")
        else:
            logger.info(f"ℹ️ Stock scan complete: No BUY signals found (buy_score < 40 or market bearish)")

    except Exception as e:
        logger.error(f"Error in check_stock_signals: {e}")


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

                ticker = signal_data.get('ticker_raw')
                if not ticker:
                    continue

                try:
                    d = stock_service.get_stock_data_combined(ticker + ".JK", '5m', '1d')
                    if not d:
                        continue

                    current_price = d['price']
                    entry = signal_data.get('entry', 0)
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
                        logger.info(f"SL hit: {ticker} at {current_price}")

                        del signals[key]
                        continue  # Skip TP checks for this signal

                    # === CHECK TP (only if SL not hit) ===
                    # Check TP1 hit
                    if not tp_hit.get('tp1') and current_price >= tp1 > 0:
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
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
                        logger.info(f"TP3 hit: {ticker} at {current_price}")

                except Exception as e:
                    logger.error(f"TP/SL check error for {key}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Error in check_stock_tp_sl: {e}")


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
                    logger.error(f"Alert error for {ticker}: {e}")

            for t in tickers_to_remove:
                if t in alerts:
                    del alerts[t]

    except Exception as e:
        logger.error(f"Check alerts error: {e}")


import os

MORNING_SENT_FILE = 'morning_sent.txt'
BSJP_SENT_FILE = 'bsjp_sent.txt'


def _check_sent_today(filepath: str) -> bool:
    """Check if notification was already sent today (file-based)"""
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                last_sent = f.read().strip()
            today = now_wib().date().isoformat()
            return last_sent == today
    except:
        pass
    return False


def _mark_sent_today(filepath: str):
    """Mark notification as sent today"""
    try:
        with open(filepath, 'w') as f:
            f.write(now_wib().date().isoformat())
    except:
        pass


# Backward compatibility wrappers
def _check_morning_sent_today():
    return _check_sent_today(MORNING_SENT_FILE)


def _mark_morning_sent():
    _mark_sent_today(MORNING_SENT_FILE)


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
            return

        # Check if any user has notif_morning enabled
        morning_users = [uid for uid, u in _get_user_db().items() if u.get('notif_morning', False)]

        if not morning_users:
            logger.debug("[MORNING] No users with notif_morning enabled")
            return

        logger.info(f"[MORNING] Scanning for {len(morning_users)} users...")

        # Scan stocks for morning signals (parallel fetch)
        morning_signals = []
        tickers = list(ALL_STOCKS.keys())[:100]

        def analyze_stock(ticker):
            """Blocking stock analysis - runs in thread pool"""
            try:
                d, _ = get_stock_data_with_fallback(ticker + ".JK", '1h', '3d')
                if not d or d.get('candles', 0) < 10:
                    return None

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
            except Exception:
                pass
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
                    logger.error(f"Failed to send morning to user {uid}: {e}")

            # Mark as sent today AFTER all users processed
            _mark_morning_sent()
            logger.info("[MORNING] Marked as sent for today")

        logger.info(f"[MORNING] Scan complete: {len(morning_signals)} signals")

    except Exception as e:
        logger.error(f"Error in check_morning_notification: {e}")


async def check_crypto_signals(app):
    """Check crypto signals 24/7 and send notifications to all users with notif_crypto=ON"""
    try:
        now = now_wib()

        # Check if any user has notif_crypto enabled
        crypto_users = [uid for uid, u in _get_user_db().items() if u.get('notif_crypto', False)]

        if not crypto_users:
            logger.info("[CRYPTO] No users with notif_crypto enabled")
            return

        logger.info(f"[CRYPTO SIGNALS] Scanning ALL crypto pairs for {len(crypto_users)} users...")

        # Get all crypto pairs to scan
        all_crypto = list(crypto_service.crypto_pairs.keys())
        logger.info(f"[CRYPTO] Total pairs to scan: {len(all_crypto)}")

        signals_found = 0
        errors_count = 0
        buy_signals = []  # Collect all BUY signals first

        def analyze_crypto(ticker):
            """Blocking crypto analysis - runs in thread pool"""
            try:
                # Small delay to avoid rate limiting
                import time
                time.sleep(0.3)

                # Use fallback with stale cache
                d, is_stale = get_crypto_data_with_fallback(ticker, '1h', '1d')
                if not d or d.get('candles', 0) < 5:
                    return None

                s = signal_service.generate_crypto_signal(d)
                if not s.get('entry') or s['entry'] <= 0:
                    return None

                current_price = d['price']
                key = f"CRYPTO_{ticker}"

                # Check if we should send this signal
                should_send = False

                # Include REVERSAL signals
                is_buy_or_reversal = s['signal'] in ('BUY', 'REVERSAL')

                if is_buy_or_reversal and s.get('buy_score', 0) >= 25:
                    signals = _get_last_buy_signals()
                    existing = signals.get(key)
                    if existing is None:
                        should_send = True
                    else:
                        time_diff = (now - existing.get('time', now)).total_seconds()
                        if time_diff > 21600:  # 6 hours
                            last_entry = existing.get('entry', 0)
                            if last_entry > 0:
                                price_change = abs(current_price - last_entry) / last_entry
                                if price_change > 0.02:
                                    should_send = True

                    if should_send:
                        return (ticker, crypto_service.crypto_pairs.get(ticker, ticker), d, s, s.get('buy_score', 0))

                return None

            except Exception as e:
                return None

        # Parallel crypto scanning with thread pool
        semaphore = asyncio.Semaphore(20)
        async def fetch_crypto(ticker):
            async with semaphore:
                return await asyncio.to_thread(analyze_crypto, ticker)

        tasks = [fetch_crypto(t) for t in all_crypto]
        results = await asyncio.gather(*tasks)

        # Get fresh signals reference for writing
        signals = _get_last_buy_signals()
        for r in results:
            if r is not None:
                ticker, name, d, s, score = r
                key = f"CRYPTO_{ticker}"
                signals_found += 1

                # Determine signal type
                signal_type = s['signal'] if s['signal'] in ('BUY', 'REVERSAL') else 'BUY'

                signals[key] = {
                    'name': name,
                    'entry': s['entry'],
                    'tp1': s['tp1'], 'tp2': s['tp2'], 'tp3': s['tp3'],
                    'sl': s['sl'], 'time': now,
                    'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
                    'type': 'crypto', 'direction': 'LONG', 'ticker_raw': ticker,
                    'buy_score': s.get('buy_score', 0),
                    'quality': s.get('quality', 'WEAK'),
                    'signal_type': signal_type,
                    'is_reversal': s.get('is_reversal', False)
                }
                buy_signals.append((ticker, name, d, s))
                logger.info(f"✅ CRYPTO {signal_type} Signal: {ticker} - {name} @ ${s['entry']:,.2f} (Score: {s.get('buy_score', 0)})")

        # Send notifications to all users with notif_crypto=ON
        if buy_signals:
            # Sort by score
            buy_signals.sort(key=lambda x: x[3].get('buy_score', 0), reverse=True)
            top_signals = buy_signals[:3]  # Max 3 signals

            logger.info(f"[CRYPTO] Found {len(buy_signals)} BUY signals, sending TOP 3 to {len(crypto_users)} users")

            for uid in crypto_users:
                try:
                    # Send each signal with 60 second delay
                    for i, (ticker, name, d, s) in enumerate(top_signals):
                        if i > 0:
                            await asyncio.sleep(60)  # Delay 60 detik antar notifikasi

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

                        # Determine notification type (BUY vs REVERSAL)
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
                            # Support & Resistance
                            'sr': d.get('sr', {}),
                            'support': d.get('support'),
                            'resistance': d.get('resistance'),
                            # REVERSAL signal info
                            'is_reversal': s.get('is_reversal', False),
                            'reversal_reasons': s.get('reversal_reasons', []),
                        }

                        # Send notification
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
                            logger.info(f"[CRYPTO] Sent BUY signal for {ticker} to user {uid}")
                        except Exception as e:
                            logger.error(f"[CRYPTO] Failed to send message for {ticker}: {e}")

                    logger.info(f"[CRYPTO] Sent TOP 3 signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send crypto signals to user {uid}: {e}")

        if signals_found > 0:
            logger.info(f"✅ Crypto scan complete: {signals_found} BUY signals found")
        else:
            logger.info(f"ℹ️ Crypto scan complete: No BUY signals found (market may be bearish)")

    except Exception as e:
        logger.error(f"Error in check_crypto_signals: {e}")


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

                ticker = signal_data.get('ticker_raw')
                if not ticker:
                    continue

                try:
                    d = crypto_service.get_crypto_data_combined(ticker, '1h', '1d')
                    if not d:
                        continue

                    current_price = d['price']
                    entry = signal_data.get('entry', 0)
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
                        logger.info(f"SL hit: {ticker} at {current_price}")

                        # Remove from tracking
                        del signals[key]
                        continue  # Skip TP checks for this signal

                    # === CHECK TP (only if SL not hit) ===
                    # Check TP1 hit
                    if not tp_hit.get('tp1') and current_price >= tp1 > 0:
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
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
                        logger.info(f"TP3 hit: {ticker} at {current_price}")

                except Exception as e:
                    logger.error(f"TP/SL check error for {key}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Error in check_crypto_tp_sl: {e}")


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
                    logger.error(f"Error checking crypto favorit {ticker}: {e}")
                    continue

            for t in tickers_to_remove:
                if t in crypto_favorit:
                    del crypto_favorit[t]

    except Exception as e:
        logger.error(f"Error in check_crypto_favorit_alerts: {e}")


async def auto_save_data(app):
    """Auto-save user data periodically"""
    try:
        # Import here to avoid module caching issues
        from handlers.command_handlers import save_user_data
        save_user_data()
        logger.debug("User data auto-saved")
    except Exception as e:
        logger.error(f"Auto-save error: {e}")


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
            except:
                pass
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
        logger.error(f"Prefetch stock cache error: {e}")


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
            except:
                pass
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
        logger.error(f"Prefetch crypto cache error: {e}")


def register_jobs(app):
    """Register all background jobs to the application"""
    # === PREFETCH JOBS (run first to warm cache) ===
    # Prefetch stock cache every 2 minutes during market hours
    app.job_queue.run_repeating(prefetch_stock_cache, interval=120, first=5)

    # Prefetch crypto cache every 5 minutes (reduced due to CoinGecko rate limits)
    app.job_queue.run_repeating(prefetch_crypto_cache, interval=300, first=10)

    # Favorit alerts check every 2 minutes
    app.job_queue.run_repeating(check_favorit_alerts, interval=120, first=30)

    # Alerts check every minute
    app.job_queue.run_repeating(check_alerts, interval=60, first=60)

    # Morning notification check every minute (07:15-08:00)
    app.job_queue.run_repeating(check_morning_notification, interval=60, first=15)

    # BSJP check every minute (14:00-16:00)
    app.job_queue.run_repeating(check_bsjp_signals, interval=60, first=30)

    # Stock signals check every 5 minutes (market hours only)
    app.job_queue.run_repeating(check_stock_signals, interval=300, first=90)

    # Stock TP/SL tracking check every 2 minutes
    app.job_queue.run_repeating(check_stock_tp_sl, interval=120, first=60)

    # Crypto signals check every 5 minutes
    app.job_queue.run_repeating(check_crypto_signals, interval=300, first=90)

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
        logger.error(f"Cleanup error: {e}")
