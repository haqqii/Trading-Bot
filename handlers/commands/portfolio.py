"""Portfolio commands - /portfolio, /buy, /sell."""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from ._shared import get_user, save_user_data

logger = logging.getLogger(__name__)


async def portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show user portfolio"""
    uid = str(update.effective_user.id)
    u = get_user(uid)
    portfolio = u.get('portfolio', [])

    if not portfolio:
        await update.message.reply_text(
            "💼 *PORTFOLIO KOSONG*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 Cara catat posisi:\n"
            "`/buy [KODE] [HARGA] [LOT]`\n"
            "Contoh: `/buy BBCA 9500 100`\n\n"
            "Untuk jual:\n"
            "`/sell [KODE] [LOT]`\n"
            "Contoh: `/sell BBCA 50`",
            parse_mode='Markdown'
        )
        return

    msg = "💼 *PORTFOLIO*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    total_buy = 0
    for pos in portfolio:
        ticker = pos.get('ticker', '?')
        buy_price = pos.get('buy_price', 0)
        lot = pos.get('lot', 0)
        buy_date = pos.get('buy_date', '?')
        total = buy_price * lot * 100  # 1 lot = 100 lembar
        total_buy += total
        msg += f"📈 *{ticker}*\n"
        msg += f"   💵 Rp {buy_price:,.0f} × {lot} lot\n"
        msg += f"   💰 Total: Rp {total:,.0f}\n"
        msg += f"   📅 {buy_date}\n\n"

    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💼 Total modal: *Rp {total_buy:,.0f}*"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Record buy position"""
    uid = str(update.effective_user.id)
    u = get_user(uid)

    if len(ctx.args) < 3:
        await update.message.reply_text(
            "❌ Format: `/buy [KODE] [HARGA] [LOT]`\n"
            "Contoh: `/buy BBCA 9500 100`",
            parse_mode='Markdown'
        )
        return

    ticker = ctx.args[0].upper()
    try:
        buy_price = float(ctx.args[1])
        lot = int(ctx.args[2])
    except (ValueError, TypeError, IndexError):
        await update.message.reply_text("❌ Format angka salah!")
        return

    if 'portfolio' not in u:
        u['portfolio'] = []

    from datetime import datetime
    position = {
        'ticker': ticker,
        'buy_price': buy_price,
        'lot': lot,
        'buy_date': datetime.now().strftime('%Y-%m-%d %H:%M')
    }
    u['portfolio'].append(position)
    save_user_data()

    total = buy_price * lot * 100
    await update.message.reply_text(
        f"✅ *BUY {ticker}*\n"
        f"💵 Rp {buy_price:,.0f} × {lot} lot\n"
        f"💰 Total: Rp {total:,.0f}",
        parse_mode='Markdown'
    )


async def sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Record sell position"""
    uid = str(update.effective_user.id)
    u = get_user(uid)
    portfolio = u.get('portfolio', [])

    if not ctx.args:
        await update.message.reply_text(
            "❌ Format: `/sell [KODE] [LOT]`\n"
            "Contoh: `/sell BBCA 50`",
            parse_mode='Markdown'
        )
        return

    ticker = ctx.args[0].upper()
    try:
        sell_lot = int(ctx.args[1])
    except (ValueError, TypeError, IndexError):
        await update.message.reply_text("❌ Format angka salah!")
        return

    remaining = []
    sold_total = 0
    sell_price = 0
    for pos in portfolio:
        if pos.get('ticker') == ticker and pos.get('lot', 0) >= sell_lot:
            sell_price = pos['buy_price']  # Use buy price as sell price (no current price)
            sold_total += sell_price * sell_lot * 100
            remaining_lot = pos['lot'] - sell_lot
            if remaining_lot > 0:
                pos['lot'] = remaining_lot
                remaining.append(pos)
        else:
            remaining.append(pos)

    u['portfolio'] = remaining
    save_user_data()

    if sell_price > 0:
        await update.message.reply_text(
            f"✅ *SELL {ticker}*\n"
            f"💵 Rp {sell_price:,.0f} × {sell_lot} lot\n"
            f"💰 Total: Rp {sold_total:,.0f}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"ℹ️ Tidak ada posisi {ticker}")
