"""
Configuration settings for the bot.
"""
import os

# Timeframes
TIMEFRAMES = {
    '1': {'name': '1 Menit', 'interval': '1m', 'period': '1d'},
    '5': {'name': '5 Menit', 'interval': '5m', 'period': '5d'},
    '15': {'name': '15 Menit', 'interval': '15m', 'period': '5d'},
    '60': {'name': '1 Jam', 'interval': '1h', 'period': '1mo'},
}

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
                except:
                    pass
    return token if token else 'YOUR_BOT_TOKEN_HERE'


BOT_TOKEN = load_token()
