"""Health/SCAN/RESET commands - debugging and maintenance."""
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from utils.rate_limiter import get_all_api_stats, reset_all_circuit_breakers, get_circuit_breaker_status
from utils.cache import _price_cache, _signal_cache
from ._shared import get_user

logger = logging.getLogger(__name__)


async def health_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check API health status"""
    msg = "🏥 *HEALTH STATUS*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    stats = get_all_api_stats()

    for api_name, api_stats in stats.items():
        breaker = api_stats.get('breaker', {})
        state = breaker.get('state', 'unknown').upper()
        state_emoji = {
            'CLOSED': '🟢',
            'OPEN': '🔴',
            'HALF_OPEN': '🟡'
        }.get(state, '⚪')

        msg += f"{state_emoji} *{api_name.upper()}*\n"
        msg += f"   State: {state}\n"
        msg += f"   Fails: {breaker.get('failure_count', 0)}/{breaker.get('failure_threshold', 0)}\n"
        msg += f"   Opens: {breaker.get('total_opens', 0)} | Closes: {breaker.get('total_closes', 0)}\n"

        if breaker.get('last_failure'):
            last_fail = datetime.fromtimestamp(breaker['last_failure']).strftime('%H:%M:%S')
            msg += f"   Last fail: {last_fail}\n"
        msg += "\n"

    msg += "📦 *CACHE*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    price_stats = _price_cache.stats()
    signal_stats = _signal_cache.stats()
    msg += f"   Price cache: {price_stats['hits']} hits, {price_stats['misses']} misses\n"
    msg += f"   Signal cache: {signal_stats['hits']} hits, {signal_stats['misses']} misses\n"

    msg += "\n👤 *USER DATA*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    uid = str(update.effective_user.id)
    u = get_user(uid)
    msg += f"   notif_saham: {u.get('notif_saham', False)}\n"
    msg += f"   notif_crypto: {u.get('notif_crypto', False)}\n"

    try:
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.debug(f"Markdown send failed, falling back to plain text: {e}")
        plain_msg = msg.replace('*', '').replace('_', ' ')
        await update.message.reply_text(plain_msg)


async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manually trigger stock scan (for testing)"""
    from handlers.scheduler import check_stock_signals

    uid = str(update.effective_user.id)
    u = get_user(uid)

    msg = "🔍 *MANUAL SCAN*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += f"notif_saham: {u.get('notif_saham', False)}\n\n"

    if not u.get('notif_saham', False):
        msg += "❌ Aktifkan dulu notif_saham di /notifikasi\n"
        await update.message.reply_text(msg, parse_mode='Markdown')
        return

    msg += "⏳ Scanning all stocks now...\n\n"
    await update.message.chat.send_action('typing')
    await update.message.reply_text(msg, parse_mode='Markdown')

    app = ctx.application if hasattr(ctx, 'application') else None
    if app:
        await check_stock_signals(app)
        await update.message.reply_text("✅ Scan completed. Check logs for results!", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Could not access bot app", parse_mode='Markdown')


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reset circuit breakers and clear stale caches"""
    await update.message.reply_text("🔄 Resetting...")

    status_before = get_circuit_breaker_status()
    reset_all_circuit_breakers()
    _price_cache._stale_cache.clear()

    msg = "🔄 *RESET COMPLETE*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += "✅ Circuit breakers: RESET\n"
    msg += "✅ Stale cache: CLEARED\n\n"

    msg += "*Before reset:*\n"
    for name, state in status_before.items():
        msg += f"   {name}: {state}\n"

    await update.message.reply_text(msg, parse_mode='Markdown')
