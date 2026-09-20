"""BSJP (Beli Sore Jual Pagi) and morning-watchlist background jobs.

Both jobs share the stock-data fetch helpers and the sent-notification markers;
they run at different times of day:
- ``check_bsjp_signals``        — 14:00-16:00 WIB (afternoon scan before close)
- ``check_morning_notification`` — 07:15-08:00 WIB (pre-market scan)
"""
import asyncio
import logging

from utils.cache import _price_cache

from handlers.scheduler._common import (
    now_wib,
    get_stock_data_with_fallback,
    _send_bot_with_retry,
    _get_user_db,
)
from handlers.scheduler._markers import (
    _check_notification_sent_today,
    _mark_notification_sent_today,
    _check_morning_sent_today,
    _mark_morning_sent,
)
from handlers.scheduler._state import ALL_STOCKS

logger = logging.getLogger(__name__)


async def check_bsjp_signals(app):
    """Check BSJP (Beli Sore Jual Pagi) signals and send notifications."""
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
            """Blocking BSJP analysis - runs in thread pool."""
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


async def check_morning_notification(app):
    """Send morning signals between 07:15-08:00 WIB."""
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
            """Blocking stock analysis - runs in thread pool."""
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
