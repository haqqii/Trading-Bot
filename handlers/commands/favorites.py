"""Favorites commands - /favorit, /add, /remove."""
import logging
from telegram import Update
from telegram.ext import ContextTypes

from services.crypto_service import crypto_service
from ._shared import get_user, save_user_data

logger = logging.getLogger(__name__)


async def favorit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show favorit list (stocks and crypto)"""
    uid = str(update.effective_user.id)
    u = get_user(uid)
    favorit = u.get('favorit', {})
    crypto_favorit = u.get('crypto_favorit', {})

    if not favorit and not crypto_favorit:
        await update.message.reply_text(
            "⭐ *FAVORIT KOSONG*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 Cara menambahkan:\n"
            "`/add BBCA 5000` - BBCA target Rp 5000\n"
            "`/add BTC-USD 70000` - BTC target $70000\n"
            "`/add BBCA` - Tanpa target\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode='Markdown'
        )
        return

    msg = "⭐ *FAVORIT*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    if favorit:
        msg += "\n📈 *Saham:*\n"
        for ticker, target in favorit.items():
            target_str = f"Rp {target:,.0f}" if target else "Tanpa target"
            msg += f"• *{ticker}* - Target: {target_str}\n"

    if crypto_favorit:
        msg += "\n₿ *Crypto:*\n"
        for ticker, target in crypto_favorit.items():
            target_str = f"${target:,.2f}" if target else "Tanpa target"
            msg += f"• *{ticker}* - Target: {target_str}\n"

    msg += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "/add [KODE] [HARGA] - Tambah\n"
    msg += "/remove [KODE] - Hapus"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Add stock or crypto to favorit/alert"""
    uid = str(update.effective_user.id)
    u = get_user(uid)

    if not ctx.args:
        await update.message.reply_text("❌ /add BBCA 5000\n❌ /add BTC-USD 70000")
        return

    ticker = ctx.args[0].upper()
    target_price = None

    if len(ctx.args) > 1:
        try:
            target_price = float(ctx.args[1])
        except (ValueError, TypeError, IndexError, KeyError):
            await update.message.reply_text("❌ Harga harus angka!")
            return

    is_crypto = ticker.endswith('-USD') or ticker in crypto_service.crypto_pairs

    if is_crypto:
        if 'crypto_favorit' not in u:
            u['crypto_favorit'] = {}

        u['crypto_favorit'][ticker] = target_price
        save_user_data()

        if target_price:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan ke alert\nTarget: ${target_price:,.2f}", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan ke alert\n(Tanpa target)", parse_mode='Markdown')
    else:
        if 'favorit' not in u:
            u['favorit'] = {}

        u['favorit'][ticker] = target_price
        save_user_data()

        if target_price:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan ke favorit\nTarget: Rp {target_price:,.0f}", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan ke favorit\n(Tanpa target)", parse_mode='Markdown')


async def remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Remove stock or crypto from favorit"""
    uid = str(update.effective_user.id)
    u = get_user(uid)

    if not ctx.args:
        await update.message.reply_text("❌ /remove BBCA\n❌ /remove BTC-USD")
        return

    ticker = ctx.args[0].upper()
    removed = False

    if ticker in u.get('favorit', {}):
        del u['favorit'][ticker]
        removed = True

    if ticker in u.get('crypto_favorit', {}):
        del u['crypto_favorit'][ticker]
        removed = True

    if removed:
        save_user_data()
        await update.message.reply_text(f"✅ *{ticker}* dihapus dari favorit", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ *{ticker}* tidak ada di favorit", parse_mode='Markdown')
