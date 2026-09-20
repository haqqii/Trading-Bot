"""Stock-family background jobs.

Owns:
- ``reset_stock_circuit_breaker`` — daily reset at market open
- ``check_stock_signals``       — per-TF BUY signal generation and notification
- ``check_stock_tp_sl``        — TP/SL hit tracking and notifications
- ``check_favorit_alerts``     — favorite-stock target-price alerts
- ``check_alerts``             — generic price alerts (user-defined BUY/SELL targets)
- ``prefetch_stock_cache``     — cache warming for top stocks

Local module state:
- ``_yahoo_stock_cooldown``    — number of scan cycles to skip after a timeout storm
- ``_stock_timeout_count``     — per-scan timeout counter (list for closure mutability)
"""
import asyncio
import logging

from services.stock_service import stock_service
from services.signal_service import calc_tPSL
from services.crypto_service import crypto_service
from utils.formatters import format_unified_stock_notification
from db import db

from handlers.scheduler._common import (
    now_wib,
    TF_TO_INTERVAL,
    _safe_time_diff,
    _unwrap_stock_result,
    get_stock_data_with_fallback,
    _get_user_db,
    _get_last_buy_signals,
    _remove_signal,
    _schedule_followup_scan,
)
from handlers.scheduler._state import ALL_STOCKS
from utils.cache import _price_cache
from utils.rate_limiter import _circuit_breakers, APIState

logger = logging.getLogger(__name__)

# Yahoo rate-limit cooldown - skip stock scans when Yahoo is overloaded
# This prevents wasting time on scans that will just timeout
_yahoo_stock_cooldown = 0  # Number of scan cycles to skip
_stock_timeout_count = [0]  # Timeout counter for this scan (list for mutability)


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


async def check_favorit_alerts(app):
    """Check favorit stocks and send alerts when target price is reached."""
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
                    result = stock_service.get_stock_data_combined(ticker + ".JK", '5m', '1d')
                    d = _unwrap_stock_result(result)
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
                        from services.signal_service import signal_service
                        s = signal_service.generate_stock_signal(d_with_tf)
                        if not s.get('entry') or s.get('entry', 0) <= 0:
                            continue

                        # Accept BUY, REVERSAL, and SELL signals
                        if s['signal'] not in ('BUY', 'REVERSAL', 'SELL'):
                            continue
                        # Score threshold uses the matching direction
                        score = s.get('sell_score', 0) if s['signal'] == 'SELL' else s.get('buy_score', 0)
                        if score >= 25:
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
                logger.info(f"[STOCK] TF={tf_key}: No actionable signals found")
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
                # Key includes direction so BUY and SELL don't dedupe each other
                dir_key = 'SELL' if s.get('signal') == 'SELL' else 'BUY'
                key = f"STOCK_{ticker}_{tf_key}_{dir_key}"
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

            # Sort by score and take top 3 — pick the matching score by direction
            def _signal_score_key(sig_tuple):
                sig = sig_tuple[3]
                return sig.get('sell_score' if sig.get('signal') == 'SELL' else 'buy_score', 0)
            fresh_signals.sort(key=_signal_score_key, reverse=True)
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
                        # Calculate TP/SL using user's TF — direction-aware for SELL signals
                        direction = 'SELL' if s['signal'] == 'SELL' else 'BUY'
                        tpsl = calc_tPSL(direction, entry_price, atr, tf_key)
                        s['tp1'] = tpsl['tp1']
                        s['tp2'] = tpsl['tp2']
                        s['tp3'] = tpsl['tp3']
                        s['sl'] = tpsl['sl']
                        logger.info(f"[STOCK] Using cached data for {ticker}: {entry_price:,.0f} ({direction})")

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
                                notif_type=direction,
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
                            logger.info(f"[STOCK] Sent {direction} [{tf_key}] for {ticker} to user {uid}")

                            # Store signal for TP/SL tracking
                            key = f"STOCK_{ticker}_{uid}"
                            signal_type = s['signal']
                            signals[key] = {
                                'name': name,
                                'entry': s['entry'],
                                'tp1': s['tp1'], 'tp2': s['tp2'], 'tp3': s['tp3'],
                                'sl': s['sl'], 'time': now_wib(),
                                'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
                                'type': 'stock',
                                'direction': 'SHORT' if direction == 'SELL' else 'LONG',
                                'ticker_raw': ticker,
                                'buy_score': s.get('buy_score', 0),
                                'sell_score': s.get('sell_score', 0),
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
                                score=s.get('sell_score' if direction == 'SELL' else 'buy_score', 0),
                                quality=s.get('quality', 'WEAK'),
                                reason=s.get('reason', ''),
                                extra_data={
                                    'name': name,
                                    'is_reversal': s.get('is_reversal', False),
                                    'direction': 'SHORT' if direction == 'SELL' else 'LONG',
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
    """Check and notify TP/SL hits for tracked stock signals."""
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
                    result = stock_service.get_stock_data_combined(ticker + ".JK", interval, period)
                    d = _unwrap_stock_result(result)
                    if not d:
                        continue

                    current_price = d['price']
                    entry = signal_data.get('entry', 0)
                    atr = signal_data.get('atr', 0)
                    is_short = signal_data.get('direction', 'LONG') == 'SHORT'

                    # Recalculate TP/SL based on user's TF (per-user TP/SL)
                    if entry > 0 and atr > 0:
                        tpsl = calc_tPSL('SELL' if is_short else 'BUY', entry, atr, user_tf)
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

                    # Direction-aware comparison functions.
                    # LONG: profit when price rises; SL hit when price drops below sl.
                    # SHORT: profit when price drops; SL hit when price rises above sl.
                    def _is_tp_hit(target):
                        if not target:
                            return False
                        return current_price <= target if is_short else current_price >= target

                    def _profit_pct(price_at):
                        return ((entry - price_at) / entry) * 100 if is_short else ((price_at - entry) / entry) * 100

                    sl_hit = (sl > 0) and (current_price >= sl if is_short else current_price <= sl)

                    # === CHECK SL FIRST ===
                    # If SL hit, send SL notification and delete signal immediately.
                    # Skip TP checks to avoid sending TP after SL.
                    if sl_hit:
                        profit_pct = _profit_pct(current_price)

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
                    if not tp_hit.get('tp1') and _is_tp_hit(tp1):
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp1')
                        profit_pct = _profit_pct(tp1)

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
                    if not tp_hit.get('tp2') and _is_tp_hit(tp2):
                        tp_hit['tp2'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp2')
                        profit_pct = _profit_pct(tp2)

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
                    if not tp_hit.get('tp3') and _is_tp_hit(tp3):
                        tp_hit['tp3'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp3', closed_price=current_price)
                        profit_pct = _profit_pct(tp3)

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

                        # Trigger a fresh signal scan within ~60s for fresh entry
                        _schedule_followup_scan(app, 'stock', delay=60)
                        continue  # Skip remaining checks for this signal

                except Exception as e:
                    logger.error(f"TP/SL check error for {key}: {e}", exc_info=True)
                    continue

    except Exception as e:
        logger.error(f"Error in check_stock_tp_sl: {e}", exc_info=True)


async def check_alerts(app):
    """Check price alerts and notify users."""
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
                    result = stock_service.get_stock_data_combined(ticker + ".JK", '1m', '1d')
                    d = _unwrap_stock_result(result)
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


async def prefetch_stock_cache(app):
    """Prefetch top stocks to warm cache.

    Runs every 2 minutes during market hours to ensure fast response.
    Cache key format: ``{ticker}:{interval}:{period}`` (matches ``stock_service.py``).
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
            """Prefetch single ticker data."""
            try:
                # Use same cache key format as stock_service.py: {ticker}:{interval}:{period}
                cache_key = f"{ticker}.JK:5m:3d"
                # Check if already cached (stock_service handles this, but double-check)
                if _price_cache.get(cache_key):
                    return None

                # Fetch fresh data - this will auto-cache via stock_service
                result = stock_service.get_stock_data_combined(ticker + ".JK", '5m', '3d')
                d = _unwrap_stock_result(result)
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
