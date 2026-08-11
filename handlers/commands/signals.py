"""Signal commands - crypto, bsjp, morning_watchlist."""
import asyncio
import logging
import time
from telegram import Update
from telegram.ext import ContextTypes

from services.crypto_service import crypto_service
from services.stock_service import stock_service
from services.signal_service import signal_service
from utils.formatters import format_crypto_msg, format_bsjp_msg, format_morning_msg
from ._shared import ALL_STOCKS

logger = logging.getLogger(__name__)


async def crypto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View crypto signals - PARALLEL FETCHING"""
    start_time = time.time()
    await update.message.chat.send_action('typing')
    await update.message.reply_text("₿ Mengambil data crypto...")

    tickers = list(crypto_service.crypto_pairs.keys())
    total = len(tickers)

    async def fetch_crypto(ticker):
        try:
            d = crypto_service.get_crypto_data_combined(ticker)
            if d:
                s = signal_service.generate_crypto_signal(d)
                if s.get('entry') and s['entry'] > 0:
                    return (ticker, crypto_service.crypto_pairs.get(ticker, ticker), s, ticker)
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
        return None

    semaphore = asyncio.Semaphore(20)

    async def fetch_with_semaphore(ticker):
        async with semaphore:
            return await fetch_crypto(ticker)

    tasks = [fetch_with_semaphore(t) for t in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    signals = [r for r in results if r and not isinstance(r, Exception)]

    elapsed = time.time() - start_time
    logger.info(f"Crypto fetch completed: {len(signals)}/{total} signals in {elapsed:.1f}s")

    await update.message.reply_text(format_crypto_msg(signals), parse_mode='Markdown')


async def bsjp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """BSJP - Beli Sore Jual Pagi signals"""
    await update.message.chat.send_action('typing')
    await update.message.reply_text("🌙 Menganalisis sinyal BSJP...")

    tickers = list(ALL_STOCKS.keys())[:150]

    async def analyze_stock(ticker):
        try:
            d1h = stock_service.get_stock_data_combined(ticker + ".JK", '1h', '3d')
            if not d1h or d1h.get('candles', 0) < 10:
                return None

            rsi = d1h.get('rsi', 50)
            price = d1h['price']
            ma_fast = d1h.get('ma_fast', price)
            ma_slow = d1h.get('ma_slow', price)
            above_ma = price > ma_fast > ma_slow
            rsi_ok = rsi < 65 and rsi > 30
            change = d1h.get('change', 0)

            score = 0
            reasons = []
            if above_ma:
                score += 2
                reasons.append("Above MA")
            if rsi_ok:
                score += 1
                reasons.append(f"RSI {rsi:.0f} OK")
            if change > 0:
                score += 1
                reasons.append(f"+{change:.1f}%")

            if score >= 2:
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
        except Exception as e:
            logger.debug(f"bsjp analyze inner failure: {e}")
        return None

    semaphore = asyncio.Semaphore(30)

    async def fetch_with_semaphore(ticker):
        async with semaphore:
            return await analyze_stock(ticker)

    tasks = [fetch_with_semaphore(t) for t in tickers]
    results = await asyncio.gather(*tasks)

    signals = [r for r in results if r is not None]
    signals.sort(key=lambda x: x['score'], reverse=True)

    await update.message.reply_text(format_bsjp_msg(signals[:10]), parse_mode='Markdown')


async def morning_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Morning watchlist - stocks likely to go up during the day"""
    await update.message.chat.send_action('typing')
    await update.message.reply_text("☀️ Menganalisis rekomendasi pagi...")

    tickers = list(ALL_STOCKS.keys())[:100]

    async def analyze_stock(ticker):
        try:
            d = stock_service.get_stock_data_combined(ticker + ".JK", '1h', '3d')
            if not d or d.get('candles', 0) < 10:
                return None

            rsi = d.get('rsi', 50)
            price = d['price']
            ma_fast = d.get('ma_fast', price)
            ma_slow = d.get('ma_slow', price)
            change = d.get('change', 0)

            score = 0
            reasons = []

            if rsi < 35:
                score += 2
                reasons.append(f"RSI {rsi:.0f} oversold")
            elif rsi < 45:
                score += 1

            if price > ma_fast > ma_slow:
                score += 2
                reasons.append("Above MA")
            elif price > ma_fast:
                score += 1

            if change > 1:
                score += 1
                reasons.append(f"+{change:.1f}%")

            if score >= 2:
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
            logger.debug(f"morning analyze inner failure: {e}")
        return None

    semaphore = asyncio.Semaphore(25)

    async def fetch_with_semaphore(ticker):
        async with semaphore:
            return await analyze_stock(ticker)

    tasks = [fetch_with_semaphore(t) for t in tickers]
    results = await asyncio.gather(*tasks)

    signals = [r for r in results if r is not None]
    signals.sort(key=lambda x: x['score'], reverse=True)

    await update.message.reply_text(format_morning_msg(signals[:10]), parse_mode='Markdown')
