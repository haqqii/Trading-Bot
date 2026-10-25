"""Crypto-family background jobs.

Owns:
- ``check_crypto_signals``        — 24/7 per-TF BUY signal generation
- ``check_crypto_tp_sl``           — TP/SL hit tracking and notifications
- ``check_crypto_favorit_alerts``  — favorite-crypto target-price alerts
- ``prefetch_crypto_cache``        — cache warming for major crypto pairs
"""
import asyncio
import logging
import math
import time

from services.crypto_service import crypto_service, is_stablecoin
from services.signal_service import calc_tPSL, signal_service
from services import crypto_yahoo_missing
from utils.formatters import format_unified_crypto_notification, escape_md
from db import db
from utils.cache import _price_cache

from handlers.scheduler._common import (
    now_wib,
    CRYPTO_TF_TO_INTERVAL,
    SEND_DELAY_SECONDS,
    get_crypto_data_with_fallback,
    store_crypto_scan_data,
    get_stale_crypto_scan_data,
    _get_user_db,
    _get_last_buy_signals,
    _remove_signal,
    _schedule_followup_scan,
    compute_reliability,
    compute_trend,
    entry_price_is_trustworthy,
    find_user_signal,
    signal_in_cooldown,
)

logger = logging.getLogger(__name__)

# Berapa ticker besar yang dipanaskan cache-nya tiap siklus prefetch.
PREFETCH_MAJOR_COUNT = 10

# Alert "scan degrade" dikirim sparing - satu per 6 jam. Scan degrade
# biasanya berlangsung lama (provider sedang bermasalah), jadi alert tiap
# 15 menit cuma jadi spam.
_DEGRADE_ALERT_INTERVAL = 6 * 60 * 60
_last_degrade_alert = 0.0


def _has_valid_rsi(data) -> bool:
    """True kalau data punya RSI yang masuk akal (dipakai buat validasi)."""
    if not data:
        return False
    try:
        rsi = float(data.get('rsi', 0))
    except (TypeError, ValueError):
        return False
    return rsi > 0 and not math.isnan(rsi)


def _alert_degraded_scan(reason: str, detail: str) -> bool:
    """Beri tahu admin kalau scan berjalan jauh di bawah cakupan normal.

    Dipakai logger.error supaya masuk ke alert admin (TelegramLogHandler),
    bukan dikirim manual - supaya tetap jalan kalau bot-nya sendiri sibuk.
    """
    global _last_degrade_alert
    now = time.time()
    if now - _last_degrade_alert < _DEGRADE_ALERT_INTERVAL:
        return False
    _last_degrade_alert = now
    logger.error(f"[CRYPTO] Scan degrade: {reason}. {detail}")
    return True


async def fetch_crypto_prices(tickers, interval: str, period: str) -> dict:
    """Ambil data harga untuk banyak ticker.

    Tahap 1: Yahoo Finance per ticker (dibatasi 20 request/menit).
    Tahap 2: ticker yang gagal diambil dari CoinGecko sekaligus - satu
             request untuk ~100 ticker. Tanpa ini, rate limit CoinGecko
             (5 request/menit) yang menentukan lamanya scan: 83 ticker
             butuh 16 menit.
    Tahap 3: ticker yang gagal total memakai data scan terakhir yang masih
             tersimpan, supaya scan tetap menghasilkan sinyal saat provider
             sedang bermasalah.

    Returns:
        dict ticker -> data.
    """
    semaphore = asyncio.Semaphore(20)

    def fetch_yahoo(ticker):
        """Ambil data Yahoo untuk satu ticker (blocking, dipanggil di thread)."""
        try:
            d = crypto_service.get_crypto_data(ticker, interval, period,
                                               skip_if_missing=True)
            if _has_valid_rsi(d):
                d['source'] = 'yahoo'
                return ticker, d
        except Exception as e:
            logger.warning(f"[CRYPTO] Gagal ambil data Yahoo {ticker}: {e}")
        return ticker, None

    async def fetch_with_limit(ticker):
        async with semaphore:
            return await asyncio.to_thread(fetch_yahoo, ticker)

    data_map: dict = {}
    failed: list = []

    for fut in asyncio.as_completed([fetch_with_limit(t) for t in tickers]):
        ticker, d = await fut
        if d:
            data_map[ticker] = d
            store_crypto_scan_data(ticker, interval, period, d)
        else:
            failed.append(ticker)

    if failed:
        logger.info(f"[CRYPTO] {len(failed)} ticker tanpa data Yahoo, "
                    f"diambil dari CoinGecko sekaligus")
        cg_data = await asyncio.to_thread(
            crypto_service.get_crypto_data_coingecko_bulk, failed)
        for ticker, payload in cg_data.items():
            if _has_valid_rsi(payload):
                data_map[ticker] = payload
                store_crypto_scan_data(ticker, interval, period, payload)

    for ticker in failed:
        if ticker in data_map:
            continue
        stale = get_stale_crypto_scan_data(ticker, interval, period)
        if stale:
            data_map[ticker] = stale
            logger.warning(f"[CRYPTO] Pakai data scan terakhir untuk {ticker} "
                           f"(provider bermasalah)")

    return data_map


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

        # Cakupan scan per TF (dipakai untuk deteksi scan degrade).
        worst_coverage = 1.0
        worst_tf = None

        # Process each TF group separately
        for tf_key, group_users in tf_groups.items():
            interval, period = CRYPTO_TF_TO_INTERVAL.get(tf_key, ('1h', '1mo'))
            logger.info(f"[CRYPTO] TF={tf_key} ({interval}): scanning for {len(group_users)} users")

            buy_signals = []
            # Penghitung stablecoin yang dilewati (dipakai di thread, jadi list).
            stable_skipped = [0]

            # Pengambilan data dulu (Yahoo per ticker + CoinGecko bulk),
            # baru semua ticker dianalisis. Kalau pengambilan dan analisis
            # dicampur, ticker yang datanya dari CoinGecko harus waited satu
            # per satu dan limiter CoinGecko jadi bottleneck scan.
            data_map = await fetch_crypto_prices(all_crypto, interval, period)

            coverage = len(data_map) / max(1, len(all_crypto))
            if coverage < worst_coverage:
                worst_coverage = coverage
                worst_tf = tf_key

            def analyze_crypto_for_tf(ticker):
                """Blocking crypto analysis for specific TF."""
                try:
                    d = data_map.get(ticker)
                    if not d or d.get('candles', 0) < 5:
                        return None

                    # Stablecoin (harga dipatok) bukan aset yang layak
                    # direkomendasikan beli - volatilitasnya ~0, jadi tidak
                    # ada peluang profit dan TP/SL-nya tidak pernah kena.
                    # Lewati sebelum sinyal dibuat supaya tidak ada
                    # notifikasi maupun baris DB.
                    if is_stablecoin(d):
                        stable_skipped[0] += 1
                        return None

                    # Pass TF for timeframe-aware signal generation
                    d['timeframe'] = tf_key
                    s = signal_service.generate_crypto_signal(d)
                    if not s.get('entry') or s['entry'] <= 0:
                        return None

                    # Include REVERSAL and SELL signals
                    if s['signal'] not in ('BUY', 'REVERSAL', 'SELL'):
                        return None
                    score = s.get('sell_score', 0) if s['signal'] == 'SELL' else s.get('buy_score', 0)

                    if score >= 25:
                        # Pengecekan duplikat dilakukan per-user nanti, setelah
                        # semua kandidat terkumpul. Kalau dicek di sini, satu
                        # sinyal bisa lolos ke semua user sekaligus padahal
                        # tiap user punya batasnya sendiri.
                        return (ticker, crypto_service.crypto_pairs.get(ticker, ticker), d, s)

                    return None
                except Exception as e:
                    logger.error(f"[CRYPTO_SIGNAL] analyze failure for {ticker}: {e}", exc_info=True)
                    return None

            semaphore = asyncio.Semaphore(20)
            async def analyze_crypto(ticker):
                async with semaphore:
                    return await asyncio.to_thread(analyze_crypto_for_tf, ticker)

            tasks = [analyze_crypto(t) for t in all_crypto]
            results = await asyncio.gather(*tasks)

            for r in results:
                if r is not None:
                    ticker, name, d, s = r
                    buy_signals.append((ticker, name, d, s))
                    logger.info(f"CRYPTO [{tf_key}] Signal: {ticker} @ ${s['entry']:,.2f}")

            if not buy_signals:
                logger.info(f"[CRYPTO] TF={tf_key}: No BUY signals found "
                            f"({stable_skipped[0]} stablecoin dilewati)")
                continue

            buy_signals.sort(key=lambda x: x[3].get('buy_score', 0), reverse=True)

            logger.info(f"[CRYPTO] TF={tf_key}: Found {len(buy_signals)} signals, "
                        f"sending TOP 3 to {len(group_users)} users "
                        f"({stable_skipped[0]} stablecoin dilewati)")

            # Baris sinyal dari DB dipakai untuk cooldown: baris itu tetap ada
            # setelah sinyal ditutup (SL/TP3) dan setelah bot restart, jadi
            # cooldown tidak hilang sendiri seperti kalau hanya mengandalkan
            # state in-memory. Kalau DB-nya sedang bermasalah, scan tetap
            # jalan - filter sinyal aktif in-memory masih melindungi user
            # dari notifikasi dobel.
            try:
                db_rows = db.get_all_signals()
            except Exception as e:
                logger.warning(f"[CRYPTO] Gagal ambil baris sinyal dari DB: {e}")
                db_rows = {}

            for uid, u in group_users:
                try:
                    # Satu ticker tidak boleh dikirim dua kali ke user yang
                    # sama. Posisi yang masih terbuka di-block (user sudah
                    # masuk posisi, "beli lagi" cuma membingungkan), ticker
                    # yang baru saja dikirim menunggu cooldown.
                    top_signals = []
                    for candidate in buy_signals:
                        if len(top_signals) >= 3:
                            break
                        ticker, name, d, s = candidate
                        sig_direction = 'SHORT' if s['signal'] == 'SELL' else 'LONG'
                        if find_user_signal(signals, uid, ticker, tf_key, sig_direction):
                            continue
                        if signal_in_cooldown(now, db_rows,
                                              f"CRYPTO_{ticker}_{uid}",
                                              tf_key, sig_direction, d['price']):
                            continue
                        top_signals.append(candidate)

                    if not top_signals:
                        logger.info(f"[CRYPTO] TF={tf_key}: No new signals for user {uid}")
                        continue

                    for i, (ticker, name, d, s) in enumerate(top_signals):
                        if i > 0:
                            await asyncio.sleep(SEND_DELAY_SECONDS)

                        # Fetch freshest data using user's TF.
                        # Blocking (request Yahoo/CoinGecko + limiter) jadi
                        # dijalankan di thread - kalau di-await langsung, event
                        # loop bot tertahan dan job lain (mis. TP/SL) terlambat.
                        fresh_d, _ = await asyncio.to_thread(
                            get_crypto_data_with_fallback, ticker, interval, period)

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

                        # Build reasons list (drives dynamic reliability)
                        crypto_reasons = []
                        if d.get('rsi', 50) < 35:
                            crypto_reasons.append(f"RSI Oversold ({d.get('rsi', 0):.0f})")
                        elif d.get('rsi', 50) > 65:
                            crypto_reasons.append(f"RSI Overbought ({d.get('rsi', 0):.0f})")
                        if d.get('ma_fast', 0) > d.get('ma_slow', 0):
                            crypto_reasons.append("MA Golden Cross")
                        elif d.get('ma_fast', 0) < d.get('ma_slow', 0):
                            crypto_reasons.append("MA Death Cross")
                        if s.get('macd_hist', 0) > 0:
                            crypto_reasons.append("MACD Bullish")
                        elif s.get('macd_hist', 0) < 0:
                            crypto_reasons.append("MACD Bearish")
                        if d.get('volume_ratio', 1) > 1.5:
                            crypto_reasons.append(f"Volume Spike ({d.get('volume_ratio', 1):.1f}x)")
                        if d.get('adx', 0) > 25:
                            crypto_reasons.append(f"ADX Strong ({d.get('adx', 0):.0f})")
                        if d.get('stoch_k', 50) < 20:
                            crypto_reasons.append("Stochastic Oversold")
                        elif d.get('stoch_k', 50) > 80:
                            crypto_reasons.append("Stochastic Overbought")

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

                        # Dynamic reliability and trend (crypto uses wider ±2% for change threshold)
                        quality_reliability = compute_reliability(
                            quality=quality,
                            reasons=crypto_reasons,
                            patterns=crypto_patterns,
                            volume_ratio=d.get('volume_ratio', 1),
                        )
                        trend = compute_trend(d, s, change_threshold=2.0)

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
                                name=name,
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

        # Scan degrade: cakupan ticker jauh lebih kecil dari seharusnya, atau
        # daftar pair-nya jatuh ke daftar cadangan. Tanpa ini, bot bisa
        # menampilkan "hanya 11 sinyal" tanpa ada yang tahu kenapa.
        if crypto_service.used_fallback_pairs:
            _alert_degraded_scan(
                "daftar ticker crypto jatuh ke daftar cadangan",
                f"Hanya {len(all_crypto)} koin yang dipantau, bukan "
                f"{len(crypto_service.crypto_pairs)}. Daftar akan dicoba "
                f"lagi otomatis oleh job prefetch.")
        elif worst_coverage < 0.5:
            _alert_degraded_scan(
                "cakupan scan terlalu kecil",
                f"Hanya {worst_coverage:.0%} ticker yang punya data "
                f"(TF {worst_tf}, {len(all_crypto)} ticker dipantau).")

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
                    # Blocking (request provider + limiter) - kalau dipanggil
                    # langsung di event loop, bot stutter dan job lain
                    # (mis. notifikasi harga) ikut terlambat.
                    d = await asyncio.to_thread(
                        crypto_service.get_crypto_data_combined, ticker, interval,
                        period, skip_if_missing=True)
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

                    # Harga sekarang harusnya dekat dengan harga entry. Kalau
                    # jauh berbeda, entry sinyal ini tidak bisa dipercaya -
                    # biasanya entry dihitung dari data provider lain (Yahoo)
                    # sementara harga sekarang dari CoinGecko, dan ticker-nya
                    # sebenarnya aset yang berbeda. Tanpa guard ini, sinyal
                    # langsung kena TP dengan profit fantastis. Tutup tanpa
                    # notifikasi.
                    if not entry_price_is_trustworthy(entry, current_price):
                        logger.warning(
                            f"Signal {key} for {ticker}: entry {entry:.8f} vs "
                            f"current {current_price:.8f} - entry not trustworthy, "
                            f"closing without notification")
                        db.save_signal_outcome(key, 'invalid',
                                               closed_price=current_price)
                        del signals[key]
                        _remove_signal(key)
                        continue

                    # Stablecoin (harga dipatok) tidak punya TP/SL yang
                    # bermakna - volatilitasnya ~0, jadi TP/SL-nya hanya
                    # batas artefak. Sinyal seperti ini ditutup tanpa
                    # notifikasi, sama seperti entry yang tidak bisa dipercaya.
                    if is_stablecoin(d):
                        logger.info(f"Signal {key} for {ticker}: stablecoin - "
                                    f"closing without notification")
                        db.save_signal_outcome(key, 'invalid',
                                               closed_price=current_price)
                        del signals[key]
                        _remove_signal(key)
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
                    #
                    # Urutannya penting: KIRIM dulu, baru tandai sudah kena.
                    # Kalau tandanya disimpan sebelum pesan dikirim, satu
                    # timeout saja sudah cukup untuk menghilangkan notifikasi:
                    # state bilang "sudah di-notifikasi" padahal user tidak
                    # pernah menerimanya, dan siklus berikutnya tidak mengirim
                    # lagi.
                    if not tp_hit.get('tp1') and _is_tp_hit(tp1):
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
                        tp_hit['tp1'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp1')
                        logger.info(f"TP1 hit: {ticker} at {current_price}")

                    # Check TP2 hit
                    if not tp_hit.get('tp2') and _is_tp_hit(tp2):
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
                        tp_hit['tp2'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp2')
                        logger.info(f"TP2 hit: {ticker} at {current_price}")

                    # Check TP3 hit
                    if not tp_hit.get('tp3') and _is_tp_hit(tp3):
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
                        tp_hit['tp3'] = True
                        signals[key]['tp_hit'] = tp_hit
                        db.save_signal_outcome(key, 'tp3', closed_price=current_price)
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
                    # Blocking (request provider + limiter) - dijalankan di
                    # thread supaya event loop bot tidak tertahan.
                    d = await asyncio.to_thread(
                        crypto_service.get_crypto_data_combined, ticker, '1h',
                        '1d', skip_if_missing=True)
                    if not d:
                        continue

                    current_price = d['price']
                    name = crypto_service.crypto_pairs.get(ticker, ticker)
                    usd_idr = crypto_service.get_usd_idr_rate()

                    # Check if price reached target (for crypto, check if ABOVE target for SELL or BELOW for BUY)
                    # Default: alert when price >= target (good for take profit)
                    if current_price >= target_price:
                        msg = f"🎯 *TARGET TERCAPAI: {escape_md(name)} ({escape_md(ticker)})*\n\n"
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
    """Pemanasan cache crypto.

    Tiga hal yang dikerjakan di sini:
    1. Coba lagi ambil daftar pair kalau sebelumnya jatuh ke daftar cadangan
       (12 koin). Tanpa retry, daftar itu tidak pulih sampai restart.
    2. Panaskan ticker besar untuk timeframe yang benar-benar dipakai user.
       Memanaskan timeframe lain tidak-gunanya - scan tetap request ulang.
    3. Ambil data CoinGecko untuk ticker yang tidak ada di Yahoo, sekaligus
       (satu request untuk ~100 ticker). Ini yang membuat scan tidak lagi
       dibatasi rate limit CoinGecko (5 request/menit).

    Cache key format: ``{ticker}_{interval}_{period}`` (matches ``crypto_service.py``).
    """
    try:
        # 1. Pulihkan daftar pair kalau sempat jatuh ke daftar cadangan.
        await asyncio.to_thread(crypto_service.retry_load_crypto_pairs)

        pairs = list(crypto_service.crypto_pairs.keys())

        # 2. Timeframe yang dipakai user (bukan 1h/1d asumsi).
        intervals = set()
        for u in _get_user_db().values():
            if not u.get('notif_crypto', False):
                continue
            tf = u.get('timeframe', '60')
            intervals.add(CRYPTO_TF_TO_INTERVAL.get(tf, ('1h', '1mo')))
        if not intervals:
            intervals = {('1h', '1d')}

        # Ticker besar saja (10) supaya tidak makan jatah rate limit Yahoo
        # (20 request/menit) - ticker lain dikerjakan scan sendiri.
        major_crypto = pairs[:PREFETCH_MAJOR_COUNT]

        def prefetch_crypto(ticker, interval, period):
            """Prefetch single crypto data."""
            try:
                # Check if already cached (crypto_service handles this)
                cache_key = f"{ticker}_{interval}_{period}"
                if _price_cache.get(cache_key):
                    return None

                # Fetch fresh data - this will auto-cache via crypto_service
                d = crypto_service.get_crypto_data_combined(ticker, interval, period,
                                                             skip_if_missing=True)
                if d and d.get('candles', 0) >= 5:
                    return ticker
            except Exception as e:
                logger.debug(f"prefetch crypto probe failed for {ticker}: {e}")
            return None

        semaphore = asyncio.Semaphore(3)  # Reduced from 10 to 3 to avoid rate limits
        async def prefetch_with_limit(ticker, interval, period):
            async with semaphore:
                return await asyncio.to_thread(prefetch_crypto, ticker, interval, period)

        tasks = []
        for interval, period in intervals:
            for ticker in major_crypto:
                tasks.append(prefetch_with_limit(ticker, interval, period))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        cached_count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        if cached_count > 0:
            logger.info(f"[PREFETCH] Crypto cache warmed: {cached_count}/{len(tasks)} pairs "
                        f"untuk timeframe {sorted(intervals)}")

        # 3. Ticker yang tidak ada di Yahoo: ambil dari CoinGecko sekaligus.
        for interval, _ in intervals:
            missing = [t for t in pairs
                       if crypto_yahoo_missing.is_missing(t, interval)]
            if not missing:
                continue
            cg_data = await asyncio.to_thread(
                crypto_service.get_crypto_data_coingecko_bulk, missing)
            if cg_data:
                logger.info(f"[PREFETCH] CoinGecko bulk: {len(cg_data)} ticker "
                            f"dipanaskan ({interval})")

    except Exception as e:
        logger.error(f"Prefetch crypto cache error: {e}", exc_info=True)
