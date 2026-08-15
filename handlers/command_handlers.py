"""
Command handlers for Telegram bot.
"""
import asyncio
import time
from datetime import datetime, timezone, timedelta
import json
import os
import logging
import concurrent.futures
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes

from services.stock_service import stock_service
from services.crypto_service import crypto_service
from services.signal_service import signal_service
from services.chart_service import chart_service
from services.news_service import news_service
from utils.formatters import TIMEFRAMES, format_signal_msg, format_crypto_msg, format_bsjp_msg, format_morning_msg, format_analisa_simple, format_analisa_pemula
from config.settings import INTERVAL_TO_KEY, VALID_INTERVALS
from utils.rate_limiter import get_all_api_stats
from utils.cache import _price_cache, _signal_cache
from data.idx_stocks import ALL_IDX_STOCKS
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


async def _send_with_retry(message, text, retries=3, delay=2, **kwargs):
    """Send message with retry on timeout. Returns True if successful."""
    from telegram.error import TimedOut
    for attempt in range(retries):
        try:
            await asyncio.wait_for(
                message.reply_text(text, **kwargs),
                timeout=30
            )
            return True
        except TimedOut:
            if attempt < retries - 1:
                logger.warning(f"Send timeout, retrying in {delay}s ({attempt+1}/{retries})")
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                logger.error(f"Send failed after {retries} attempts")
                return False
        except Exception as e:
            logger.error(f"Send failed: {e}")
            return False
    return False


async def _safe_query_answer(query, retries=2, delay=0.5):
    """Acknowledge callback query with soft timeout handling.

    query.answer() tells Telegram to remove the loading spinner. If the
    request to Telegram times out, we don't want to crash the whole
    handler — the user just sees the spinner a bit longer. Log and move on.
    """
    from telegram.error import TimedOut
    for attempt in range(retries):
        try:
            await asyncio.wait_for(query.answer(), timeout=5)
            return True
        except (TimedOut, asyncio.TimeoutError):
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            else:
                logger.warning(f"query.answer() timed out after {retries} attempts")
        except Exception as e:
            logger.warning(f"query.answer() failed: {e}")
            return False
    return False


# Global state
ALL_STOCKS = ALL_IDX_STOCKS
user_data_db = {}
last_signal_sent = {}
last_prices = {}
last_crypto_prices = {}
last_buy_signals = {}

# Persistence
USER_DATA_FILE = 'user_data.json'  # Legacy - kept for migration reference
SIGNALS_FILE = 'last_signals.json'  # Legacy - kept for migration reference


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

    # Load all users into global dict (legacy compatibility for scheduler)
    try:
        user_data_db.clear()
        # Query all users from DB and populate user_data_db cache
        import sqlite3
        # Use db.DATA_DIR or fallback to local data/ folder
        db_path = db.db_path if hasattr(db, 'db_path') else 'data/ochobot.db'
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            'SELECT user_id, username, first_name, notif_saham, notif_crypto, '
            'notif_bsjp, notif_morning, notif_alert_favorit FROM users'
        ).fetchall():
            user_id_str = str(row['user_id'])
            user_data_db[user_id_str] = {
                'username': row['username'],
                'first_name': row['first_name'],
                'notif_saham': bool(row['notif_saham']),
                'notif_crypto': bool(row['notif_crypto']),
                'notif_bsjp': bool(row['notif_bsjp']),
                'notif_morning': bool(row['notif_morning']),
                'notif_alert_favorit': bool(row['notif_alert_favorit']),
                'favorites': [f['ticker'] for f in db.get_favorites(row['user_id'])],
            }
        conn.close()
        logger.info(f"[LOAD_USER] Loaded {len(user_data_db)} users into cache for scheduler")
    except Exception as e:
        logger.error(f"Error loading user data: {e}")

    # Load active (open) signals into global dict for TP/SL tracking
    # This restores signal tracking state after bot restart
    try:
        last_buy_signals.clear()
        active_signals = db.load_active_signals()
        last_buy_signals.update(active_signals)
        logger.info(f"[LOAD_USER] Loaded {len(last_buy_signals)} active signals from DB")
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
        # Bug fix: signal dict uses 'type' not 'asset_type'. Fall back to key prefix.
        asset_type = val.get('asset_type') or val.get('type')
        if not asset_type:
            asset_type = 'crypto' if key.startswith('CRYPTO_') else 'stock'
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
    """Start command. See handlers/commands/start.py"""
    from handlers.commands.start import start as _start
    return await _start(update, ctx)


async def harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Harga command. See handlers/commands/start.py"""
    from handlers.commands.start import harga as _harga
    return await _harga(update, ctx)


async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route button text. See handlers/commands/start.py"""
    from handlers.commands.start import buttons as _buttons
    return await _buttons(update, ctx)




async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show win-rate statistics from tracked signals. See handlers/commands/stats.py"""
    from handlers.commands.stats import stats_cmd as _stats_cmd
    return await _stats_cmd(update, ctx)


# === REGISTER ALL HANDLERS ===
async def tf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Timeframe menu. See handlers/commands/timeframe.py"""
    from handlers.commands.timeframe import tf as _tf
    return await _tf(update, ctx)


async def tf_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Timeframe category callback. See handlers/commands/timeframe.py"""
    from handlers.commands.timeframe import tf_cat_cb as _tf_cat_cb
    return await _tf_cat_cb(update, ctx)


async def tf_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Timeframe selection callback. See handlers/commands/timeframe.py"""
    from handlers.commands.timeframe import tf_cb as _tf_cb
    return await _tf_cb(update, ctx)


async def favorit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show favorites. See handlers/commands/favorites.py"""
    from handlers.commands.favorites import favorit as _favorit
    return await _favorit(update, ctx)


async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Add favorite. See handlers/commands/favorites.py"""
    from handlers.commands.favorites import add as _add
    return await _add(update, ctx)


async def remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Remove favorite. See handlers/commands/favorites.py"""
    from handlers.commands.favorites import remove as _remove
    return await _remove(update, ctx)


async def notifikasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Notification settings. See handlers/commands/notifications.py"""
    from handlers.commands.notifications import notifikasi as _notifikasi
    return await _notifikasi(update, ctx)


async def notifikasi_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Notification toggle callback. See handlers/commands/notifications.py"""
    from handlers.commands.notifications import notifikasi_cb as _notifikasi_cb
    return await _notifikasi_cb(update, ctx)


async def portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show portfolio. See handlers/commands/portfolio.py"""
    from handlers.commands.portfolio import portfolio as _portfolio
    return await _portfolio(update, ctx)


async def buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Record buy. See handlers/commands/portfolio.py"""
    from handlers.commands.portfolio import buy as _buy
    return await _buy(update, ctx)


async def sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Record sell. See handlers/commands/portfolio.py"""
    from handlers.commands.portfolio import sell as _sell
    return await _sell(update, ctx)


async def crypto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Crypto signals. See handlers/commands/signals.py"""
    from handlers.commands.signals import crypto as _crypto
    return await _crypto(update, ctx)


async def bsjp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """BSJP signals. See handlers/commands/signals.py"""
    from handlers.commands.signals import bsjp as _bsjp
    return await _bsjp(update, ctx)


async def morning_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Morning watchlist. See handlers/commands/signals.py"""
    from handlers.commands.signals import morning_watchlist as _morning_watchlist
    return await _morning_watchlist(update, ctx)


async def health_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Health status. See handlers/commands/health.py"""
    from handlers.commands.health import health_cmd as _health_cmd
    return await _health_cmd(update, ctx)


async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Manual scan. See handlers/commands/health.py"""
    from handlers.commands.health import scan_cmd as _scan_cmd
    return await _scan_cmd(update, ctx)


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reset circuit breakers. See handlers/commands/health.py"""
    from handlers.commands.health import reset_cmd as _reset_cmd
    return await _reset_cmd(update, ctx)


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Help command. See handlers/commands/help.py"""
    from handlers.commands.help import help_cmd as _help_cmd
    return await _help_cmd(update, ctx)


async def chart_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Chart generator. See handlers/commands/chart.py"""
    from handlers.commands.chart import chart_cmd as _chart_cmd
    return await _chart_cmd(update, ctx)


async def analisa_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Detailed analysis. See handlers/commands/analisa.py"""
    from handlers.commands.analisa import analisa_cmd as _analisa_cmd
    return await _analisa_cmd(update, ctx)


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
    app.add_handler(CommandHandler(["stats", "stat"], stats_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(tf_cat_cb, pattern=r"tfcat_"))
    app.add_handler(CallbackQueryHandler(tf_cb, pattern=r"tf_"))
    app.add_handler(CallbackQueryHandler(notifikasi_cb, pattern=r"notif_"))

    # Message handler for buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, buttons))

    # Error handler
    app.add_handler(CommandHandler("error", lambda u, c: logger.error(f"Error: {c.error}")))
