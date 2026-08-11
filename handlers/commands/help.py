"""Help command - list all available commands."""
from telegram import Update
from telegram.ext import ContextTypes


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = """╔══════════════════════════════════════╗
║        📖 DAFTAR COMMAND           ║
╠══════════════════════════════════════╣
║  /start    - Mulai bot             ║
║  /harga    - Daftar harga          ║
║  /bsjp     - Beli Sore Jual Pagi  ║
║  /crypto   - Sinyal crypto         ║
║  /tf       - Pilih timeframe       ║
║  /sinyal   - Sinyal saham         ║
║  /analisa  - Analisis saham/crypto ║
║  /portfolio - Lihat portfolio      ║
║  /buy      - Catat buy             ║
║  /sell     - Catat sell            ║
║  /health   - Cek status            ║
╚══════════════════════════════════════╝"""
    await update.message.reply_text(msg, parse_mode='Markdown')
