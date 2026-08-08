"""
Configuration settings for the bot.
"""
import logging
import os

logger = logging.getLogger(__name__)

# Timeframes
TIMEFRAMES = {
    '1':   {'name': '1 Menit',  'interval': '1m',  'period': '1d'},
    '5':   {'name': '5 Menit',  'interval': '5m',  'period': '5d'},
    '15':  {'name': '15 Menit', 'interval': '15m', 'period': '5d'},
    '30':  {'name': '30 Menit', 'interval': '30m', 'period': '5d'},
    '60':  {'name': '1 Jam',    'interval': '1h',  'period': '1mo'},
    '240': {'name': '4 Jam',    'interval': '4h',  'period': '1mo'},
    '1440': {'name': '1 Hari',  'interval': '1d',  'period': '3mo'},
}

# Valid intervals for custom input (maps Yahoo Finance interval -> TIMEFRAMES key)
INTERVAL_TO_KEY = {
    '1m': '1', '2m': '1', '5m': '5', '15m': '15',
    '30m': '30', '60m': '60', '90m': '60',
    '1h': '60', '4h': '240',
    '1d': '1440', '5d': '1440', '1wk': '1440', '1mo': '1440',
}

# Yahoo Finance supported intervals for validation
VALID_INTERVALS = set(INTERVAL_TO_KEY.keys())

# Separators
SEP = "═" * 35
SEP40 = "═" * 40


def load_token():
    """Load Telegram bot token from environment or .env file"""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        possible_paths = [
            '.env',
            os.path.join(os.getcwd(), '.env'),
        ]
        for env_file in possible_paths:
            if os.path.exists(env_file):
                try:
                    with open(env_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith('TELEGRAM_BOT_TOKEN='):
                                token = line.split('=', 1)[1].strip()
                                break
                except Exception as e:
                    logger.warning(f"Failed to read {env_file}: {e}")
    return token if token else 'YOUR_BOT_TOKEN_HERE'


BOT_TOKEN = load_token()
