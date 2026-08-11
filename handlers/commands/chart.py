"""Chart command - generate price chart for stocks/crypto."""
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from services.crypto_service import crypto_service
from services.chart_service import chart_service

logger = logging.getLogger(__name__)


async def chart_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args

    if not args:
        await update.message.reply_text(
            "📊 *Chart Generator*\n\n"
            "Usage: `/chart [KODE] [TIMEFRAME] [PERIOD]`\n\n"
            "*Contoh:*\n"
            "`/chart BTC-USD` - Chart BTC default\n"
            "`/chart BBCA 15m 5d`",
            parse_mode='Markdown'
        )
        return

    ticker = args[0].upper()
    interval = args[1] if len(args) > 1 else '1h'
    period = args[2] if len(args) > 2 else '5d'

    valid_intervals = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
    valid_periods = ['1d', '2d', '3d', '5d', '7d', '14d', '30d', '60d', '90d']

    if interval not in valid_intervals:
        await update.message.reply_text(f"❌ Interval tidak valid: `{interval}`", parse_mode='Markdown')
        return

    if period not in valid_periods:
        await update.message.reply_text(f"❌ Period tidak valid: `{period}`", parse_mode='Markdown')
        return

    is_crypto = (
        ticker.endswith('-USD') or
        ticker.endswith('-USDT') or
        ticker in crypto_service.crypto_pairs
    )

    common_crypto = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'WLD', 'SUI',
                     'APT', 'ARB', 'OP', 'MATIC', 'AVAX', 'LINK', 'DOT', 'UNI', 'ATOM', 'LTC']
    if ticker in common_crypto and not ticker.endswith('-USD'):
        is_crypto = True
        ticker = ticker + '-USD'

    await update.message.chat.send_action('typing')
    await update.message.reply_text(f"📊 Generating chart for `{ticker}`...", parse_mode='Markdown')

    try:
        if is_crypto:
            img_buf = chart_service.generate_crypto_chart(ticker, interval=interval, period=period)
        else:
            full_ticker = ticker if ticker.endswith('.JK') else ticker + '.JK'
            img_buf = chart_service.generate_price_chart(full_ticker, interval=interval, period=period)

        if img_buf is None:
            await update.message.reply_text(f"❌ Gagal generate chart untuk `{ticker}`.", parse_mode='Markdown')
            return

        await update.message.reply_photo(
            photo=img_buf,
            caption=f"📊 *{ticker}* | {period} ({interval})\n"
                    f"🕐 {datetime.now().strftime('%d %b %Y %H:%M')}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Chart error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
