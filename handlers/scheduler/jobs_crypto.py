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
from services.signal_service import calc_tPSL, safe_num, signal_service, FEATURES
from services import crypto_yahoo_missing
from services import exit_manager
from utils.formatters import format_unified_crypto_notification, escape_md
from db import db
from utils.cache import _price_cache
from utils.rate_limiter import coingecko_can_call_now

from handlers.scheduler._common import (
    now_wib,
    CRYPTO_TF_TO_INTERVAL,
    SEND_DELAY_SECONDS,
    get_crypto_data_with_fallback,
    store_crypto_scan_data,
    get_crypto_scan_data,
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

# Seberapa jauh harga CoinGecko boleh berbeda dari harga scan terakhir
# sebelum dipakai untuk cek TP/SL. Simbol di Yahoo dan di CoinGecko tidak
# selalu menunjuk aset yang sama (mis. JUP-USD), jadi harga CoinGecko
# hanya dipakai kalau masih cocok dengan data scan terakhir. Kalau beda
# lebih dari ini, harga diambil dari Yahoo (sumber harga entry sinyal).
_TP_SL_PRICE_TOLERANCE = 0.05

# Toleransi "apakah ticker-nya menunjuk aset yang sama" - dipakai kalau
# tidak ada harga acuan dari scan (mis. tepat setelah restart container).
# Jauh lebih longgar dari _TP_SL_PRICE_TOLERANCE: yang diuji adalah
# pertukaran aset antar provider, bukan pergerakan harga harian.
_SAME_ASSET_MAX_RATIO = 2.0

# Berapa bagian harga CoinGecko yang harus ada di cache sebelum cache itu
# dipakai buat cek TP/SL tanpa menunggu slot limiter. Kalau cache-nya
# belum terisi (mis. tepat setelah restart), lebih baik tunggu slot
# daripada memeriksa sinyal tanpa harga.
_COINGECKO_CACHE_COVERAGE = 0.8

# Alert "scan degrade" dikirim sparing - satu per 6 jam. Scan degrade
# biasanya berlangsung lama (provider sedang bermasalah), jadi alert tiap
# 15 menit cuma jadi spam.
_DEGRADE_ALERT_INTERVAL = 6 * 60 * 60
_last_degrade_alert = 0.0

# Batas waktu tahap Yahoo di job cek TP/SL. Job ini jalan tiap 2 menit,
# jadi satu tahap yang lama bisa bikin siklus berikutnya kena skip.
# Kalau lewat batas ini, harga diambil dari CoinGecko (datanya sudah
# ada di tangan job ini) - notifikasi tetap jalan, walau sedikit terlambat.
_TP_SL_YAHOO_TIMEOUT = 45


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
    # Acuan harga CoinGecko disegarkan sekali per siklus scan, di sini.
    # Kalau tidak, tiap fetch ticker yang butuh pencocokan harga bisa
    # memicu pemuatan acuan (1-3 request CoinGecko) dan menahan jalurnya
    # hampir semenit di antrean limiter. Dijalankan sebagai task supaya
    # scan tidak ikut menunggu antrean CoinGecko.
    async def _refresh_ref():
        try:
            # priority=False: jalan di belakang. Kuota yang dipesan untuk
            # job cek TP/SL tidak boleh dimakan di sini.
            await asyncio.to_thread(crypto_service.refresh_price_reference,
                                    priority=False)
        except Exception as ref_err:
            logger.warning(f"[CRYPTO] Gagal segarkan acuan harga CoinGecko: {ref_err}")

    asyncio.create_task(_refresh_ref())

    # 10 request sekaligus. Kuota limiter (16/menit) yang jadi penentu
    # kecepatan, tapi jumlah worker menentukan seberapa cepat satu fetch
    # yang lambat (Yahoo yang timeout bisa tahan 2 menit) dihide.
    # 20 worker dulu dipakai, tapi itu bikin cache SQLite yfinance contends
    # ("database is locked") sehingga tiap fetch jadi lebih lambat.
    semaphore = asyncio.Semaphore(10)

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

    yahoo_t0 = time.monotonic()
    for fut in asyncio.as_completed([fetch_with_limit(t) for t in tickers]):
        ticker, d = await fut
        if d:
            data_map[ticker] = d
            store_crypto_scan_data(ticker, interval, period, d)
        else:
            failed.append(ticker)
    yahoo_secs = time.monotonic() - yahoo_t0
    # Log durasi tahap ini penting: kalau scan kena interval 15 menit,
    # siklus berikutnya akan kena skip dan sinyal baru telat satu siklus.
    logger.info(
        f"[CRYPTO] tahap Yahoo {len(tickers)} ticker - {yahoo_secs:.1f} detik "
        f"({len(tickers) / max(yahoo_secs, 0.1) * 60:.0f} ticker/menit, "
        f"{len(failed)} tanpa data)")

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
        # Data scan terakhir (basi pun boleh). Pakai get_crypto_scan_data
        # supaya entri yang masih ada di cache "segar" tapi sudah lewat
        # TTL-nya juga ketemu - get_stale_crypto_scan_data hanya melihat
        # entri yang sudah dipindah ke cache basi.
        stale = get_crypto_scan_data(ticker, interval, period)
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
                            atr = safe_num(d.get('atr'), entry_price * 0.02)
                            s['entry'] = entry_price
                            s['atr'] = atr
                            # Calculate TP/SL using user's TF — direction-aware for SELL
                            direction = 'SELL' if s['signal'] == 'SELL' else 'BUY'
                            tpsl = calc_tPSL(direction, entry_price, atr, tf_key,
                                             support=d.get('support'),
                                             resistance=d.get('resistance'))
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
                        # Angka boleh None dari jalur fallback - dibaca lewat
                        # safe_num supaya perbandingannya tidak meledak.
                        crypto_reasons = []
                        rsi_val = safe_num(d.get('rsi'), 50)
                        if rsi_val < 35:
                            crypto_reasons.append(f"RSI Oversold ({rsi_val:.0f})")
                        elif rsi_val > 65:
                            crypto_reasons.append(f"RSI Overbought ({rsi_val:.0f})")
                        ma_fast_val = safe_num(d.get('ma_fast'), 0)
                        ma_slow_val = safe_num(d.get('ma_slow'), 0)
                        if ma_fast_val > ma_slow_val:
                            crypto_reasons.append("MA Golden Cross")
                        elif ma_fast_val < ma_slow_val:
                            crypto_reasons.append("MA Death Cross")
                        macd_hist_val = safe_num(s.get('macd_hist'), 0)
                        if macd_hist_val > 0:
                            crypto_reasons.append("MACD Bullish")
                        elif macd_hist_val < 0:
                            crypto_reasons.append("MACD Bearish")
                        volume_ratio_val = safe_num(d.get('volume_ratio'), 1)
                        if volume_ratio_val > 1.5:
                            crypto_reasons.append(f"Volume Spike ({volume_ratio_val:.1f}x)")
                        if safe_num(d.get('adx'), 0) > 25:
                            crypto_reasons.append(f"ADX Strong ({safe_num(d.get('adx'), 0):.0f})")
                        stoch_k_val = safe_num(d.get('stoch_k'), 50)
                        if stoch_k_val < 20:
                            crypto_reasons.append("Stochastic Oversold")
                        elif stoch_k_val > 80:
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
                            volume_ratio=safe_num(d.get('volume_ratio'), 1),
                        )
                        trend = compute_trend(d, s, change_threshold=2.0)

                        notif_type = direction if direction == 'SELL' else ('REVERSAL' if s.get('is_reversal', False) else 'BUY')

                        analysis_data = {
                            'pattern': {'type': trend, 'reliability': quality_reliability},
                            'patterns': crypto_patterns,
                            'leverage': 5 if quality == 'STRONG' else 3,
                            'indicators': {
                                'rsi': safe_num(d.get('rsi'), 0),
                                'macd': safe_num(s.get('macd_hist'), 0),
                                'atr': safe_num(s.get('atr'), 0),
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
            expected = getattr(crypto_service, 'expected_pair_count', 0) or 0
            source = getattr(crypto_service, 'pair_source', 'fallback')
            if source == 'cache':
                # Cakupan tetap penuh - yang dipakai daftar lengkap
                # terakhir yang tersimpan. Bukan penurunan cakupan, jadi
                # cukup dicatat di log, tidak perlu dikirimi admin.
                logger.warning(
                    f"[CRYPTO] CoinGecko rate limited - cakupan crypto "
                    f"memakai daftar tersimpan ({len(all_crypto)} pair "
                    f"dari sekitar {expected}). Daftar akan dimuat ulang "
                    f"otomatis dari CoinGecko.")
            else:
                _alert_degraded_scan(
                    "daftar ticker crypto jatuh ke daftar cadangan",
                    f"Hanya {len(all_crypto)} koin yang dipantau dari sekitar "
                    f"{expected} pair normal. Daftar akan dicoba lagi otomatis "
                    f"oleh job prefetch.")
        elif worst_coverage < 0.5:
            _alert_degraded_scan(
                "cakupan scan terlalu kecil",
                f"Hanya {worst_coverage:.0%} ticker yang punya data "
                f"(TF {worst_tf}, {len(all_crypto)} ticker dipantau).")

    except Exception as e:
        logger.error(f"Error in check_crypto_signals: {e}", exc_info=True)


async def _fetch_prices_for_tp_sl(user_db, signals):
    """Ambil harga semua sinyal crypto yang dipantau, sekaligus.

    Kalau diambil satu per satu dari Yahoo, tiap sinyal harus antre di
    limiter provider. Saat scan crypto sedang jalan limiter itu penuh,
    jadi satu siklus cek TP/SL bisa lewat 2 menit - job berikutnya lalu
    kena "maximum number of running instances reached" dan notifikasi
    TP/SL telat.

    Jalur pengambilan:
    1. Satu request bulk CoinGecko untuk semua ticker yang dipantau.
       Endpoint /coins/markets bisa membawa puluhan ticker sekaligus,
       jadi satu request cukup untuk seluruh sinyal yang dilacak.
       Kalau limiter CoinGecko penuh, pakai harga dari cache (5 menit
       lalu) daripada menunggu slot - menunggu hampir semenit sudah
       cukup untuk bikin siklus lewat batas 2 menit.
    2. Harga CoinGecko dipakai hanya kalau cocok dengan harga scan
       terakhir (dalam _TP_SL_PRICE_TOLERANCE). Kalau ticker-nya di
       Yahoo menunjuk aset yang lain, price-nya bukan harga yang dicari
       - ticker seperti itu diambil dari Yahoo.
    3. Harga Yahoo diambil dari cache dulu (data scan terakhir, sumber
       yang sama dengan harga entry sinyal). Request baru ke Yahoo
       hanya kalau cache-nya kosong - request baru harus antre di
       limiter yang sedang dipakai penuh oleh scan.

    Returns:
        dict key sinyal -> data harga (hanya yang datanya berhasil diambil).
    """
    wanted = []
    for uid, u in user_db.items():
        if not u.get('notif_crypto', False):
            continue
        user_tf = u.get('timeframe', '60')
        interval, period = CRYPTO_TF_TO_INTERVAL.get(user_tf, ('1h', '1mo'))
        for key, signal_data in list(signals.items()):
            if signal_data.get('type') != 'crypto':
                continue
            if signal_data.get('user_id') != uid:
                continue
            ticker = signal_data.get('ticker_raw')
            if not ticker:
                continue
            wanted.append((key, ticker, interval, period))

    if not wanted:
        return {}

    # Harga entry sinyal dipakai sebagai pembanding kalau data scan
    # terakhir tidak ada (mis. tepat setelah container start). Entry
    # dihitung dari sumber yang sama dengan harga scan, jadi cocok
    # untuk memastikan ticker-nya bukan aset yang berbeda.
    entry_prices = {}
    for key, signal_data in list(signals.items()):
        try:
            entry_prices[key] = float(signal_data.get('entry') or 0)
        except (TypeError, ValueError):
            entry_prices[key] = 0

    # Kelompokkan per timeframe - tiap kelompok cukup satu pengambilan.
    groups: dict = {}
    for key, ticker, interval, period in wanted:
        groups.setdefault((interval, period), []).append((key, ticker))

    async def _fetch_group(interval, period, items):
        tickers = [ticker for _, ticker in items]
        result: dict = {}

        # Satu request untuk semua ticker yang dipantau.
        # use_cache=False supaya harganya benar-benar segar - cache
        # CoinGecko bertahan 5 menit, terlalu lama untuk cek TP/SL.
        #
        # Kalau limiter CoinGecko sedang penuh, menunggu slot bisa memakan
        # hampir semenit - itu sendiri sudah cukup untuk bikin siklus ini
        # lewat batas 2 menit, jadi siklus berikutnya kena skip. Tapi
        # kalau cache-nya belum terisi (mis. tepat setelah restart),
        # menunggu slot lebih baik daripada tidak punya harga sama sekali.
        t0 = time.monotonic()
        cached_cg = crypto_service.get_crypto_data_coingecko_cached(tickers)
        if coingecko_can_call_now(priority=True):
            cg_data = await asyncio.to_thread(
                crypto_service.get_crypto_data_coingecko_bulk, tickers, 3, False,
                priority=True)
        elif len(cached_cg) >= len(tickers) * _COINGECKO_CACHE_COVERAGE:
            cg_data = cached_cg
            logger.info(
                "[TP/SL] limiter CoinGecko penuh - pakai harga dari cache "
                f"({len(cg_data)}/{len(tickers)} ticker)")
        else:
            # Cache belum lengkap - tunggu slot saja. Menunggu paling
            # lama satu menit, masih jauh di bawah batas 2 menit job.
            logger.info(
                f"[TP/SL] limiter CoinGecko penuh dan cache baru "
                f"{len(cached_cg)}/{len(tickers)} - tunggu slot")
            cg_data = await asyncio.to_thread(
                crypto_service.get_crypto_data_coingecko_bulk, tickers, 3, False,
                priority=True)
        cg_secs = time.monotonic() - t0

        need_yahoo = []
        for key, ticker in items:
            payload = cg_data.get(ticker)
            if not payload or not _has_valid_rsi(payload):
                need_yahoo.append(ticker)
                continue
            ref = get_crypto_scan_data(ticker, interval, period)
            price_ref = ref.get('price') if ref else None
            if price_ref:
                same_asset = _price_matches_reference(payload.get('price'),
                                                     price_ref)
            else:
                # Tidak ada data scan untuk ticker ini - biasanya tepat
                # setelah container restart, waktu cache scan masih kosong.
                #
                # Pembandingnya jadi harga entry sinyal, dan toleransinya
                # longgar: yang diuji di sini bukan pergerakan harga
                # (5% terlalu ketat untuk aset yang bergerak cepat) tapi
                # "apakah ticker ini aset yang sama". Selisih lebih dari
                # 2x dicurigai sebagai ticker yang menunjuk aset lain.
                same_asset = _price_is_same_asset(payload.get('price'),
                                                 entry_prices.get(key))
            if same_asset:
                result[ticker] = payload
            else:
                # Harga CoinGecko beda jauh dari data scan - ticker ini
                # bisa jadi aset yang berbeda di Yahoo. Ambil dari Yahoo
                # dulu; harga CoinGecko dipakai kalau Yahoo tidak punya.
                need_yahoo.append(ticker)

        # Ticker yang tidak ada di CoinGecko, atau yang harganya beda jauh
        # dari data scan terakhir, butuh harga Yahoo.
        if need_yahoo:
            # Cache harga provider dipakai dulu. Isinya berasal dari scan
            # terakhir (sumber harga yang sama dengan harga entry sinyal),
            # jadi tidak perlu request baru. Request baru harus antre di
            # limiter Yahoo yang sedang dipakai penuh oleh scan - itu yang
            # bikin satu siklus cek TP/SL lewat 2 menit dan siklus
            # berikutnya kena skip.
            still_need_fetch = []
            for ticker in need_yahoo:
                cached = _get_cached_yahoo_data(ticker, interval, period)
                if cached and _has_valid_rsi(cached):
                    result[ticker] = cached
                else:
                    still_need_fetch.append(ticker)

            if still_need_fetch:
                t1 = time.monotonic()
                async def _fetch_yahoo(ticker):
                    # priority=True: notifikasi TP/SL harus cepat. Slot
                    # limiter yang dicadangkan dipakai di sini, supaya
                    # request ini tidak antre belasan detik di belakang
                    # scan crypto yang sedang jalan.
                    d = await asyncio.to_thread(
                        crypto_service.get_crypto_data, ticker, interval, period,
                        skip_if_missing=True, priority=True)
                    return ticker, d

                # Tahap ini dibatasi waktunya. Satu ticker yang lambat
                # (mis. Yahoo cuma punya sedikit candle untuk timeframe 4h)
                # pernah menahan tahap ini 90 detik, dan itu
                # mendorong siklus ke batas 2 menit - siklus berikutnya
                # lalu kena skip dan notifikasi telat.
                #
                # asyncio.wait, bukan wait_for: wait_for membatalkan task
                # dan tetap menunggu thread-nya selesai (thread tidak bisa
                # diinterupsi), jadi batasnya jadi tidak konsisten.
                # asyncio.wait melepas task yang belum selesai - job lanjut
                # dengan harga CoinGecko, thread sisa dibiarkan beres sendiri.
                tasks = [asyncio.create_task(_fetch_yahoo(t))
                         for t in still_need_fetch]
                done, pending = await asyncio.wait(
                    tasks, timeout=_TP_SL_YAHOO_TIMEOUT)

                fetched_map = {}
                for task in done:
                    try:
                        ticker, d = task.result()
                    except Exception as fetch_err:
                        logger.warning(
                            f"[TP/SL] gagal ambil harga Yahoo: {fetch_err}")
                        continue
                    fetched_map[ticker] = d

                if pending:
                    logger.warning(
                        f"[TP/SL] tahap Yahoo lewat {_TP_SL_YAHOO_TIMEOUT} "
                        f"detik ({len(pending)} ticker) - pakai harga "
                        f"CoinGecko untuk sisa ticker")

                for ticker in still_need_fetch:
                    d = fetched_map.get(ticker)
                    if d and _has_valid_rsi(d):
                        result[ticker] = d
                    else:
                        # Yahoo tidak punya ticker ini - CoinGecko satu-satunya
                        # sumber harga yang ada.
                        payload = cg_data.get(ticker)
                        if payload and _has_valid_rsi(payload):
                            result[ticker] = payload
                yahoo_secs = time.monotonic() - t1
                logger.info(
                    f"[TP/SL] {len(still_need_fetch)} ticker butuh request "
                    f"Yahoo baru (dari {len(need_yahoo)} yang tidak cocok "
                    f"CoinGecko) - {yahoo_secs:.1f} detik")

        logger.info(
            f"[TP/SL] harga {len(result)}/{len(tickers)} ticker "
            f"(CoinGecko bulk {cg_secs:.1f} detik)")
        return {key: result.get(ticker) for key, ticker in items}

    results = await asyncio.gather(*[
        _fetch_group(interval, period, items)
        for (interval, period), items in groups.items()
    ])

    price_cache: dict = {}
    for group_result in results:
        for key, data in group_result.items():
            if data:
                price_cache[key] = data
    return price_cache


def _get_cached_yahoo_data(ticker: str, interval: str, period: str):
    """Data harga Yahoo yang sudah ada di cache, tanpa request baru.

    Cache harga provider disimpan dengan TTL 60 detik, lalu masih ada
    300 detik lagi sebagai data basi. Selama scan berjalan, datanya
    masih kepakai - dan sumbernya sama dengan harga entry sinyal
    (Yahoo), jadi aman dipakai untuk cek TP/SL.

    Data basi hanya bisa menghilangkan TP/SL (harga yang sudah balik),
    tidak pernah memicu TP/SL palsu - jadi lebih baik daripada
    menunggu request yang antre di limiter.
    """
    cache_key = f"{ticker}_{interval}_{period}"
    return (crypto_service.cache.get(cache_key)
            or crypto_service.cache.get_stale(cache_key))


def _price_matches_reference(price, reference) -> bool:
    """True kalau dua harga dianggap harga dari aset yang sama.

    CoinGecko dan Yahoo bisa punya harga yang beda jauh untuk simbol
    yang sama, kalau simbolnya menunjuk aset berbeda. Kalau tidak ada
    harga pembanding, hasilnya False - pemanggil lalu pakai Yahoo.
    """
    try:
        price = float(price)
        reference = float(reference)
    except (TypeError, ValueError):
        return False
    if price <= 0 or reference <= 0:
        return False
    return abs(price / reference - 1) <= _TP_SL_PRICE_TOLERANCE


def _price_is_same_asset(price, reference) -> bool:
    """True kalau dua harga dianggap harga dari aset yang sama.

    Dipakai kalau tidak ada harga acuan dari scan (mis. tepat setelah
    container restart, waktu cache scan masih kosong). Yang diuji di sini
    bukan pergerakan harga - tapi apakah ticker-nya menunjuk aset yang
    sama, jadi toleransinya jauh lebih longgar dari
    _TP_SL_PRICE_TOLERANCE: aset crypto bisa bergerak berkali-kali dalam
    sehari, sedangkan ticker yang tertukar aset biasanya bedanya jauh
    lebih dari itu.
    """
    try:
        price = float(price)
        reference = float(reference)
    except (TypeError, ValueError):
        return False
    if price <= 0 or reference <= 0:
        return False
    ratio = price / reference
    return (1 / _SAME_ASSET_MAX_RATIO) <= ratio <= _SAME_ASSET_MAX_RATIO


async def check_crypto_tp_sl(app):
    """Check and notify TP/SL hits for tracked crypto signals."""
    try:
        user_db = _get_user_db()
        signals = _get_last_buy_signals()

        # Harga semua sinyal diambil sekali di awal (paralel), lalu dipakai
        # di bawah per sinyal.
        price_cache = await _fetch_prices_for_tp_sl(user_db, signals)

        for uid, u in user_db.items():
            if not u.get('notif_crypto', False):
                continue

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
                    d = price_cache.get(key)
                    if not d:
                        continue

                    current_price = d['price']
                    entry = signal_data.get('entry', 0)
                    atr = safe_num(signal_data.get('atr'), 0)
                    is_short = signal_data.get('direction', 'LONG') == 'SHORT'

                    # Recalculate TP/SL based on user's TF (per-user TP/SL)
                    if entry > 0 and atr > 0:
                        tpsl = calc_tPSL('SELL' if is_short else 'BUY', entry, atr, user_tf,
                                         support=d.get('support'),
                                         resistance=d.get('resistance'))
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

                    # SL efektif: setelah TP1 kena SL digeser ke breakeven,
                    # setelah TP2 kena SL trailing (minimal di level TP1).
                    sl_effective = sl
                    if FEATURES['exit_mgmt'] and tp_hit.get('tp1'):
                        direction = 'SHORT' if is_short else 'LONG'
                        locked = signal_data.get('sl_lock') or sl
                        sl_effective = exit_manager.update_sl(
                            locked, entry, direction, tp_hit,
                            current_price, atr, tp1=tp1)
                        signals[key]['sl_lock'] = sl_effective

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

                    sl_hit = (sl_effective > 0) and (current_price >= sl_effective if is_short else current_price <= sl_effective)

                    # === CHECK SL FIRST ===
                    # If SL hit, send SL notification and delete signal immediately.
                    # Skip TP checks to avoid sending TP after SL.
                    if sl_hit:
                        # Exit kind-nya beda tergantung SL yang sedang aktif:
                        # - TP1 kena, TP2 belum : SL di entry -> break even
                        # - TP2 juga kena          : SL trailing -> exit profit
                        # - belum ada TP kena     : SL asli -> cut loss
                        if FEATURES['exit_mgmt']:
                            exit_kind = exit_manager.exit_outcome(tp_hit)
                        else:
                            exit_kind = 'sl'
                        # Harga exit = harga yang benar-benar terlihat saat
                        # cek, sama seperti TP3 dan SL biasa. Level SL yang
                        # aktif ditampilkan terpisah sebagai info stop.
                        exit_price = current_price
                        profit_pct = _profit_pct(exit_price)

                        notes = {
                            'beakeven': ('SL sudah digeser ke harga entry setelah '
                                         'TP1 kena - risiko kembali ke modal.'),
                            'trailing': ('SL sudah naik (trailing stop) setelah TP2 '
                                         'kena - profit terkunci.'),
                        }
                        notif_types = {'beakeven': 'BE', 'trailing': 'TRAIL'}

                        name = signal_data.get('name', ticker)
                        msg = format_unified_crypto_notification(
                            notif_type=notif_types.get(exit_kind, 'SL'),
                            ticker=ticker,
                            name=name,
                            entry=entry,
                            current_price=current_price,
                            tp1=tp1, tp2=tp2, tp3=tp3,
                            sl=sl_effective,
                            analysis_data=tp_analysis,
                            change_pct=profit_pct,
                            profit_loss=profit_pct,
                            usd_idr_rate=crypto_service.get_usd_idr_rate(),
                            exit_note=notes.get(exit_kind, '')
                        )
                        await app.bot.send_message(chat_id=int(uid), text=msg, parse_mode='Markdown')
                        logger.info(f"{exit_kind.upper()} hit: {ticker} at {current_price} - Position closed")

                        db.save_signal_outcome(key, exit_kind,
                                               closed_price=exit_price)

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
                            usd_idr_rate=crypto_service.get_usd_idr_rate(),
                            exit_note=('SL dipindah ke breakeven (harga entry) - '
                                       'risiko jadi 0.') if FEATURES['exit_mgmt'] else ''
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
                            usd_idr_rate=crypto_service.get_usd_idr_rate(),
                            exit_note=('SL sekarang trailing (minimal di level TP1) - '
                                       'profit TP1 terkunci.') if FEATURES['exit_mgmt'] else ''
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

            # Target yang punya harga target. Ambil harga semuanya sekaligus
            # (Yahoo paralel + CoinGecko bulk) - kalau satu per satu, tiap
            # ticker antre di limiter provider dan satu siklus bisa lewat
            # 2 menit.
            targets = {t: p for t, p in crypto_favorit.items()
                       if p is not None}
            if not targets:
                continue

            price_map = await fetch_crypto_prices(list(targets), '1h', '1d')

            for ticker, target_price in targets.items():
                try:
                    d = price_map.get(ticker)
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


async def check_crypto_pairs_recovery(app):
    """Coba lagi ambil daftar pair crypto kalau jatuh ke daftar cadangan.

    Job ini jalan tiap 2 menit, tapi hanya melakukan sesuatu kalau daftar
    sedang fallback - kalau daftar sudah lengkap, langsung selesai tanpa
    request ke CoinGecko.

    Kenapa perlu: kalau CoinGecko kena rate limit saat bot start, hanya 12
    koin yang dipantau. Job prefetch (15 menit) terlalu jarang untuk
    memulihkan, jadi user bisa kehilangan separuh aset selama belasan menit
    tanpa ada yang tahu.
    """
    try:
        if not crypto_service.used_fallback_pairs:
            return

        recovered = await asyncio.to_thread(
            crypto_service.retry_load_crypto_pairs)
        if recovered:
            logger.info(
                "Daftar pair crypto pulih - universe dipantau penuh lagi "
                f"({len(crypto_service.crypto_pairs)} pair)")
    except Exception as e:
        logger.warning(f"Gagal memulihkan daftar pair crypto: {e}")


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
