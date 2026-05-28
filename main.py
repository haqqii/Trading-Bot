# -*- coding: utf-8 -*-
"""
Bot Sinyal Saham Indonesia - IDX Stocks & Crypto Signal Bot
Entry point for the Telegram bot.

Modular structure:
- config/     - Configuration settings
- utils/      - Cache, rate limiter, formatters, indicators
- services/   - Stock, crypto, signal, chart services
- handlers/   - Command handlers and schedulers
"""

import os
import io
import sys
import logging

# Fix UTF-8 output for Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Single instance lock - prevent multiple bot instances
LOCK_FILE = os.path.join(os.path.dirname(__file__), 'bot.lock')
if os.path.exists(LOCK_FILE):
    try:
        with open(LOCK_FILE, 'r') as f:
            pid = int(f.read().strip())
        import psutil
        if psutil.pid_exists(pid):
            print(f"ERROR: Bot sudah jalan (PID: {pid})")
            print("Stop instance lama dulu atau kill proses python.")
            sys.exit(1)
        else:
            print(f"Stale lock file found (PID {pid}), removing...")
            os.remove(LOCK_FILE)
    except:
        pass

# Write our PID to lock file
with open(LOCK_FILE, 'w') as f:
    f.write(str(os.getpid()))

from telegram.ext import Application

# Import config
from config.settings import BOT_TOKEN, load_token

# Import services (this also initializes global cache instances)
from services.stock_service import stock_service
from services.crypto_service import crypto_service

# Import handlers
from handlers.command_handlers import register_handlers, ALL_STOCKS
from handlers.scheduler import register_jobs, set_user_db, set_last_prices, set_last_crypto_prices, set_last_buy_signals, set_all_stocks

# Configure logging
import logging
import logging.handlers

# Create logs directory if not exists
logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Custom handler to handle Windows file locking
class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def emit(self, record):
        try:
            super().emit(record)
        except PermissionError:
            # If file is locked, try to reopen it
            try:
                self.stream.close()
                self.stream = self._open()
                super().emit(record)
            except:
                pass

# Configure logging with safe file rotation
file_handler = SafeRotatingFileHandler(
    os.path.join(logs_dir, 'bot.log'),
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,
    encoding='utf-8',
    delay=True  # Delay opening file until first write
)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('yfinance').setLevel(logging.WARNING)
logging.getLogger('pandas').setLevel(logging.WARNING)


def main():
    """Main entry point for the bot"""
    import sys

    try:
        sys.stdout.write("BOT_STARTING\n")
        sys.stdout.flush()

        sys.stdout.write("KILL_SKIP\n")
        sys.stdout.flush()

        if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
            sys.stdout.write("NO_TOKEN\n")
            sys.stdout.flush()
            print("ERROR: Telegram bot token not found!")
            print("Please set TELEGRAM_BOT_TOKEN in .env file or environment variable.")
            return

        # Load stocks
        sys.stdout.write("LOADING_STOCKS\n")
        sys.stdout.flush()
        global ALL_STOCKS
        ALL_STOCKS = stock_service.load_stocks()
        sys.stdout.write(f"STOCKS_LOADED:{len(ALL_STOCKS)}\n")
        sys.stdout.flush()

        # Load crypto pairs
        sys.stdout.write("LOADING_CRYPTO\n")
        sys.stdout.flush()
        crypto_service.load_crypto_pairs()
        sys.stdout.write(f"CRYPTO_LOADED:{len(crypto_service.crypto_pairs)}\n")
        sys.stdout.flush()

        # Load persisted user data - import and call directly from command_handlers module
        sys.stdout.write("LOADING_USER_DATA\n")
        sys.stdout.flush()
        import handlers.command_handlers as ch
        ch.load_user_data()
        sys.stdout.write(f"USER_DATA_LOADED:{len(ch.user_data_db)}\n")
        sys.stdout.flush()

        # Debug: Check user_data_db before set
        logger.info(f"[MAIN] ch.user_data_db has {len(ch.user_data_db)} users BEFORE set_user_db")

        # Set global references for scheduler
        set_all_stocks(ALL_STOCKS)
        set_user_db(ch.user_data_db)
        set_last_prices(ch.last_prices)
        set_last_crypto_prices(ch.last_crypto_prices)
        set_last_buy_signals(ch.last_buy_signals)

        # Debug: Log user data status AFTER set
        logger.info(f"[MAIN] Users after set_user_db: {len(ch.user_data_db)}")

        # Create application
        sys.stdout.write("CREATING_APP\n")
        sys.stdout.flush()
        app = Application.builder().token(BOT_TOKEN).build()

        # Register handlers and jobs
        sys.stdout.write("REGISTERING_HANDLERS\n")
        sys.stdout.flush()
        register_handlers(app)
        register_jobs(app)

        sys.stdout.write("STARTING_POLLING\n")
        sys.stdout.flush()

        sys.stdout.write("BOT_RUNNING\n")
        sys.stdout.flush()

        app.run_polling(
            allowed_updates=Update.ALL_TYPES if 'Update' in dir() else None,
            drop_pending_updates=False,
        )

    except Exception as e:
        import traceback
        sys.stdout.write(f"ERROR:{str(e)}\n")
        traceback.print_exc()


# Import Update at module level
try:
    from telegram import Update
except ImportError:
    Update = None


import signal
import sys

def cleanup():
    """Cleanup on exit"""
    if os.path.exists(LOCK_FILE):
        try:
            os.remove(LOCK_FILE)
            print("Lock file removed")
        except:
            pass

def signal_handler(sig, frame):
    print("\n\n🛑 Bot dihentikan!")
    cleanup()
    os._exit(0)

# Windows doesn't have SIGTERM, so only use SIGINT
signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Bot dihentikan!")
        cleanup()
    finally:
        cleanup()
