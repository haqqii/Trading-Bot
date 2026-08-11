"""Notification commands - /notifikasi and toggle callback."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ._shared import get_user, save_user_data, _safe_query_answer

logger = logging.getLogger(__name__)


async def notifikasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show notification settings menu"""
    uid = str(update.effective_user.id)
    u = get_user(uid)

    notif_keys = [
        ('notif_saham', '📈 Sinyal Saham', 'Sinyal TP/SL untuk saham'),
        ('notif_crypto', '₿ Sinyal Crypto', 'Sinyal 24/7 untuk crypto'),
        ('notif_bsjp', '🌙 BSJP', 'Beli Sore Jual Pagi'),
        ('notif_morning', '☀️ Sinyal Pagi', 'Watchlist rekomendasi pagi'),
        ('notif_alert_favorit', '⭐ Alert Favorit', 'Alert harga saham favorit'),
    ]

    msg = "🔔 *PENGATURAN NOTIFIKASI*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    kb = []
    for key, label, desc in notif_keys:
        is_on = u.get(key, False)
        status = "✅ ON" if is_on else "❌ OFF"
        msg += f"{status} *{label}* - {desc}\n"
        kb.append([InlineKeyboardButton(
            f"{'✅' if is_on else '❌'} {label}",
            callback_data=f"notif_{key}"
        )])

    msg += "\n_Klik tombol di bawah untuk toggle_"

    await update.message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


async def notifikasi_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle notification settings via callback"""
    query = update.callback_query
    await _safe_query_answer(query)
    notif_key = query.data.replace('notif_', '')
    uid = str(query.from_user.id)
    u = get_user(uid)

    if notif_key not in ('notif_saham', 'notif_crypto', 'notif_bsjp',
                        'notif_morning', 'notif_alert_favorit'):
        await query.edit_message_text("⚠️ Setting tidak dikenali.")
        return

    u[notif_key] = not u.get(notif_key, False)
    save_user_data()

    notif_keys = [
        ('notif_saham', '📈 Sinyal Saham'),
        ('notif_crypto', '₿ Sinyal Crypto'),
        ('notif_bsjp', '🌙 BSJP'),
        ('notif_morning', '☀️ Sinyal Pagi'),
        ('notif_alert_favorit', '⭐ Alert Favorit'),
    ]

    msg = "🔔 *PENGATURAN NOTIFIKASI*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    kb = []
    for key, label in notif_keys:
        is_on = u.get(key, False)
        status = "✅ ON" if is_on else "❌ OFF"
        msg += f"{status} *{label}*\n"
        kb.append([InlineKeyboardButton(
            f"{'✅' if is_on else '❌'} {label}",
            callback_data=f"notif_{key}"
        )])

    msg += f"\n_Update: {notif_key} = {'ON' if u[notif_key] else 'OFF'}_"

    await query.edit_message_text(
        msg,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )
