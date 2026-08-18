"""Analisa command - detailed stock/crypto analysis with multi-indicator scoring."""
import asyncio
import logging
import concurrent.futures
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from services.stock_service import stock_service, StockDataResult
from services.crypto_service import crypto_service
from services.signal_service import signal_service
from services.news_service import news_service
from utils.formatters import format_analisa_simple, format_analisa_pemula
from utils.cache import _price_cache
from ._shared import _strip_markdown_chars, _send_with_retry, ALL_STOCKS

logger = logging.getLogger(__name__)


def _build_analisa_keyboard(ticker: str, is_crypto: bool = False) -> InlineKeyboardMarkup:
    """Build inline keyboard with quick action buttons for analisa result."""
    keyboard = []

    # Row 1: Main actions
    row1 = [
        InlineKeyboardButton("⭐ Favorit", callback_data=f"fav_add_{ticker}"),
        InlineKeyboardButton("📊 Chart", callback_data=f"chart_{ticker}"),
    ]
    keyboard.append(row1)

    # Row 2: Secondary actions
    row2 = [
        InlineKeyboardButton("🔄 Refresh", callback_data=f"analisa_{ticker}"),
        InlineKeyboardButton("📈 Sinyal", callback_data=f"signal_{ticker}"),
    ]
    keyboard.append(row2)

    return InlineKeyboardMarkup(keyboard)


async def analisa_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Analisis saham atau crypto dengan format lengkap"""
    args = ctx.args

    logger.info(f"[ANALISA] Command received: {args}")

    if not args:
        await update.message.reply_text(
            "📊 *Analisis Saham/Crypto*\n\n"
            "Usage: `/analisa [KODE]`\n\n"
            "*Contoh:*\n"
            "`/analisa BBCA` - Analisis saham BBCA\n"
            "`/analisa BTC-USD` - Analisis crypto BTC\n"
            "`/analisa ETH` - Analisis crypto ETH",
            parse_mode='Markdown'
        )
        return

    ticker = args[0].upper()
    logger.info(f"[ANALISA] Replying immediately for {ticker}")

    # Step 1: Send "Menganalisis..." message FIRST (fire and forget)
    async def send_immediate_reply():
        try:
            await asyncio.wait_for(
                update.message.reply_text(
                    f"📊 Menganalisis `{ticker}`...\n\n"
                    f"⏳ Mengambil data dari Yahoo Finance...\n"
                    f"📰 Cek berita terbaru...",
                    parse_mode='Markdown',
                    read_timeout=15,
                    write_timeout=15
                ),
                timeout=15
            )
            logger.info(f"[ANALISA] Immediate reply sent for {ticker}")
        except Exception as reply_err:
            logger.warning(f"[ANALISA] Immediate reply failed (non-blocking): {type(reply_err).__name__}: {reply_err}")

    asyncio.create_task(send_immediate_reply())

    try:
        # Load crypto pairs if not loaded
        if not crypto_service.crypto_pairs:
            crypto_service.load_crypto_pairs()

        ticker_upper = ticker.upper()

        # If ticker is in IDX stocks database, ALWAYS treat as stock
        if ticker_upper in ALL_STOCKS:
            is_crypto = False
        else:
            is_crypto = (
                ticker_upper in crypto_service.crypto_pairs
                or (ticker_upper + '-USD') in crypto_service.crypto_pairs
                or (ticker_upper + '-USDT') in crypto_service.crypto_pairs
                or ticker_upper in crypto_service.coingecko_ids
                or ticker.endswith('-USD') or ticker.endswith('-USDT')
                or ticker.endswith('-BTC') or ticker.endswith('-ETH')
                or ticker_upper in ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE',
                                    'WLD', 'SUI', 'APT', 'ARB', 'OP', 'MATIC', 'AVAX',
                                    'LINK', 'DOT', 'UNI', 'ATOM', 'LTC', 'WORLDCOIN',
                                    'PEPE', 'SHIB', 'FIL', 'NEAR', 'AAVE', 'GRT', 'VET',
                                    'ALGO', 'ICP', 'EGLD', 'AXS', 'MANA', 'SAND', 'GALA', 'ENJ']
            )

        # Yahoo probe fallback for unknown tickers
        yahoo_probe = None
        if not is_crypto and len(ticker) <= 12 and ticker not in ALL_STOCKS:
            try:
                probe = crypto_service.get_crypto_data(ticker_upper + '-USD', '1h', '1d')
                if probe and probe.get('candles', 0) >= 5:
                    rsi = probe.get('rsi', float('nan'))
                    import math as _math
                    if not (isinstance(rsi, float) and _math.isnan(rsi)) and rsi > 0:
                        is_crypto = True
                        yahoo_probe = probe
                        yahoo_probe['source'] = 'yahoo'
                        logger.info(f"[ANALISA] Yahoo probe matched crypto for {ticker_upper}")
            except Exception as probe_err:
                logger.debug(f"[ANALISA] Yahoo probe failed for {ticker_upper}: {probe_err}")

        if is_crypto:
            full_ticker = ticker
            for ct in crypto_service.crypto_pairs.keys():
                if ticker.upper() in ct.upper() or ct.startswith(ticker.upper() + '-'):
                    full_ticker = ct
                    break

            if full_ticker == ticker:
                if not ticker.endswith('-USD') and not ticker.endswith('-USDT'):
                    if len(ticker) <= 10:
                        full_ticker = ticker.upper() + '-USD'

            ticker_known = (
                ticker_upper in crypto_service.crypto_pairs
                or (ticker_upper + '-USD') in crypto_service.crypto_pairs
                or (ticker_upper + '-USDT') in crypto_service.crypto_pairs
                or full_ticker in crypto_service.crypto_pairs
                or yahoo_probe is not None
            )

            cache_key = f"crypto_{full_ticker}_1h_5d"
            used_stale_cache = False

            # Step 1: Check cache first (instant, no API call)
            d = _price_cache.get(cache_key)
            if d and d.get('candles', 0) >= 5:
                logger.info(f"[ANALISA] Using cached crypto data for {full_ticker}")
            else:
                d = None
                if yahoo_probe is not None:
                    d = yahoo_probe
                else:
                    try:
                        d = await asyncio.wait_for(
                            asyncio.to_thread(crypto_service.get_crypto_data_combined, full_ticker, '1h', '5d'),
                            timeout=20
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"[ANALISA] Crypto fetch timeout for {full_ticker}")
                        d = None
                    except Exception as fetch_err:
                        logger.error(f"[ANALISA] Crypto fetch exception for {full_ticker}: {type(fetch_err).__name__}: {fetch_err}")
                        await update.message.reply_text(
                            f"❌ *Error teknis* saat mengambil data `{full_ticker}`\n"
                            f"_{type(fetch_err).__name__}: {str(fetch_err)[:120]}_\n\n"
                            "Coba lagi dalam beberapa saat.",
                            parse_mode='Markdown'
                        )
                        return

                # Fallback: try without -USD suffix
                if not d and full_ticker.endswith('-USD'):
                    alt_ticker = full_ticker.replace('-USD', '')
                    try:
                        d = await asyncio.wait_for(
                            asyncio.to_thread(crypto_service.get_crypto_data_combined, alt_ticker, '1h', '5d'),
                            timeout=20
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"[ANALISA] Crypto fallback fetch timeout for {alt_ticker}")
                        d = None

                # Fallback: try with -USDT suffix
                if not d and full_ticker.endswith('-USD'):
                    alt_ticker = full_ticker.replace('-USD', '-USDT')
                    try:
                        d = await asyncio.wait_for(
                            asyncio.to_thread(crypto_service.get_crypto_data_combined, alt_ticker, '1h', '5d'),
                            timeout=20
                        )
                    except asyncio.TimeoutError:
                        logger.error(f"[ANALISA] Crypto USDT fallback fetch timeout for {alt_ticker}")
                        d = None

                # Step 3: If API failed, try stale cache
                if not d:
                    logger.info(f"[ANALISA] Crypto API failed, trying stale cache for {full_ticker}")
                    d = _price_cache.get_stale(cache_key)
                    if d:
                        used_stale_cache = True
                        logger.info(f"[ANALISA] Using STALE crypto cache for {full_ticker}")

            if not d:
                if not ticker_known:
                    await update.message.reply_text(
                        f"❌ Crypto `{ticker}` tidak ditemukan di CoinGecko\n\n"
                        f"Pastikan ticker benar. Contoh:\n"
                        f"• `/analisa BTC` atau `/analisa BTC-USD`\n"
                        f"• `/analisa ETH` atau `/analisa ETH-USD`\n"
                        f"• `/analisa BEAT` (Audiera)\n\n"
                        f"Cek daftar lengkap di https://www.coingecko.com/",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text(
                        f"⚠️ Gagal mengambil data untuk `{ticker}`\n\n"
                        f"Ticker dikenal, tapi CoinGecko & Yahoo Finance tidak merespon.\n\n"
                        f"Coba lagi dalam 1-2 menit saat rate limit reset.",
                        parse_mode='Markdown'
                    )
                return

            if d.get('candles', 0) < 30:
                source = d.get('source', 'unknown')
                await update.message.reply_text(
                    f"❌ Data `{ticker}` tidak cukup untuk dianalisis\n\n"
                    f"Hanya tersedia {d.get('candles', 0)} candle (minimum 30) dari sumber `{source}`.\n"
                    f"Crypto ini mungkin baru listing atau volume sangat rendah.",
                    parse_mode='Markdown'
                )
                return

            s = signal_service.generate_crypto_signal(d)
            if s is None:
                s = {'signal': 'HOLD', 'entry': d.get('price') if d else 0}

            clean_ticker = ticker.replace('-USD', '').replace('-USDT', '').upper()
            sentiment = None
            try:
                result = await asyncio.to_thread(news_service.get_crypto_news, clean_ticker)
                if result and len(result) >= 2:
                    articles, sentiment = result[0], result[1]
            except Exception as e:
                logger.warning(f"Failed to fetch crypto news for {clean_ticker}: {e}")

            name = d.get('name') or ticker
            msg = format_analisa_simple(
                ticker=ticker,
                name=name,
                data=d,
                signal=s,
                sentiment=sentiment,
                is_crypto=True,
                usd_idr_rate=crypto_service.get_usd_idr_rate()
            )

            # Add note if using stale cache
            if used_stale_cache:
                msg = msg.rstrip() + "\n\n⚠️ Data dari cache (stale) karena API sedang tidak merespon"

        else:
            # Stock analysis path
            full_ticker = ticker + ".JK"
            ticker_known = ticker in ALL_STOCKS
            cache_key = f"stock_{ticker}_5m_1d"

            d, sentiment = None, None
            used_stale_cache = False
            fetch_error: str | None = None
            retry_after: int = 0

            # Step 1: Check cache first (instant, no API call)
            d = _price_cache.get(cache_key)
            if d:
                logger.info(f"[ANALISA] Using cached data for {full_ticker}")
            else:
                # Step 2: Try fresh fetch from API
                def fetch_stock_data():
                    try:
                        return stock_service.get_stock_data_combined(full_ticker, '5m', '1d')
                    except Exception as e:
                        logger.error(f"[ANALISA] Stock fetch error for {full_ticker}: {e}")
                        return StockDataResult(success=False, error=str(e), source='exception')

                async def fetch_news_async():
                    try:
                        result = await asyncio.to_thread(news_service.get_stock_news, ticker, None)
                        if result and len(result) >= 2:
                            return result[1]
                    except Exception as e:
                        logger.warning(f"[ANALISA] News fetch failed for {ticker}: {e}")
                    return None

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    stock_future = executor.submit(fetch_stock_data)
                    news_coro = fetch_news_async()

                    try:
                        result = stock_future.result(timeout=20)
                        if result and result.success:
                            d = result.data
                        else:
                            fetch_error = result.error if result else "Unknown error"
                            retry_after = result.retry_after if result else 0
                    except concurrent.futures.TimeoutError:
                        logger.error(f"[ANALISA] Stock fetch timeout for {full_ticker}")
                        fetch_error = "Timeout saat mengambil data"
                        retry_after = 30

                    try:
                        sentiment = await asyncio.wait_for(news_coro, timeout=10)
                    except (asyncio.TimeoutError, Exception):
                        sentiment = None

                # Step 3: If API fails, try stale cache
                if not d:
                    logger.info(f"[ANALISA] API failed (retry_after={retry_after}s), trying stale cache for {full_ticker}")
                    d = _price_cache.get_stale(cache_key)
                    if d:
                        used_stale_cache = True
                        logger.info(f"[ANALISA] Using STALE cache for {full_ticker}")

            name = None
            if d:
                name = d.get('name') or ALL_STOCKS.get(ticker, ticker)
            else:
                name = ALL_STOCKS.get(ticker, ticker)

            if not d:
                if not ticker_known:
                    await _send_with_retry(
                        update.message,
                        f"❌ Saham `{ticker}` tidak ditemukan\n\n"
                        f"Pastikan kode saham benar (4 huruf, contoh: BBCA, TLKM, BMRI).\n"
                        f"Saham yang tidak ada di database IDX mungkin tidak bisa dianalisis.\n\n"
                        f"ℹ️ Ini command untuk saham. Untuk crypto gunakan:\n"
                        f"`/analisa BTC` atau `/analisa BEAT`",
                        parse_mode='Markdown'
                    )
                else:
                    # Better error message with retry timing
                    retry_msg = f"Coba lagi dalam {retry_after} detik." if retry_after > 0 else "Coba lagi dalam 1-2 menit."
                    await _send_with_retry(
                        update.message,
                        f"⚠️ Gagal mengambil data untuk `{ticker}`\n\n"
                        f"Error: {fetch_error or 'Yahoo Finance & TradingView tidak merespon'}\n\n"
                        f"{retry_msg}\n\n"
                        f"💡 Tips: Gunakan `/favorit {ticker}` untuk auto-alert saat data tersedia",
                        parse_mode='Markdown'
                    )
                return
                if not ticker_known:
                    await _send_with_retry(
                        update.message,
                        f"❌ Saham `{ticker}` tidak ditemukan\n\n"
                        f"Pastikan kode saham benar (4 huruf, contoh: BBCA, TLKM, BMRI).\n"
                        f"Saham yang tidak ada di database IDX mungkin tidak bisa dianalisis.\n\n"
                        f"ℹ️ Ini command untuk saham. Untuk crypto gunakan:\n"
                        f"`/analisa BTC` atau `/analisa BEAT`",
                        parse_mode='Markdown'
                    )
                else:
                    await _send_with_retry(
                        update.message,
                        f"⚠️ Gagal mengambil data untuk `{ticker}`\n\n"
                        f"Saham ada di database, tapi Yahoo Finance & TradingView tidak merespon.\n\n"
                        f"Coba lagi dalam 1-2 menit saat rate limit reset.\n\n"
                        f"💡 Tips: Gunakan `/favorit {ticker}` untuk auto-alert",
                        parse_mode='Markdown'
                    )
                return

            if d.get('candles', 0) < 50:
                source = d.get('source', 'unknown')
                await _send_with_retry(
                    update.message,
                    f"❌ Data saham `{ticker}` tidak cukup untuk dianalisis\n\n"
                    f"Hanya tersedia {d.get('candles', 0)} candle (minimum 50) dari sumber `{source}`.\n"
                    f"Coba lagi saat jam pasar (09:00-15:00 WIB) atau saham baru listing.",
                    parse_mode='Markdown'
                )
                return

            s = signal_service.generate_stock_signal(d)
            if s is None:
                s = {'signal': 'HOLD', 'entry': d.get('price') if d else 0}
            logger.info(f"[ANALISA] Signal generated: {s.get('signal')}")
            msg = format_analisa_pemula(
                ticker=ticker,
                name=name,
                data=d,
                signal=s,
                sentiment=sentiment,
                timeframe='5 Menit',
            )

            # Add note if using stale cache
            if used_stale_cache:
                msg = msg.rstrip() + "\n\n⚠️ Data dari cache (stale) karena API sedang tidak merespon"


        logger.info(f"[ANALISA] Sending final result for {ticker}")
        sanitized = _strip_markdown_chars(msg)

        # Build inline keyboard for quick actions
        is_crypto_ticker = ticker_upper in crypto_service.crypto_pairs or ticker.endswith('-USD') or ticker.endswith('-USDT')
        keyboard = _build_analisa_keyboard(ticker, is_crypto=is_crypto_ticker)

        # Send with inline keyboard
        sent = await _send_with_retry(
            update.message, msg, parse_mode='Markdown', reply_markup=keyboard
        )
        if not sent:
            sent = await _send_with_retry(update.message, sanitized, reply_markup=keyboard)
        if sent:
            logger.info(f"[ANALISA] Done for {ticker}")
        else:
            logger.error(f"[ANALISA] Failed to send result for {ticker}")

    except Exception as e:
        logger.error(f"Analisa error: {e}", exc_info=True)
        await _send_with_retry(update.message, f"❌ Error: {str(e)[:300]}")
