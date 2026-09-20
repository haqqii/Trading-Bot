"""Crypto-family background jobs.

Owns:
- ``check_crypto_signals``        — 24/7 per-TF BUY signal generation
- ``check_crypto_tp_sl``           — TP/SL hit tracking and notifications
- ``check_crypto_favorit_alerts``  — favorite-crypto target-price alerts
- ``prefetch_crypto_cache``        — cache warming for major crypto pairs
"""
import asyncio
import logging

from services.crypto_service import crypto_service
from services.signal_service import calc_tPSL, signal_service
from utils.formatters import format_unified_crypto_notification
from db import db
from utils.cache import _price_cache

from handlers.scheduler._common import (
    now_wib,
    CRYPTO_TF_TO_INTERVAL,
    _safe_time_diff,
    get_crypto_data_with_fallback,
    _get_user_db,
    _get_last_buy_signals,
    _remove_signal,
    _schedule_followup_scan,
)

logger = logging.getLogger(__name__)


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
                """Blocking crypto analysis for specific TF."""
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
                    # Include REVERSAL and SELL signals
                    if s['signal'] not in ('BUY', 'REVERSAL', 'SELL'):
                        return None
                    dir_key = 'SELL' if s['signal'] == 'SELL' else 'BUY'
                    key = f"CRYPTO_{ticker}_{tf_key}_{dir_key}"
                    score = s.get('sell_score', 0) if s['signal'] == 'SELL' else s.get('buy_score', 0)

                    if score >= 25:
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
                            # Calculate TP/SL using user's TF — direction-aware for SELL
                            direction = 'SELL' if s['signal'] == 'SELL' else 'BUY'
                            tpsl = calc_tPSL(direction, entry_price, atr, tf_key)
                            s['tp1'] = tpsl['tp1']
                            s['tp2'] = tpsl['tp2']
                            s['tp3'] = tpsl['tp3']
                            s['sl'] = tpsl['sl']
                            logger.info(f"[CRYPTO] Fresh price for {ticker}: ${entry_price:,.2f} ({direction})")
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

                        notif_type = direction if direction == 'SELL' else ('REVERSAL' if s.get('is_reversal', False) else 'BUY')

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
                            logger.info(f"[CRYPTO] Sent {notif_type} [{tf_key}] for {ticker} to user {uid}")

                            key = f"CRYPTO_{ticker}_{uid}"
                            signal_type = s['signal']
                            signals[key] = {
                                'name': name,
                                'entry': s['entry'],
                                'tp1': s['tp1'], 'tp2': s['tp2'], 'tp3': s['tp3'],
                                'sl': s['sl'], 'time': now_wib(),
                                'tp_hit': {'tp1': False, 'tp2': False, 'tp3': False},
                                'type': 'crypto',
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
                                asset_type='crypto',
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
                            logger.error(f"[CRYPTO] Failed to send message for {ticker}: {e}", exc_info=True)

                    logger.info(f"[CRYPTO] Sent TOP 3 [{tf_key}] signals to user {uid}")

                except Exception as e:
                    logger.error(f"Failed to send crypto signals to user {uid}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error in check_crypto_signals: {e}", exc_info=True)


async def check_crypto_tp_sl(app):
    """Check and notify TP/SL hits for tracked crypto signals."""
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

                    # Build analysis_data for TP/SL notifications
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
                    if not tp_hit.get('tp1') and _is_tp_hit(tp1):
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp1')
                        profit_pct = _profit_pct(tp1)

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
                    if not tp_hit.get('tp2') and _is_tp_hit(tp2):
                        tp_hit['tp2'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp2')
                        profit_pct = _profit_pct(tp2)

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
                    if not tp_hit.get('tp3') and _is_tp_hit(tp3):
                        tp_hit['tp3'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp3', closed_price=current_price)
                        profit_pct = _profit_pct(tp3)

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

                        # Trigger a fresh signal scan within ~60s for fresh entry
                        _schedule_followup_scan(app, 'crypto', delay=60)
                        continue  # Skip remaining checks for this signal

                except Exception as e:
                    logger.error(f"TP/SL check error for {key}: {e}", exc_info=True)
                    continue

    except Exception as e:
        logger.error(f"Error in check_crypto_tp_sl: {e}", exc_info=True)


async def check_crypto_favorit_alerts(app):
    """Check crypto favorit alerts and send notifications when target price is reached."""
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


async def prefetch_crypto_cache(app):
    """Prefetch major crypto pairs to warm cache.

    Runs every 5 minutes (reduced due to CoinGecko rate limits).
    Cache key format: ``{ticker}_{interval}_{period}`` (matches ``crypto_service.py``).
    """
    try:
        # Get top 10 major crypto only (reduced due to rate limits)
        major_crypto = list(crypto_service.crypto_pairs.keys())[:10]

        def prefetch_crypto(ticker):
            """Prefetch single crypto data."""
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
