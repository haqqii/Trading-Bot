"""Stats command - show win-rate statistics from tracked signals."""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from db import db

logger = logging.getLogger(__name__)


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show win-rate statistics from tracked signals"""

    def fmt_bar(count, total, width=10):
        if total == 0:
            return '░' * width
        filled = int(round(count / total * width))
        return '█' * filled + '░' * (width - filled)

    def fmt_stats(asset_type, label, emoji):
        s = db.get_signal_stats(asset_type)
        total = s['total']
        closed = s['closed']
        if total == 0:
            return None
        tp1 = s['tp1_count']
        tp2 = s['tp2_count']
        tp3 = s['tp3_count']
        sl = s['sl_count']
        be = s.get('beakeven_count', 0)
        trail = s.get('trailing_count', 0)
        wr = s['tp_rate']
        avg = s['avg_tp_hit']
        open_n = s.get('open', 0)

        lines = []
        lines.append(f"{emoji} *{label}*")
        lines.append(f"   📊 Total: {total} sinyal | {closed} closed | "
                     f"{open_n} belum selesai")
        if closed > 0:
            bar = fmt_bar(wr, 100)
            lines.append(f"   {bar} Win Rate: {wr}%")
            lines.append(f"   ✅ TP1: {tp1} | ✅ TP2: {tp2} | ✅ TP3: {tp3} | ❌ SL: {sl}")
            if be:
                lines.append(f"   ⚪ Stop di Entry (SL digeser ke entry): {be}")
            if trail:
                lines.append(f"   🔵 Trailing stop (SL bergerak): {trail}")
            lines.append(f"   📐 Rata-rata TP tercapai: {avg}x")
        else:
            lines.append("   ⏳ Belum ada sinyal yang ditutup")
        return '\n'.join(lines)

    stock = fmt_stats('stock', 'SAHAM', '📈')
    crypto = fmt_stats('crypto', 'CRYPTO', '₿')

    if not stock and not crypto:
        msg = ("📊 *STATISTIK SINYAL*\n\n"
               "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
               "Belum ada data statistik.\n\n"
               "Statistik akan terisi otomatis setelah bot mengirim sinyal BUY/SELL.")
    else:
        parts = ["📊 *STATISTIK SINYAL*\n", "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
        if stock:
            parts.append(stock)
        if crypto:
            parts.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n" + crypto)
        parts.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.append("_Data diperbarui otomatis saat sinyal ditutup_")
        msg = '\n'.join(parts)

    assert update.message is not None
    await update.message.reply_text(msg, parse_mode='Markdown')
