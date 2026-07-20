"""
Command handlers for Telegram bot.
"""
import asyncio
import time
import json
import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes

from services.stock_service import stock_service
from services.crypto_service import crypto_service
from services.signal_service import signal_service
from services.chart_service import chart_service
from services.news_service import news_service
from utils.formatters import TIMEFRAMES, format_signal_msg, format_crypto_msg, format_bsjp_msg, format_morning_msg, format_analisa_simple
from utils.rate_limiter import get_all_api_stats
from utils.cache import _price_cache, _signal_cache
from idx_stocks import ALL_IDX_STOCKS
from db import db

logger = logging.getLogger(__name__)


def _strip_markdown_chars(text: str) -> str:
    """Remove Telegram Markdown formatting chars for clean plain text fallback.

    Used when parse_mode='Markdown' fails — keeps the text readable instead of
    showing literal *...* / _..._ / `...` / [...] in the chat.
    """
    if not text:
        return text
    # Unescape first (so \* -> *), then strip the formatting markers
    out = text.replace('\\*', '*').replace('\\_', '_').replace('\\`', '`').replace('\\[', '[')
    for ch in ('*', '_', '`', '['):
        out = out.replace(ch, '')
    return out


# Global state
ALL_STOCKS = ALL_IDX_STOCKS
user_data_db = {}
last_signal_sent = {}
last_prices = {}
last_crypto_prices = {}
last_buy_signals = {}

# Persistence
USER_DATA_FILE = 'user_data.json'
SIGNALS_FILE = 'last_signals.json'


def load_user_data():
    """Load user data from SQLite database (with JSON migration fallback)"""
    global user_data_db, last_buy_signals
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"[LOAD_USER] Starting load_user_data(), current user_data_db has {len(user_data_db)} users")

    # Initialize SQLite database
    db.initialize()
    stats = db.stats()

    # If database is empty but JSON files exist, migrate
    if stats['users'] == 0 and (os.path.exists(USER_DATA_FILE) or os.path.exists(SIGNALS_FILE)):
        logger.info("[LOAD_USER] SQLite empty, attempting JSON migration...")
        from db import migrate_from_json
        migrated = migrate_from_json(USER_DATA_FILE, SIGNALS_FILE)
        if migrated:
            logger.info("[LOAD_USER] Migration complete")
        stats = db.stats()

    # Load all users into global dict (legacy compatibility)
    try:
        user_data_db.clear()
        # Get all users - we'd need to add a method for this, but for now
        # load from DB only when accessed via get_user_data()
        # Keep user_data_db as a cache for backward compatibility
        logger.info(f"[LOAD_USER] Database ready: {stats['users']} users, "
                    f"{stats['signals']} signals, {stats['favorites']} favorites")
    except Exception as e:
        logger.error(f"Error loading user data: {e}")

    # Load signals into global dict (for BSJP tracking)
    try:
        last_buy_signals.clear()
        signals_data = db.get_all_signals()
        for key, signal in signals_data.items():
            # Convert created_at string back to datetime
            if 'created_at' in signal and isinstance(signal['created_at'], str):
                try:
                    signal['time'] = datetime.fromisoformat(signal['created_at'])
                except (ValueError, TypeError):
                    signal['time'] = datetime.now()
            last_buy_signals[key] = signal
        logger.info(f"[LOAD_USER] Loaded {len(last_buy_signals)} signals from DB")
    except Exception as e:
        logger.error(f"Error loading signals: {e}")


def get_user_data(user_id: int) -> dict:
    """Get user data dict for a specific user (loads from SQLite)."""
    user = db.get_user(user_id)
    if not user:
        return {}
    # Convert boolean-like int back to bool for notification keys
    result = dict(user)
    for k in ['notif_saham', 'notif_crypto', 'notif_bsjp',
              'notif_morning', 'notif_alert_favorit']:
        if k in result:
            result[k] = bool(result[k])
    # Add favorites list
    result['favorites'] = [f['ticker'] for f in db.get_favorites(user_id)]
    return result


def get_user_data_db() -> dict:
    """Legacy function - returns in-memory user_data_db cache.
    Note: For new code, use get_user_data(user_id) instead."""
    return user_data_db


def _atomic_write(filepath: str, data: dict):
    """
    Atomically write data to file using temp file + rename pattern.
    Also creates a backup before writing.
    """
    import tempfile
    import shutil

    # Create backup of existing file
    if os.path.exists(filepath):
        backup_path = filepath + '.bak'
        try:
            shutil.copy2(filepath, backup_path)
        except Exception as e:
            logger.warning(f"Backup write failed for {filepath}: {e}")

    # Write to temp file
    temp_fd, temp_path = tempfile.mkstemp(suffix='.tmp', dir=os.path.dirname(filepath) or '.')
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Ensure data written to disk

        # Rename temp to target (atomic on POSIX, near-atomic on Windows)
        os.replace(temp_path, filepath)
        logger.debug(f"Atomically saved {filepath}")
        return True

    except Exception as e:
        logger.error(f"Error writing {filepath}: {e}")
        # Try to remove temp file
        try:
            os.unlink(temp_path)
        except Exception as cleanup_err:
            logger.warning(f"Failed to remove temp file {temp_path}: {cleanup_err}")
        # Restore from backup
        backup_path = filepath + '.bak'
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, filepath)
                logger.warning(f"Restored {filepath} from backup")
            except Exception as restore_err:
                logger.error(f"Backup restore also failed for {filepath}: {restore_err}")
        return False


def save_user_data():
    """Save user data and signals to SQLite (atomic, crash-safe)."""
    db.initialize()

    # Save user data (legacy cache → DB)
    for user_id_str, user_info in user_data_db.items():
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            continue
        # Upsert user
        db.upsert_user(
            user_id=user_id,
            username=user_info.get('username'),
            first_name=user_info.get('first_name')
        )
        # Update notifications
        notif_settings = {
            k: bool(user_info.get(k, False))
            for k in ['notif_saham', 'notif_crypto', 'notif_bsjp',
                      'notif_morning', 'notif_alert_favorit']
        }
        db.update_notifications(user_id, **notif_settings)
        # Sync favorites
        for ticker in user_info.get('favorites', []):
            db.add_favorite(user_id, ticker)

    # Save signals
    for key, val in last_buy_signals.items():
        ticker = val.get('ticker', 'UNKNOWN')
        signal_type = val.get('signal_type', val.get('signal', 'BUY'))
        asset_type = val.get('asset_type', 'stock')
        db.save_signal(
            key=key,
            ticker=ticker,
            asset_type=asset_type,
            signal_type=signal_type,
            price=val.get('price') or val.get('entry'),
            target_price=val.get('tp') or val.get('tp1') or val.get('target_price'),
            stop_loss=val.get('sl') or val.get('stop_loss'),
            score=val.get('score'),
            quality=val.get('quality'),
            reason=val.get('reason'),
            extra_data={k: v for k, v in val.items()
                        if k not in ['ticker', 'signal_type', 'asset_type',
                                      'price', 'target_price', 'stop_loss',
                                      'score', 'quality', 'reason',
                                      'entry', 'tp', 'tp1', 'sl', 'time']}
        )


def save_signal(key: str, ticker: str, signal_type: str,
                asset_type: str = 'stock', price: float = None,
                target_price: float = None, stop_loss: float = None,
                score: float = None, quality: str = None,
                reason: str = None, extra_data: dict = None):
    """Convenience function to save a single signal."""
    db.initialize()
    db.save_signal(
        key=key,
        ticker=ticker,
        signal_type=signal_type,
        asset_type=asset_type,
        price=price,
        target_price=target_price,
        stop_loss=stop_loss,
        score=score,
        quality=quality,
        reason=reason,
        extra_data=extra_data
    )


def get_user(user_id):
    """Get or create user data"""
    if user_id not in user_data_db:
        user_data_db[user_id] = {
            'watchlist': [],
            'crypto_watchlist': [],
            'portfolio': [],
            'notifications': True,
            'notif_saham': False,
            'notif_crypto': False,
            'notif_bsjp': False,
            'notif_morning': False,
            'notif_watchlist': False,
            'favorit': {},
            'crypto_favorit': {},
            'timeframe': '5',
            'alerts': {},
            'subscribed_at': datetime.now().isoformat()
        }
    return user_data_db[user_id]


# === START ===
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    u = get_user(uid)

    kb = [
        ["📊 Harga", "🎯 Sinyal"],
        ["⭐ Favorit", "🌙 BSJP"],
        ["💼 Portfolio", "🔔 Notifikasi"],
        ["⏱️ TF", "₿ Crypto"]
    ]
    rm = ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=False)

    tf_name = TIMEFRAMES[u.get('timeframe', '5')]['name']
    notif_status = "🔔 AKTIF" if u.get('notifications') else "🔕 NONAKTIF"

    msg = f"""🤖 *IDX SAHAM BOT*

━━━━━━━━━━━━━━━━━━━━━━━━━━
👋 Halo {user.first_name}!

📊 Saham: *{len(ALL_STOCKS)}*
⏱️ Timeframe: {tf_name}
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
⏱️ TF - Ganti timeframe

━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ Trading risiko tanggung sendiri"""

    await update.message.reply_text(msg, reply_markup=rm, parse_mode='Markdown')


# === HARGA ===
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


# === TIMEFRAME ===
async def tf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)
    curr = u.get('timeframe', '5')

    kb = [[InlineKeyboardButton(f"{'✅ ' if k==curr else ''}{v['name']}", callback_data=f"tf_{k}")]
          for k, v in TIMEFRAMES.items()]

    await update.message.reply_text("⏱️ PILIH TIMEFRAME", reply_markup=InlineKeyboardMarkup(kb))


async def tf_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tf_key = query.data.replace('tf_', '')
    uid = str(query.from_user.id)
    get_user(uid)['timeframe'] = tf_key
    await query.edit_message_text(f"✅ Timeframe: {TIMEFRAMES[tf_key]['name']}")


# === FAVORIT ===
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
        except (ValueError, TypeError, IndexError):
            await update.message.reply_text("❌ Harga harus angka!")
            return

    # Check if it's a crypto ticker
    is_crypto = ticker.endswith('-USD') or ticker in crypto_service.crypto_pairs

    if is_crypto:
        # Crypto alert
        if 'crypto_favorit' not in u:
            u['crypto_favorit'] = {}

        u['crypto_favorit'][ticker] = target_price
        save_user_data()

        if target_price:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan ke alert\nTarget: ${target_price:,.2f}", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan ke alert\n(Tanpa target)", parse_mode='Markdown')
    else:
        # Stock favorit
        if 'favorit' not in u:
            u['favorit'] = {}

        u['favorit'][ticker] = target_price
        save_user_data()

        if target_price:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan\nTarget: Rp {target_price:,.0f}", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"✅ *{ticker}* ditambahkan\n(Tanpa target)", parse_mode='Markdown')


async def remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Remove stock or crypto from favorit/alert"""
    uid = str(update.effective_user.id)
    u = get_user(uid)

    if not ctx.args:
        await update.message.reply_text("❌ /remove BBCA\n❌ /remove BTC-USD")
        return

    ticker = ctx.args[0].upper()

    # Check if it's a crypto ticker
    is_crypto = ticker.endswith('-USD') or ticker in crypto_service.crypto_pairs

    if is_crypto:
        # Remove from crypto alerts
        crypto_favorit = u.get('crypto_favorit', {})

        if ticker in crypto_favorit:
            del crypto_favorit[ticker]
            save_user_data()
            await update.message.reply_text(f"✅ *{ticker}* dihapus dari alert crypto", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"ℹ️ *{ticker}* tidak ada di alert crypto", parse_mode='Markdown')
    else:
        # Remove from stock favorit
        favorit = u.get('favorit', {})

        if ticker in favorit:
            del favorit[ticker]
            save_user_data()
            await update.message.reply_text(f"✅ *{ticker}* dihapus dari favorit", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"ℹ️ *{ticker}* tidak ada di favorit", parse_mode='Markdown')


# === NOTIFIKASI ===
async def notifikasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show and manage notification settings"""
    uid = str(update.effective_user.id)
    u = get_user(uid)

    def status_emoji(val):
        return "✅" if val else "❌"

    keyboard = [
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_saham'))} Sinyal Saham", callback_data="notif_saham")],
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_crypto'))} Sinyal Crypto", callback_data="notif_crypto")],
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_bsjp'))} BSJP", callback_data="notif_bsjp")],
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_morning'))} Sinyal Pagi", callback_data="notif_morning")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = """🔔 *PENGATURAN NOTIFIKASI*

━━━━━━━━━━━━━━━━━━━━━━━━━━
Aktifkan notifikasi yang diinginkan:

• *Sinyal Saham* - Sinyal BUY saham IDX
• *Sinyal Crypto* - Sinyal BUY crypto 24/7
• *BSJP* - Sinyal Beli Sore Jual Pagi
• *Sinyal Pagi* - Rekomendasi pagi hari

━━━━━━━━━━━━━━━━━━━━━━━━━━
Klik tombol untuk toggle ON/OFF"""

    await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


async def notifikasi_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Toggle notification settings via callback"""
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    u = get_user(uid)

    notif_key = query.data.replace('notif_', '')
    if notif_key in ('saham', 'crypto', 'bsjp', 'morning'):
        u[f'notif_{notif_key}'] = not u.get(f'notif_{notif_key}', False)
        save_user_data()

    def status_emoji(val):
        return "✅" if val else "❌"

    keyboard = [
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_saham'))} Sinyal Saham", callback_data="notif_saham")],
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_crypto'))} Sinyal Crypto", callback_data="notif_crypto")],
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_bsjp'))} BSJP", callback_data="notif_bsjp")],
        [InlineKeyboardButton(f"{status_emoji(u.get('notif_morning'))} Sinyal Pagi", callback_data="notif_morning")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        """🔔 *PENGATURAN NOTIFIKASI*

━━━━━━━━━━━━━━━━━━━━━━━━━━
Aktifkan notifikasi yang diinginkan:

• *Sinyal Saham* - Sinyal BUY saham IDX
• *Sinyal Crypto* - Sinyal BUY crypto 24/7
• *BSJP* - Sinyal Beli Sore Jual Pagi
• *Sinyal Pagi* - Rekomendasi pagi hari

━━━━━━━━━━━━━━━━━━━━━━━━━━
Klik tombol untuk toggle ON/OFF""",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


# === PORTFOLIO ===
async def portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)
    pf = u.get('portfolio', [])

    if not pf:
        await update.message.reply_text("💼 Portfolio kosong\n\n/buy BBCA 8500 10")
        return

    await update.message.chat.send_action('typing')
    await update.message.reply_text("💼 Mengambil data portfolio...")

    msg = "💼 PORTFOLIO\n" + "="*30 + "\n\n"
    for p in pf:
        d = stock_service.get_stock_data_combined(p['ticker'] + ".JK")
        if d:
            pnl = (d['price'] - p['buy_price']) * p['lot'] * 100
            emoji = "🟢" if pnl >= 0 else "🔴"
            msg += f"{emoji} {p['ticker']}\n"
            msg += f"   Buy: {p['buy_price']} | Current: {d['price']:,.0f}\n"
            msg += f"   Lot: {p['lot']} | P/L: {pnl:,.0f}\n\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)

    if len(ctx.args) < 3:
        await update.message.reply_text("❌ /buy BBCA 8500 10")
        return

    try:
        t = ctx.args[0].upper()
        price = float(ctx.args[1])
        lot = int(ctx.args[2])
        u.setdefault('portfolio', []).append({'ticker': t, 'buy_price': price, 'lot': lot})
        await update.message.reply_text(f"✅ Buy {t} @ {price:,} | Lot {lot}")
    except (ValueError, TypeError, IndexError, KeyError):
        await update.message.reply_text("❌ Format salah!")


async def sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)

    if len(ctx.args) < 2:
        await update.message.reply_text("❌ /sell BBCA 5")
        return

    try:
        t = ctx.args[0].upper()
        lot = int(ctx.args[1])
        pf = u.get('portfolio', [])

        await update.message.chat.send_action('typing')
        await update.message.reply_text(f"💰 Mengambil harga {t}...")

        for i, p in enumerate(pf):
            if p['ticker'] == t:
                d = stock_service.get_stock_data_combined(t + ".JK")
                current = d['price'] if d else p['buy_price']
                pnl = (current - p['buy_price']) * lot * 100

                if p['lot'] <= lot:
                    pf.pop(i)
                else:
                    pf[i]['lot'] -= lot

                await update.message.reply_text(f"✅ Sell {t} {lot} lot @ {current:,.0f}\nP/L: {pnl:,.0f}")
                return

        await update.message.reply_text(f"ℹ️ Tidak ada posisi {t}")
    except (ValueError, TypeError, IndexError, KeyError):
        await update.message.reply_text("❌ Format salah!")


# === CRYPTO ===
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


# === BSJP ===
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


# === MORNING ===
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


# === HEALTH CHECK ===
async def health_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Check API health status"""
    from datetime import datetime

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

    # Cache stats
    msg += "📦 *CACHE*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    price_stats = _price_cache.stats()
    signal_stats = _signal_cache.stats()
    msg += f"   Price cache: {price_stats['hits']} hits, {price_stats['misses']} misses\n"
    msg += f"   Signal cache: {signal_stats['hits']} hits, {signal_stats['misses']} misses\n"

    # User data status
    msg += "\n👤 *USER DATA*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    uid = str(update.effective_user.id)
    u = get_user(uid)
    msg += f"   notif_saham: {u.get('notif_saham', False)}\n"
    msg += f"   notif_crypto: {u.get('notif_crypto', False)}\n"

    # Show saved file status
    import os
    user_file = 'user_data.json'
    if os.path.exists(user_file):
        import json
        with open(user_file, 'r') as f:
            all_users = json.load(f)
        saved_user = all_users.get(uid, {})
        msg += f"\n💾 *SAVED DATA*\n"
        msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"   notif_saham: {saved_user.get('notif_saham', False)}\n"
    else:
        msg += f"\n❌ user_data.json not found\n"

    # Try markdown, fallback to plain text if error
    try:
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.debug(f"Markdown send failed, falling back to plain text: {e}")
        plain_msg = msg.replace('*', '').replace('_', ' ')
        await update.message.reply_text(plain_msg)


# === DEBUG/SCAN COMMAND ===
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

    # Get app from context or use global
    app = ctx.application if hasattr(ctx, 'application') else None
    if app:
        await check_stock_signals(app)
        await update.message.reply_text("✅ Scan completed. Check logs for results!", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Could not access bot app", parse_mode='Markdown')


# === RESET COMMAND ===
async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reset circuit breakers and clear stale caches"""
    from utils.rate_limiter import reset_all_circuit_breakers, get_circuit_breaker_status

    # Quick reply first
    await update.message.reply_text("🔄 Resetting...")

    status_before = get_circuit_breaker_status()

    # Reset all circuit breakers
    reset_all_circuit_breakers()

    # Clear stale cache
    _price_cache._stale_cache.clear()

    msg = "🔄 *RESET COMPLETE*\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    msg += "✅ Circuit breakers: RESET\n"
    msg += "✅ Stale cache: CLEARED\n\n"

    msg += "*Before reset:*\n"
    for name, state in status_before.items():
        msg += f"   {name}: {state}\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


# === HELP ===
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
╚══════════════════════════════════════╝

⚠️ Disclaimer: Trading risiko
tanggung sendiri"""
    await update.message.reply_text(msg)


# === CHART ===
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

    # Check if it's crypto (has -USD, -USDT suffix) or stock (IDX)
    is_crypto = (
        ticker.endswith('-USD') or
        ticker.endswith('-USDT') or
        ticker in crypto_service.crypto_pairs
    )

    # Auto-detect common crypto symbols
    common_crypto = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'WLD', 'SUI', 'APT', 'ARB', 'OP', 'MATIC', 'AVAX', 'LINK', 'DOT', 'UNI', 'ATOM', 'LTC']
    if ticker in common_crypto and not ticker.endswith('-USD'):
        is_crypto = True
        ticker = ticker + '-USD'

    await update.message.chat.send_action('typing')
    await update.message.reply_text(f"📊 Generating chart for `{ticker}`...", parse_mode='Markdown')

    try:
        if is_crypto:
            img_buf = chart_service.generate_crypto_chart(ticker, interval=interval, period=period)
        else:
            # Stock - add .JK suffix if not present
            if not ticker.endswith('.JK'):
                full_ticker = ticker + '.JK'
            else:
                full_ticker = ticker
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


# === ANALISA COMMAND ===
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

    # Reply immediately to user - send this FIRST before any processing
    # Use short timeout so it doesn't block the analysis
    try:
        await update.message.reply_text(
            f"📊 Menganalisis `{ticker}`...\n\n⏳ Mengambil data dan analisis...",
            parse_mode='Markdown',
            read_timeout=10,
            write_timeout=10
        )
    except Exception as reply_err:
        logger.debug(f"[ANALISA] Immediate reply failed: {reply_err}")

    try:
        # Load crypto pairs if not loaded
        if not crypto_service.crypto_pairs:
            crypto_service.load_crypto_pairs()

        # Check if it's a crypto ticker - auto detect
        ticker_upper = ticker.upper()

        # IMPORTANT: If ticker is in IDX stocks database, ALWAYS treat as stock
        # This prevents conflicts like COCO (both IDX stock and crypto exist)
        if ticker_upper in ALL_STOCKS:
            is_crypto = False
        else:
            # Check patterns that indicate crypto
            is_crypto = (
                ticker_upper in crypto_service.crypto_pairs or  # In CoinGecko list (e.g. BEAT-USD)
                (ticker_upper + '-USD') in crypto_service.crypto_pairs or  # Symbol only (e.g. BEAT)
                (ticker_upper + '-USDT') in crypto_service.crypto_pairs or  # Symbol only USDT variant
                ticker_upper in crypto_service.coingecko_ids or  # In CoinGecko IDs
                ticker.endswith('-USD') or  # Already has -USD suffix
                ticker.endswith('-USDT') or  # USDT pair
                ticker.endswith('-BTC') or  # BTC pair
                ticker.endswith('-ETH') or  # ETH pair
                ticker_upper in ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'WLD', 'SUI', 'APT', 'ARB', 'OP', 'MATIC', 'AVAX', 'LINK', 'DOT', 'UNI', 'ATOM', 'LTC', 'WORLDCOIN', 'PEPE', 'SHIB', 'FIL', 'NEAR', 'AAVE', 'GRT', 'VET', 'ALGO', 'ICP', 'EGLD', 'AXS', 'MANA', 'SAND', 'GALA', 'ENJ']  # Common crypto symbols
            )

        # Fallback probe: if ticker unknown and not an IDX stock, ask Yahoo Finance
        # (covers coins outside CoinGecko top-1250 list, e.g. MYX Finance, BEAT, etc.)
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
            # Try to find full ticker in CoinGecko pairs
            full_ticker = ticker
            for ct in crypto_service.crypto_pairs.keys():
                if ticker.upper() in ct.upper() or ct.startswith(ticker.upper() + '-'):
                    full_ticker = ct
                    break

            # If not found, construct USD pair
            if full_ticker == ticker:
                if not ticker.endswith('-USD') and not ticker.endswith('-USDT'):
                    # Add -USD suffix for common crypto symbols (up to 10 chars for names like WORLDCOIN)
                    if len(ticker) <= 10:
                        full_ticker = ticker.upper() + '-USD'

            # Verify ticker exists in our crypto database
            ticker_known = (
                ticker_upper in crypto_service.crypto_pairs
                or (ticker_upper + '-USD') in crypto_service.crypto_pairs
                or (ticker_upper + '-USDT') in crypto_service.crypto_pairs
                or full_ticker in crypto_service.crypto_pairs
                or yahoo_probe is not None  # Yahoo probe counts as known
            )

            # Get crypto data - use 5d period to get enough candles for indicators
            if yahoo_probe is not None:
                # Reuse the Yahoo probe result (avoid 2nd API call)
                d = yahoo_probe
            else:
                try:
                    d = crypto_service.get_crypto_data_combined(full_ticker, '1h', '5d')
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
                d = crypto_service.get_crypto_data_combined(alt_ticker, '1h', '5d')

            # Fallback: try with -USDT suffix
            if not d and full_ticker.endswith('-USD'):
                alt_ticker = full_ticker.replace('-USD', '-USDT')
                d = crypto_service.get_crypto_data_combined(alt_ticker, '1h', '5d')

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
                        f"❌ Gagal mengambil data untuk `{ticker}`\n\n"
                        f"Ticker dikenal di database, tapi CoinGecko & Yahoo Finance keduanya tidak merespon.\n"
                        f"Mungkin API sedang down atau kena rate limit. Coba lagi dalam 1-2 menit.",
                        parse_mode='Markdown'
                    )
                return

            # Check if data has enough candles
            if d.get('candles', 0) < 50:
                source = d.get('source', 'unknown')
                await update.message.reply_text(
                    f"❌ Data `{ticker}` tidak cukup untuk dianalisis\n\n"
                    f"Hanya tersedia {d.get('candles', 0)} candle (minimum 50) dari sumber `{source}`.\n"
                    f"Crypto ini mungkin baru listing atau volume sangat rendah.",
                    parse_mode='Markdown'
                )
                return

            # Generate signal
            s = signal_service.generate_crypto_signal(d)
            if s is None:
                s = {'signal': 'HOLD', 'entry': d.get('price') if d else 0}

            # Fetch news and sentiment for crypto (run in thread to not block)
            clean_ticker = ticker.replace('-USD', '').replace('-USDT', '').upper()
            sentiment = None
            try:
                result = await asyncio.to_thread(news_service.get_crypto_news, clean_ticker)
                if result and len(result) >= 2:
                    articles, sentiment = result[0], result[1]
            except Exception as e:
                logger.warning(f"Failed to fetch crypto news for {clean_ticker}: {e}")

            # Use clean format with sentiment
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

        else:
            # Stock analysis - use combined method for fallback support
            full_ticker = ticker + ".JK"
            ticker_known = ticker in ALL_STOCKS
            name = ALL_STOCKS.get(ticker, ticker)

            # Run stock data fetch and news fetch in PARALLEL to speed up response
            d, sentiment = None, None

            def fetch_stock_data():
                try:
                    # Use 1d period for faster fetching (78 candles at 5m interval is enough for RSI14)
                    return stock_service.get_stock_data_combined(full_ticker, '5m', '1d')
                except Exception as e:
                    logger.error(f"[ANALISA] Stock fetch error for {full_ticker}: {e}")
                    return None

            async def fetch_news_async():
                try:
                    result = await asyncio.to_thread(news_service.get_stock_news, ticker, name)
                    if result and len(result) >= 2:
                        return result[1]
                except Exception as e:
                    logger.warning(f"[ANALISA] News fetch failed for {ticker}: {e}")
                return None

            # Execute stock fetch and news fetch in parallel
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                stock_future = executor.submit(fetch_stock_data)
                news_coro = fetch_news_async()

                # Wait for stock data first (needed for analysis)
                d = stock_future.result()

                # Also wait for news (may be slower but doesn't block result)
                sentiment = await news_coro

            if not d:
                if not ticker_known:
                    await update.message.reply_text(
                        f"❌ Saham `{ticker}` tidak ditemukan\n\n"
                        f"Pastikan kode saham benar (4 huruf, contoh: BBCA, TLKM, BMRI).\n"
                        f"Saham yang tidak ada di database IDX mungkin tidak bisa dianalisis.\n\n"
                        f"ℹ️ Ini command untuk saham. Untuk crypto gunakan:\n"
                        f"`/analisa BTC` atau `/analisa BEAT`",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Gagal mengambil data untuk `{ticker}`\n\n"
                        f"Saham ada di database IDX, tapi Yahoo Finance & TradingView keduanya tidak merespon.\n"
                        f"Mungkin API sedang down atau kena rate limit. Coba lagi dalam 1-2 menit.",
                        parse_mode='Markdown'
                    )
                return

            if d.get('candles', 0) < 50:
                source = d.get('source', 'unknown')
                await update.message.reply_text(
                    f"❌ Data saham `{ticker}` tidak cukup untuk dianalisis\n\n"
                    f"Hanya tersedia {d.get('candles', 0)} candle (minimum 50) dari sumber `{source}`.\n"
                    f"Coba lagi saat jam pasar (09:00-15:00 WIB) atau saham baru listing.",
                    parse_mode='Markdown'
                )
                return

            # Generate signal
            s = signal_service.generate_stock_signal(d)
            if s is None:
                s = {'signal': 'HOLD', 'entry': d.get('price') if d else 0}
            logger.info(f"[ANALISA] Signal generated: {s.get('signal')}")
            msg = format_analisa_simple(
                ticker=ticker,
                name=name,
                data=d,
                signal=s,
                sentiment=sentiment,
                is_crypto=False
            )

        logger.info(f"[ANALISA] Sending final result for {ticker}")
        try:
            await update.message.reply_text(msg, parse_mode='Markdown', read_timeout=120, write_timeout=120)
            logger.info(f"[ANALISA] Done for {ticker}")
        except Exception as send_err:
            err_str = str(send_err)
            logger.error(f"[ANALISA] Markdown send failed ({err_str[:80]}), retrying as plain text")
            sanitized = _strip_markdown_chars(msg)
            try:
                await update.message.reply_text(sanitized, read_timeout=120, write_timeout=120)
                logger.info(f"[ANALISA] Done for {ticker} (plain text fallback)")
            except Exception as plain_err:
                logger.error(f"[ANALISA] Plain text send also failed: {plain_err}")
                try:
                    if reply_msg:
                        await reply_msg.edit_text((sanitized or msg)[:3500] + "\n\n⚠️ _Gagal kirim hasil_", parse_mode='Markdown')
                except Exception as edit_err:
                    logger.error(f"[ANALISA] edit_text fallback also failed: {edit_err}")

    except Exception as e:
        logger.error(f"Analisa error: {e}", exc_info=True)
        try:
            await update.message.reply_text(f"❌ Error: {str(e)[:300]}", read_timeout=60, write_timeout=60)
        except Exception as reply_err:
            logger.error(f"[ANALISA] Error reply also failed: {reply_err}")


# === BUTTONS ===
async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    handlers = {
        "📊 Harga": harga,
        "🎯 Sinyal": morning_watchlist,
        "⭐ Favorit": favorit,
        "⏱️ TF": tf,
        "🌙 BSJP": bsjp,
        "💼 Portfolio": portfolio,
        "🔔 Notifikasi": notifikasi,
        "₿ Crypto": crypto,
    }
    if update.message.text in handlers:
        await handlers[update.message.text](update, ctx)


# === REGISTER ALL HANDLERS ===
def register_handlers(app):
    """Register all command handlers to the application"""
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters

    # Main commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["harga", "price"], harga))
    app.add_handler(CommandHandler(["tf", "timeframe"], tf))
    app.add_handler(CommandHandler(["favorit", "fav"], favorit))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler(["portfolio", "pf"], portfolio))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("crypto", crypto))
    app.add_handler(CommandHandler("bsjp", bsjp))
    app.add_handler(CommandHandler(["morning", "pagi", "sinyal"], morning_watchlist))
    app.add_handler(CommandHandler(["help", "bantuan"], help_cmd))
    app.add_handler(CommandHandler(["notifikasi", "notif"], notifikasi))
    app.add_handler(CommandHandler(["chart", "c"], chart_cmd))
    app.add_handler(CommandHandler(["health", "status"], health_cmd))
    app.add_handler(CommandHandler(["scan", "test"], scan_cmd))
    app.add_handler(CommandHandler(["reset", "rst"], reset_cmd))
    app.add_handler(CommandHandler(["analisa", "analisis", "analysis"], analisa_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(tf_cb, pattern=r"tf_"))
    app.add_handler(CallbackQueryHandler(notifikasi_cb, pattern=r"notif_"))

    # Message handler for buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buttons))

    # Error handler
    app.add_handler(CommandHandler("error", lambda u, c: logger.error(f"Error: {c.error}")))
