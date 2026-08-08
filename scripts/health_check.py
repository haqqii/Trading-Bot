#!/usr/bin/env python3
"""
Health check script for Ochobot.
Runs every 5 minutes via systemd timer.
Sends Telegram alert if bot is down.
"""
import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(SCRIPT_DIR)
STATE_FILE = os.path.join(BOT_DIR, '.health_state.json')
LOG_FILE = os.path.join(BOT_DIR, 'logs', 'health.log')

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('health_check')


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def check_bot_alive(bot_token: str, admin_chat_id: str) -> bool:
    """Check if bot responds to getMe API."""
    import urllib.request
    import urllib.error

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get('ok'):
                return True
            log.error(f"getMe returned not-ok: {data}")
            return False
    except Exception as e:
        log.error(f"getMe failed: {e}")
        return False


def send_alert(bot_token: str, admin_chat_id: str, message: str):
    """Send alert to admin via Telegram."""
    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': admin_chat_id,
        'text': message,
        'parse_mode': 'HTML',
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get('ok'):
                log.info(f"Alert sent to {admin_chat_id}")
            else:
                log.error(f"sendMessage failed: {result}")
    except Exception as e:
        log.error(f"Failed to send alert: {e}")


def is_market_hours() -> bool:
    """Check if it's currently IDX market hours (weekdays 09:00-15:30 WIB)."""
    now_wib = datetime.now(WIB)
    weekday = now_wib.weekday()
    if weekday >= 5:  # Saturday=5, Sunday=6
        return False
    hour, minute = now_wib.hour, now_wib.minute
    market_start = 9 * 60       # 09:00
    market_end = 15 * 60 + 30   # 15:30
    current_minutes = hour * 60 + minute
    return market_start <= current_minutes <= market_end


def get_bot_status_from_systemd() -> bool:
    """Check if bot-saham systemd service is active."""
    import subprocess
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'bot-saham'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description='Ochobot health check')
    parser.add_argument('--skip-market-check', action='store_true',
                        help='Always alert even outside market hours')
    args = parser.parse_args()

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    admin_chat_id = os.environ.get('ADMIN_CHAT_ID')

    if not bot_token or not admin_chat_id:
        log.error("TELEGRAM_BOT_TOKEN and ADMIN_CHAT_ID must be set")
        sys.exit(1)

    if not args.skip_market_check and not is_market_hours():
        log.info("Outside market hours, skipping health check")
        sys.exit(0)

    state = load_state()
    now = datetime.now(WIB)
    now_str = now.strftime('%d/%m %H:%M')
    last_alert = state.get('last_alert', '')
    consecutive_failures = state.get('consecutive_failures', 0)

    bot_ok = check_bot_alive(bot_token, admin_chat_id)

    if not bot_ok:
        consecutive_failures += 1
        state['consecutive_failures'] = consecutive_failures
        save_state(state)

        log.warning(f"Bot is DOWN (attempt {consecutive_failures})")

        # Only alert every 30 consecutive failures (~every 2.5 hours)
        # unless it's the first failure or last alert was long ago
        should_alert = False
        if consecutive_failures == 1:
            should_alert = True
        elif consecutive_failures >= 30:
            # More than 2.5 hours down
            should_alert = True
        elif last_alert:
            try:
                last = datetime.strptime(last_alert, '%d/%m %H:%M').replace(tzinfo=WIB)
                if (now - last) >= timedelta(hours=1):
                    should_alert = True
            except Exception:
                should_alert = True

        if should_alert:
            svc_ok = get_bot_status_from_systemd()
            svc_status = "RUNNING" if svc_ok else "STOPPED"
            msg = (
                f"🔴 <b>Bot Ochobot DOWN!</b>\n\n"
                f"🕐 {now_str} WIB\n"
                f"⏱️ Gagal #{consecutive_failures}x berturut-turut\n"
                f"⚙️ Service: {svc_status}\n\n"
                f"<i>Cek: journalctl -u bot-saham -n 30</i>"
            )
            send_alert(bot_token, admin_chat_id, msg)
            state['last_alert'] = now_str
            save_state(state)
    else:
        if consecutive_failures > 0:
            log.info(f"Bot recovered after {consecutive_failures} failures")
        consecutive_failures = 0
        state['consecutive_failures'] = 0
        state['last_recovery'] = now_str
        save_state(state)

    log.info(f"Health check OK | consecutive_failures={consecutive_failures}")


if __name__ == '__main__':
    main()
