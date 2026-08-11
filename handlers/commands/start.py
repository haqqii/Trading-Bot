"""Start command and main menu buttons."""
import logging
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes

from utils.formatters import TIMEFRAMES
from ._shared import get_user, ALL_STOCKS, _strip_markdown_chars

logger = logging.getLogger(__name__)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    u = get_user(uid)

    kb = [
        ["📊 Harga", "🎯 Sinyal"],
        ["⭐ Favorit", "🌙 BSJP"],
        ["💼 Portfolio", "🔔 Notifikasi"],
        ["⏱️ Timeframe", "₿ Crypto"]
    ]
    rm = ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=False)

    tf_name = TIMEFRAMES[u.get('timeframe', '5')]['name']
    notif_status = "🔔 AKTIF" if u.get('notifications') else "🔕 NONAKTIF"

    msg = f"""🤖 *Ochobot*

📈 *Bot sinyal trading saham IDX & crypto Indonesia*
Menganalisis 683+ saham & 250+ crypto dengan multi-indikator
teknikal (RSI, MACD, Bollinger Bands, MA, VWAP, ADX, Ichimoku).

━━━━━━━━━━━━━━━━━━━━━━━━━━
👋 Halo {user.first_name}!

📊 Saham: *{len(ALL_STOCKS)}*
⏱️ Timeframe: *{tf_name}*

💡 _Timeframe = interval candle yg dianalisis_
• 1m/5m → Scalping (trading cepat, 5-15 menit)
• 15m/1h → Intraday (trading harian)
• Default: 5 Menit (cok untuk pemula)

🔔 Notifikasi: {notif_status}

━━━━━━━━━━━━━━━━━━━━━━━━━━
📱 *MENU*

🎯 Sinyal - Sinyal BUY saham
📊 Harga - Daftar harga
⭐ Favorit - Saham favorit + alert
🌙 BSJP - Beli sore jual pagi
💼 Portfolio - Portfolio Anda
🔔 Notifikasi - Setting notifikasi
₿ Crypto - Sinyal crypto
⏱️ Timeframe - Ganti timeframe

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 */help* untuk daftar command lengkap
⚠️ Trading risiko tanggung sendiri"""

    try:
        await update.message.reply_text(msg, reply_markup=rm, parse_mode='Markdown',
                                         read_timeout=30, write_timeout=30, connect_timeout=30)
    except Exception as start_err:
        logger.warning(f"[START] Markdown reply failed: {start_err}, retrying plain text")
        try:
            await update.message.reply_text(_strip_markdown_chars(msg), reply_markup=rm,
                                             read_timeout=30, write_timeout=30, connect_timeout=30)
        except Exception as plain_err:
            logger.error(f"[START] Plain text reply also failed: {plain_err}")


async def harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)
    tf = TIMEFRAMES[u.get('timeframe', '5')]

    await update.message.chat.send_action('typing')
    await update.message.reply_text("⏳ Mengambil data harga...")

    stocks = list(ALL_STOCKS.items())[:30]

    async def fetch_stock(ticker, name):
        d = stock_service.get_stock_data_combined(ticker + ".JK", tf['interval'], tf['period'])
        if d:
            return (ticker, name, d)
        return None

    tasks = [fetch_stock(t, n) for t, n in stocks]
    fetched = await asyncio.gather(*tasks)
    results = [r for r in fetched if r is not None]

    msg = f"📊 *DAFTAR HARGA SAHAM*\n"
    msg += f"⏱️ Timeframe: {tf['name']}\n"
    msg += "═" * 40 + "\n\n"

    for t, n, d in results:
        emoji = "🟢" if d['change'] >= 0 else "🔴"
        sign = "+" if d['change'] >= 0 else ""
        msg += f"{emoji} *{t}* - {n}\n"
        msg += f"   💵 Rp {d['price']:,.0f}\n"
        msg += f"   📈 {sign}{d['change']:.2f}%\n\n"

    msg += "═" * 40 + "\n"
    msg += f"📅 {datetime.now().strftime('%d %b %Y %H:%M')}"

    await update.message.reply_text(msg, parse_mode='Markdown')


# Import for harga function
import asyncio
from services.stock_service import stock_service


async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route button text to corresponding handler."""
    from handlers.command_handlers import (
        stats_cmd, morning_watchlist, favorit, tf, bsjp, portfolio, notifikasi, crypto
    )
    handlers = {
        "📊 Harga": harga,
        "📈 Stats": stats_cmd,
        "🎯 Sinyal": morning_watchlist,
        "⭐ Favorit": favorit,
        "⏱️ Timeframe": tf,
        "🌙 BSJP": bsjp,
        "💼 Portfolio": portfolio,
        "🔔 Notifikasi": notifikasi,
        "₿ Crypto": crypto,
    }
    if update.message.text in handlers:
        await handlers[update.message.text](update, ctx)
