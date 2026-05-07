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
from utils.formatters import TIMEFRAMES, format_signal_msg, format_crypto_msg, format_bsjp_msg, format_morning_msg
from utils.rate_limiter import get_all_api_stats
from utils.cache import _price_cache, _signal_cache
from idx_stocks import ALL_IDX_STOCKS

logger = logging.getLogger(__name__)

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
    """Load user data from file with backup recovery"""
    global user_data_db, last_buy_signals
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"[LOAD_USER] Starting load_user_data(), current user_data_db has {len(user_data_db)} users")

    # Load user data with backup fallback
    try:
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
                user_data_db = json.load(f)
            logger.info(f"[LOAD_USER] Loaded {len(user_data_db)} users from {USER_DATA_FILE}")
            logger.info(f"[LOAD_USER] user_data_db id={id(user_data_db)}")
        else:
            backup_file = USER_DATA_FILE + '.bak'
            if os.path.exists(backup_file):
                with open(backup_file, 'r', encoding='utf-8') as f:
                    user_data_db = json.load(f)
                logger.warning(f"Loaded {len(user_data_db)} users from BACKUP {backup_file}")
    except json.JSONDecodeError as e:
        # Main file corrupt, try backup
        backup_file = USER_DATA_FILE + '.bak'
        if os.path.exists(backup_file):
            try:
                with open(backup_file, 'r', encoding='utf-8') as f:
                    user_data_db = json.load(f)
                logger.warning(f"Recovered {len(user_data_db)} users from BACKUP after JSON error: {e}")
            except Exception as backup_err:
                logger.error(f"Backup also corrupt: {backup_err}")
                user_data_db = {}
        else:
            logger.error(f"JSON error loading user data, no backup available: {e}")
            user_data_db = {}
    except Exception as e:
        logger.error(f"Error loading user data: {e}")

    # Load signals with backup fallback
    try:
        if os.path.exists(SIGNALS_FILE):
            with open(SIGNALS_FILE, 'r', encoding='utf-8') as f:
                last_buy_signals = json.load(f)
            # Convert string keys back to datetime for existing entries
            for key in list(last_buy_signals.keys()):
                if isinstance(last_buy_signals[key].get('time'), str):
                    try:
                        last_buy_signals[key]['time'] = datetime.fromisoformat(last_buy_signals[key]['time'])
                    except:
                        last_buy_signals[key]['time'] = datetime.now()
            logger.info(f"Loaded {len(last_buy_signals)} signals from {SIGNALS_FILE}")
        else:
            backup_file = SIGNALS_FILE + '.bak'
            if os.path.exists(backup_file):
                with open(backup_file, 'r', encoding='utf-8') as f:
                    last_buy_signals = json.load(f)
                logger.warning(f"Loaded {len(last_buy_signals)} signals from BACKUP")
    except json.JSONDecodeError as e:
        backup_file = SIGNALS_FILE + '.bak'
        if os.path.exists(backup_file):
            try:
                with open(backup_file, 'r', encoding='utf-8') as f:
                    last_buy_signals = json.load(f)
                logger.warning(f"Recovered {len(last_buy_signals)} signals from BACKUP after JSON error: {e}")
            except Exception as backup_err:
                logger.error(f"Backup also corrupt: {backup_err}")
                last_buy_signals = {}
        else:
            logger.error(f"JSON error loading signals, no backup available: {e}")
            last_buy_signals = {}
    except Exception as e:
        logger.error(f"Error loading signals: {e}")


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
        except Exception:
            pass  # Backup failed, continue anyway

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
        except:
            pass
        # Restore from backup
        backup_path = filepath + '.bak'
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, filepath)
                logger.warning(f"Restored {filepath} from backup")
            except:
                pass
        return False


def save_user_data():
    """Save user data to file atomically"""
    # Save user data
    _atomic_write(USER_DATA_FILE, user_data_db)

    # Convert datetime to string for JSON serialization
    serializable_signals = {}
    for key, val in last_buy_signals.items():
        serializable_signals[key] = {**val, 'time': val.get('time', datetime.now()).isoformat()}

    # Save signals
    _atomic_write(SIGNALS_FILE, serializable_signals)


def get_user(user_id):
    """Get or create user data"""
    if user_id not in user_data_db:
        user_data_db[user_id] = {
            'crypto_watchlist': ['BTC-USD', 'ETH-USD', 'BNB-USD'],
            'portfolio': [],
            'notifications': True,
            'notif_saham': False,
            'notif_crypto': False,
            'notif_bsjp': False,
            'notif_morning': False,
            'favorit': {},  # {ticker: target_price}
            'crypto_favorit': {},  # {ticker: target_price}
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
        except:
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
    except:
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
    except:
        await update.message.reply_text("❌ Format salah!")


# === CRYPTO ===
async def crypto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View crypto signals - PARALLEL FETCHING"""
    start_time = time.time()
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

    semaphore = asyncio.Semaphore(15)

    async def fetch_with_semaphore(ticker):
        async with semaphore:
            await asyncio.sleep(0.1)
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
        except Exception:
            pass
        return None

    semaphore = asyncio.Semaphore(25)
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
        except Exception:
            pass
        return None

    semaphore = asyncio.Semaphore(20)
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
    except Exception:
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

    await update.message.reply_text(f"📊 Generating chart for `{ticker}`...", parse_mode='Markdown')

    try:
        img_buf = chart_service.generate_crypto_chart(ticker, interval=interval, period=period)

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
    await update.message.reply_text(f"📊 Menganalisis `{ticker}`...", parse_mode='Markdown')

    try:
        # Load crypto pairs if not loaded
        if not crypto_service.crypto_pairs:
            crypto_service.load_crypto_pairs()

        # Check if it's a crypto ticker - auto detect
        ticker_upper = ticker.upper()

        # Check patterns that indicate crypto
        is_crypto = (
            ticker_upper in crypto_service.crypto_pairs or  # In CoinGecko list
            ticker_upper in crypto_service.coingecko_ids or  # In CoinGecko IDs
            ticker.endswith('-USD') or  # Already has -USD suffix
            ticker.endswith('-USDT') or  # USDT pair
            ticker.endswith('-BTC') or  # BTC pair
            ticker.endswith('-ETH') or  # ETH pair
            ticker_upper in ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE']  # Common symbols
        )

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
                    if len(ticker) <= 5:  # Likely a symbol, add -USD
                        full_ticker = ticker.upper() + '-USD'

            # Get crypto data - use 5d period to get enough candles for indicators
            d = crypto_service.get_crypto_data_combined(full_ticker, '1h', '5d')

            # Fallback: try without -USD suffix
            if not d and full_ticker.endswith('-USD'):
                alt_ticker = full_ticker.replace('-USD', '')
                d = crypto_service.get_crypto_data_combined(alt_ticker, '1h', '5d')

            # Fallback: try with -USDT suffix
            if not d and full_ticker.endswith('-USD'):
                alt_ticker = full_ticker.replace('-USD', '-USDT')
                d = crypto_service.get_crypto_data_combined(alt_ticker, '1h', '5d')

            if not d:
                await update.message.reply_text(
                    f"❌ Gagal mengambil data untuk `{ticker}`\n"
                    "Kode tidak ditemukan di CoinGecko/Yahoo Finance",
                    parse_mode='Markdown'
                )
                return

            # Generate signal
            s = signal_service.generate_crypto_signal(d)

            price = d['price']
            rsi = d.get('rsi', 50)
            change = d.get('change', 0)
            ma_fast = d.get('ma_fast', price)
            ma_slow = d.get('ma_slow', price)
            macd = d.get('macd', 0)
            macd_signal = d.get('macd_signal', 0)
            macd_hist = d.get('macd_hist', 0)
            bb_upper = d.get('bb_upper', price * 1.05)
            bb_middle = d.get('bb_middle', price)
            bb_lower = d.get('bb_lower', price * 0.95)
            volume_ratio = d.get('volume_ratio', 1)
            atr = d.get('atr', price * 0.02)
            name = d.get('name', ticker)
            candles = d.get('candles', 0)
            usd_idr = crypto_service.get_usd_idr_rate()

            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            upside = (bb_upper - price) / price * 100
            downside = (price - bb_lower) / price * 100
            price_idr = price * usd_idr

            # Detect chart patterns
            patterns_detected = []
            pattern_summary = ""
            try:
                if d.get('raw_df') is not None and len(d.get('raw_df', [])) >= 30:
                    from utils.patterns import detect_all_patterns
                    patterns = detect_all_patterns(d['raw_df'])
                    if patterns.get('patterns_found', 0) > 0:
                        pattern_list = patterns.get('pattern_list', [])
                        for p in pattern_list[:3]:
                            emoji = "🟢" if p.get('bullish') else "🔴" if p.get('bearish') else "🟡"
                            strength = p.get('strength', 0)
                            strength_bar = "█" * int(strength * 5)
                            patterns_detected.append({
                                'emoji': emoji,
                                'name': p.get('name', 'Unknown'),
                                'strength_bar': strength_bar,
                                'description': p.get('description', '')
                            })
                        pattern_summary = patterns.get('pattern_summary', '')
            except Exception as e:
                logger.debug(f"Pattern detection error: {e}")

            # RSI status
            if rsi < 30:
                rsi_status = "⚠️ OVERSOLD"
            elif rsi < 40:
                rsi_status = "🟢 BULLISH"
            elif rsi > 70:
                rsi_status = "⚠️ OVERBOUGHT"
            elif rsi > 60:
                rsi_status = "🔴 BEARISH"
            else:
                rsi_status = "🟢 NETRAL"

            # MA status
            if ma_fast > ma_slow:
                ma_status = "🟢 GOLDEN CROSS"
                ma_golden = True
            else:
                ma_status = "🔴 DEATH CROSS"
                ma_golden = False

            # MACD status
            macd_status = "🟢 POSITIF" if macd > 0 else "🔴 NEGATIF"
            if macd > macd_signal:
                macd_cross = "🟢 CROSS UP"
                macd_bullish = True
            else:
                macd_cross = "🔴 CROSS DOWN"
                macd_bullish = False

            macd_hist_status = "🟢 BULLISH" if macd_hist > 0 else "🔴 BEARISH"

            # Volume status
            if volume_ratio > 1.5:
                vol_status = "✅ SPIKE"
            elif volume_ratio > 1.0:
                vol_status = "✅ TINGGI"
            else:
                vol_status = "⚠️ RENDAH"

            # Signal
            signal = s.get('signal', 'HOLD')
            quality = s.get('quality', 'WEAK')
            buy_score = s.get('buy_score', 0)
            sell_score = s.get('sell_score', 0)

            if signal == 'BUY':
                signal_emoji = "🟢"
            elif signal == 'SELL':
                signal_emoji = "🔴"
            else:
                signal_emoji = "⚪"

            # Build bullish/bearish reasons
            bullish_reasons = []
            bearish_reasons = []

            if rsi < 35:
                bullish_reasons.append(f"RSI {rsi:.0f} Oversold")
            elif rsi > 65:
                bearish_reasons.append(f"RSI {rsi:.0f} Overbought")
            if ma_golden:
                bullish_reasons.append("MA Golden Cross")
            else:
                bearish_reasons.append("MA Death Cross")
            if macd_bullish:
                bullish_reasons.append("MACD Bullish")
            else:
                bearish_reasons.append("MACD Bearish")
            if volume_ratio > 1.5:
                bullish_reasons.append(f"Volume Spike {volume_ratio:.1f}x")
            if change > 2:
                bullish_reasons.append(f"Harga naik +{change:.1f}%")
            elif change < -2:
                bearish_reasons.append(f"Harga turun {change:.1f}%")

            # Build message with comprehensive format
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            upside = (bb_upper - price) / price * 100
            downside = (price - bb_lower) / price * 100

            # Conflict detection
            has_conflict = (rsi < 35 and not ma_golden) or (rsi > 65 and ma_golden)

            msg = f"""● Analisis {name.upper()} ({ticker})

┌──────────────────────────────────────────────────────┐
│                    📊 OVERVIEW                       │
├──────────────────────────────────────────────────────┤
│  Harga      : ${price:,.2f} ({price_idr:,.0f} IDR)  │
│  Change     : {change:+.2f}%                        │
│  RSI        : {rsi:.1f} {rsi_status}              │
│  Candles    : {candles}                            │
└──────────────────────────────────────────────────────┘

📈 INDIKATOR TEKNIKAL

┌──────────────┬──────────────┬──────────────────────┐
│  Indikator   │ Value        │ Status               │
├──────────────┼──────────────┼──────────────────────┤
│ RSI          │ {rsi:,.1f}       │ {rsi_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MA Fast      │ ${ma_fast:,.2f}    │ {"Di atas harga" if ma_fast > price else "Di bawah harga"}     │
├──────────────┼──────────────┼──────────────────────┤
│ MA Slow      │ ${ma_slow:,.2f}    │ {"Di atas harga" if ma_slow > price else "Di bawah harga"}     │
├──────────────┼──────────────┼──────────────────────┤
│ MACD         │ {macd:,.2f}       │ {macd_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MACD Hist    │ {macd_hist:,.2f}      │ {macd_hist_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ BB Lower     │ ${bb_lower:,.2f}    │ Dekat support     │
├──────────────┼──────────────┼──────────────────────┤
│ Volume Ratio │ {volume_ratio:.2f}x      │ {vol_status:<20} │
└──────────────┴──────────────┴──────────────────────┘

Signal Analysis

{signal_emoji} Signal: **{signal}** ({quality.upper()})
Buy Score: {buy_score} | Sell Score: {sell_score}

Alasan {signal}:
"""

            # Add signal reasons
            if signal == 'BUY':
                for r in bullish_reasons:
                    msg += f"  - {r}\n"
            elif signal == 'SELL':
                for r in bearish_reasons:
                    msg += f"  - {r}\n"
            else:
                if bullish_reasons:
                    msg += "🟢 Bullish: " + ", ".join(bullish_reasons[:3]) + "\n"
                if bearish_reasons:
                    msg += "🔴 Bearish: " + ", ".join(bearish_reasons[:3]) + "\n"

            # Chart Patterns Detected
            if patterns_detected:
                msg += "\n📐 Chart Patterns Detected:\n"
                for p in patterns_detected:
                    msg += f"   {p['emoji']} {p['name']} {p['strength_bar']}\n"
                    msg += f"      {p['description']}\n"

            # Conflict warning
            if has_conflict:
                msg += """
Tapi ada kontradiksi:
  - RSI oversold tapi MA masih bearish
  - Tunggu konfirmasi sinyal
"""

            msg += f"""
Kesimpulan

┌──────────────────────────────────────────────────────┐
│  ⚠️  KONDISI {"KONFLIK" if has_conflict else "TERKINI":<51}│
├──────────────────────────────────────────────────────┤"""

            if bearish_reasons:
                msg += """
│  📉 BEARISH:                                        │"""
                for r in bearish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            if bullish_reasons:
                msg += """
│  📈 BULLISH:                                        │"""
                for r in bullish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            msg += """
│                                                      │
└──────────────────────────────────────────────────────┘
"""

            # Add TP/SL recommendations
            if signal in ['BUY', 'SELL']:
                entry = s.get('entry', price)
                tp1 = s.get('tp1')
                tp2 = s.get('tp2')
                tp3 = s.get('tp3')
                sl = s.get('sl')

                if tp1 and sl:
                    tp1_pct = (tp1 - entry) / entry * 100
                    sl_pct = (entry - sl) / entry * 100
                    rr = tp1_pct / sl_pct if sl_pct > 0 else 0

                    # Generate dynamic saran based on actual data
                    saran_parts = []

                    if signal == 'BUY':
                        if rsi < 35:
                            saran_parts.append(f"RSI oversold ({rsi:.0f}) - peluang rebound")
                        if has_conflict:
                            saran_parts.append("Konflik sinyal - tunggu konfirmasi breakout")
                        elif volume_ratio < 1:
                            saran_parts.append("Volume rendah - tunggu volume spike")
                        if ma_f < price:
                            saran_parts.append(f"Tunggu harga di atas MA ({ma_f:,.0f})")
                        saran_parts.append("Risk/Reward 1:" + f"{rr:.1f}")
                    elif signal == 'SELL':
                        if rsi > 65:
                            saran_parts.append(f"RSI overbought ({rsi:.0f}) - peluang koreksi")
                        if has_conflict:
                            saran_parts.append("Konflik sinyal - tunggu konfirmasi breakdown")
                        elif volume_ratio < 1:
                            saran_parts.append("Volume rendah - hati-hati false break")
                        if ma_f > price:
                            saran_parts.append(f"Tunggu harga di bawah MA ({ma_f:,.0f})")
                        saran_parts.append("Risk/Reward 1:" + f"{rr:.1f}")

                    saran_text = ". ".join(saran_parts[:3]) if saran_parts else "Analisis teknikal murni"

                    msg += f"""Rekomendasi

Skenario: Agresif
Action: **{'BUY' if signal == 'BUY' else 'SELL'}**
Entry: ${entry:,.2f}
SL: ${sl:,.2f}
Target: ${tp1:,.2f}
────────────────────────────────────────────────
Skenario: Konservatif
Action: HOLD
Entry: -
SL: -
Target: Tunggu konfirmasi sinyal

Saran: {saran_text}.
"""

            msg += """
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *Disclaimer:* Analisis bukan
nasihat keuangan. Trading risiko
tanggung sendiri.
"""

        else:
            # Stock analysis
            full_ticker = ticker + ".JK"
            d = stock_service.get_stock_data(full_ticker, '5m', '3d')

            if not d or d.get('candles', 0) < 50:
                await update.message.reply_text(
                    f"❌ Gagal mengambil data untuk `{ticker}`\n"
                    "Saham tidak ditemukan atau data insufficient",
                    parse_mode='Markdown'
                )
                return

            # Generate signal
            s = signal_service.generate_stock_signal(d)

            price = d['price']
            rsi = d.get('rsi', 50)
            change = d.get('change', 0)
            ma_fast = d.get('ma_fast', 0)
            ma_slow = d.get('ma_slow', 0)
            macd = d.get('macd', 0)
            macd_signal = d.get('macd_signal', 0)
            macd_hist = d.get('macd_hist', 0)
            bb_upper = d.get('bb_upper', price * 1.05)
            bb_middle = d.get('bb_middle', price)
            bb_lower = d.get('bb_lower', price * 0.95)
            volume_ratio = d.get('volume_ratio', 1)
            atr = d.get('atr', price * 0.02)
            name = ALL_STOCKS.get(ticker, ticker)
            candles = d.get('candles', 0)

            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            upside = (bb_upper - price) / price * 100
            downside = (price - bb_lower) / price * 100

            # Detect chart patterns
            patterns_detected = []
            pattern_summary = ""
            try:
                if d.get('raw_df') is not None and len(d.get('raw_df', [])) >= 30:
                    from utils.patterns import detect_all_patterns
                    patterns = detect_all_patterns(d['raw_df'])
                    if patterns.get('patterns_found', 0) > 0:
                        pattern_list = patterns.get('pattern_list', [])
                        for p in pattern_list[:3]:
                            emoji = "🟢" if p.get('bullish') else "🔴" if p.get('bearish') else "🟡"
                            strength = p.get('strength', 0)
                            strength_bar = "█" * int(strength * 5)
                            patterns_detected.append({
                                'emoji': emoji,
                                'name': p.get('name', 'Unknown'),
                                'strength_bar': strength_bar,
                                'description': p.get('description', '')
                            })
                        pattern_summary = patterns.get('pattern_summary', '')
            except Exception as e:
                logger.debug(f"Pattern detection error: {e}")

            # RSI status
            if rsi < 30:
                rsi_status = "⚠️ OVERSOLD"
            elif rsi < 40:
                rsi_status = "🟢 BULLISH"
            elif rsi > 70:
                rsi_status = "⚠️ OVERBOUGHT"
            elif rsi > 60:
                rsi_status = "🔴 BEARISH"
            else:
                rsi_status = "🟢 NETRAL"

            # MA status
            if ma_fast > ma_slow:
                ma_status = "🟢 GOLDEN CROSS"
                ma_golden = True
            else:
                ma_status = "🔴 DEATH CROSS"
                ma_golden = False

            # MACD status
            macd_status = "🟢 POSITIF" if macd > 0 else "🔴 NEGATIF"
            if macd > macd_signal:
                macd_cross = "🟢 CROSS UP"
                macd_bullish = True
            else:
                macd_cross = "🔴 CROSS DOWN"
                macd_bullish = False

            macd_hist_status = "🟢 BULLISH" if macd_hist > 0 else "🔴 BEARISH"

            # Volume status
            if volume_ratio > 1.5:
                vol_status = "✅ SPIKE"
            elif volume_ratio > 1.0:
                vol_status = "✅ TINGGI"
            else:
                vol_status = "⚠️ RENDAH"

            # Signal
            signal = s.get('signal', 'HOLD')
            quality = s.get('quality', 'WEAK')
            buy_score = s.get('buy_score', 0)
            sell_score = s.get('sell_score', 0)

            if signal == 'BUY':
                signal_emoji = "🟢"
            elif signal == 'SELL':
                signal_emoji = "🔴"
            else:
                signal_emoji = "⚪"

            # Build bullish/bearish reasons
            bullish_reasons = []
            bearish_reasons = []

            if rsi < 35:
                bullish_reasons.append(f"RSI {rsi:.0f} Oversold")
            elif rsi > 65:
                bearish_reasons.append(f"RSI {rsi:.0f} Overbought")
            if ma_golden:
                bullish_reasons.append("MA Golden Cross")
            else:
                bearish_reasons.append("MA Death Cross")
            if macd_bullish:
                bullish_reasons.append("MACD Bullish")
            else:
                bearish_reasons.append("MACD Bearish")
            if volume_ratio > 1.5:
                bullish_reasons.append(f"Volume Spike {volume_ratio:.1f}x")
            if change > 2:
                bullish_reasons.append(f"Harga naik +{change:.1f}%")
            elif change < -2:
                bearish_reasons.append(f"Harga turun {change:.1f}%")
            if bb_pos < 0.2:
                bullish_reasons.append("Dekat BB Lower (Support)")
            elif bb_pos > 0.8:
                bearish_reasons.append("Dekat BB Upper (Resistance)")

            # Build message with comprehensive format
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            upside = (bb_upper - price) / price * 100
            downside = (price - bb_lower) / price * 100

            # Conflict detection
            has_conflict = (rsi < 35 and not ma_golden) or (rsi > 65 and ma_golden)

            # MA status text
            ma_fast_status = "Di atas harga" if ma_fast > price else "Di bawah harga"
            ma_slow_status = "Di atas harga" if ma_slow > price else "Di bawah harga"

            msg = f"""● Analisis {name.upper()} ({ticker})

┌──────────────────────────────────────────────────────┐
│                    📊 OVERVIEW                       │
├──────────────────────────────────────────────────────┤
│  Harga      : Rp {price:,.0f}                           │
│  Change     : {change:+.2f}%                        │
│  RSI        : {rsi:.1f} {rsi_status}              │
│  Candles    : {candles}                            │
└──────────────────────────────────────────────────────┘

📈 INDIKATOR TEKNIKAL

┌──────────────┬──────────────┬──────────────────────┐
│  Indikator   │ Value        │ Status               │
├──────────────┼──────────────┼──────────────────────┤
│ RSI          │ {rsi:,.1f}       │ {rsi_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MA Fast      │ {ma_fast:,.2f}     │ {ma_fast_status:<17}│
├──────────────┼──────────────┼──────────────────────┤
│ MA Slow      │ {ma_slow:,.2f}     │ {ma_slow_status:<17}│
├──────────────┼──────────────┼──────────────────────┤
│ MACD         │ {macd:,.2f}       │ {macd_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MACD Hist    │ {macd_hist:,.2f}      │ {macd_hist_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ BB Lower     │ {bb_lower:,.2f}     │ Dekat support     │
├──────────────┼──────────────┼──────────────────────┤
│ Volume Ratio │ {volume_ratio:.2f}x      │ {vol_status:<20} │
└──────────────┴──────────────┴──────────────────────┘

Signal Analysis

{signal_emoji} Signal: **{signal}** ({quality.upper()})
Buy Score: {buy_score} | Sell Score: {sell_score}

Alasan {signal}:
"""

            # Add signal reasons
            if signal == 'BUY':
                for r in bullish_reasons:
                    msg += f"  - {r}\n"
            elif signal == 'SELL':
                for r in bearish_reasons:
                    msg += f"  - {r}\n"
            else:
                if bullish_reasons:
                    msg += "🟢 Bullish: " + ", ".join(bullish_reasons[:3]) + "\n"
                if bearish_reasons:
                    msg += "🔴 Bearish: " + ", ".join(bearish_reasons[:3]) + "\n"

            # Chart Patterns Detected
            if patterns_detected:
                msg += "\n📐 Chart Patterns Detected:\n"
                for p in patterns_detected:
                    msg += f"   {p['emoji']} {p['name']} {p['strength_bar']}\n"
                    msg += f"      {p['description']}\n"

            # Conflict warning
            if has_conflict:
                msg += """
Tapi ada kontradiksi:
  - RSI oversold tapi MA masih bearish
  - Tunggu konfirmasi sinyal
"""

            msg += f"""
Kesimpulan

┌──────────────────────────────────────────────────────┐
│  ⚠️  KONDISI {"KONFLIK" if has_conflict else "TERKINI":<51}│
├──────────────────────────────────────────────────────┤"""

            if bearish_reasons:
                msg += """
│  📉 BEARISH:                                        │"""
                for r in bearish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            if bullish_reasons:
                msg += """
│  📈 BULLISH:                                        │"""
                for r in bullish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            msg += """
│                                                      │
└──────────────────────────────────────────────────────┘
"""

            # Add TP/SL recommendations
            if signal in ['BUY', 'SELL']:
                entry = s.get('entry', price)
                tp1 = s.get('tp1')
                tp2 = s.get('tp2')
                tp3 = s.get('tp3')
                sl = s.get('sl')

                if tp1 and sl:
                    tp1_pct = (tp1 - entry) / entry * 100
                    sl_pct = (entry - sl) / entry * 100
                    rr = tp1_pct / sl_pct if sl_pct > 0 else 0

                    # Generate dynamic saran based on actual data
                    saran_parts = []

                    if signal == 'BUY':
                        if rsi < 35:
                            saran_parts.append(f"RSI oversold ({rsi:.0f}) - peluang rebound")
                        if has_conflict:
                            saran_parts.append("Konflik sinyal - tunggu konfirmasi breakout")
                        elif volume_ratio < 1:
                            saran_parts.append("Volume rendah - tunggu volume spike")
                        if ma_fast < price:
                            saran_parts.append(f"Tunggu harga di atas MA ({ma_fast:,.0f})")
                        saran_parts.append("Risk/Reward 1:" + f"{rr:.1f}")
                    elif signal == 'SELL':
                        if rsi > 65:
                            saran_parts.append(f"RSI overbought ({rsi:.0f}) - peluang koreksi")
                        if has_conflict:
                            saran_parts.append("Konflik sinyal - tunggu konfirmasi breakdown")
                        elif volume_ratio < 1:
                            saran_parts.append("Volume rendah - hati-hati false break")
                        if ma_fast > price:
                            saran_parts.append(f"Tunggu harga di bawah MA ({ma_fast:,.0f})")
                        saran_parts.append("Risk/Reward 1:" + f"{rr:.1f}")

                    saran_text = ". ".join(saran_parts[:3]) if saran_parts else "Analisis teknikal murni"

                    msg += f"""Rekomendasi

Skenario: Agresif
Action: **{'BUY' if signal == 'BUY' else 'SELL'}**
Entry: Rp {entry:,.0f}
SL: Rp {sl:,.0f}
Target: Rp {tp1:,.0f}
────────────────────────────────────────────────
Skenario: Konservatif
Action: HOLD
Entry: -
SL: -
Target: Tunggu konfirmasi sinyal

Saran: {saran_text}.
"""

            msg += """
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *Disclaimer:* Analisis bukan
nasihat keuangan. Trading risiko
tanggung sendiri.
"""

        await update.message.reply_text(msg, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Analisa error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


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
