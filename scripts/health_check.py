#!/usr/bin/env python3
"""
Health check script for ochobot.

Usage:
    python health_check.py               # Human-readable, alerts on failure
    python health_check.py --json       # Machine-readable JSON output
    python health_check.py --json --quiet  # JSON only, no logging
    python health_check.py --skip-market-check  # Run even outside market hours

Exit codes:
    0 = all healthy
    1 = degraded (some checks failed but bot is alive)
    2 = down (bot not responding)
"""
import os
import sys
import json
import time
import logging
import argparse
import sqlite3
from datetime import datetime, timezone, timedelta

WIB = timezone(timedelta(hours=7))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(SCRIPT_DIR)
STATE_FILE = os.path.join(BOT_DIR, 'data', 'health_state.json')
BOT_LOCK = os.path.join(BOT_DIR, 'data', 'bot.lock')
DB_FILE = os.path.join(BOT_DIR, 'data', 'ochobot.db')
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


def check_bot_alive(bot_token: str) -> dict:
    """Check if bot responds to getMe API."""
    import ssl
    import urllib.request
    import urllib.error

    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    result = {'ok': False, 'error': None, 'response_time_ms': 0}
    start = time.time()
    try:
        # Create SSL context that handles proxies/certificates gracefully
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read())
            result['response_time_ms'] = int((time.time() - start) * 1000)
            if data.get('ok'):
                result['ok'] = True
                result['bot_username'] = data.get('result', {}).get('username')
            else:
                result['error'] = f"API returned ok=false"
                log.error(f"getMe returned not-ok: {data}")
    except Exception as e:
        result['error'] = str(e)
        log.error(f"getMe failed: {e}")
    return result


def check_database() -> dict:
    """Check SQLite database connectivity and basic health."""
    result = {'ok': False, 'error': None, 'size_mb': 0,
              'wal_size_mb': 0, 'tables': {}}
    try:
        size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
        result['size_mb'] = round(size_mb, 2)

        wal_file = DB_FILE + '-wal'
        if os.path.exists(wal_file):
            result['wal_size_mb'] = round(os.path.getsize(wal_file) / (1024 * 1024), 2)

        conn = sqlite3.connect(DB_FILE, timeout=5.0)
        cursor = conn.cursor()

        # Count rows in main tables
        for table in ['users', 'signals', 'price_alerts', 'portfolio']:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                result['tables'][table] = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                result['tables'][table] = -1  # table doesn't exist

        # WAL size warning
        if result['wal_size_mb'] > 5:
            result['warning'] = f"WAL file is {result['wal_size_mb']} MB — consider checkpoint"

        conn.close()
        result['ok'] = True
    except Exception as e:
        result['error'] = str(e)
        log.error(f"DB check failed: {e}")
    return result


def check_bot_lock() -> dict:
    """Check if bot lock file exists and process is alive."""
    result = {'ok': False, 'pid': None, 'alive': False, 'error': None}
    try:
        if os.path.exists(BOT_LOCK):
            with open(BOT_LOCK) as f:
                pid = int(f.read().strip())
            result['pid'] = pid
            try:
                import psutil
                result['alive'] = psutil.pid_exists(pid)
                result['ok'] = result['alive']
            except ImportError:
                # psutil not available — can't verify process, but lock exists
                result['ok'] = True
                result['alive'] = None
        else:
            result['error'] = "lock file not found"
    except Exception as e:
        result['error'] = str(e)
    return result


def check_disk_space() -> dict:
    """Check available disk space."""
    result = {'ok': True, 'free_mb': 0, 'total_mb': 0, 'pct_free': 100}
    try:
        import psutil
        disk = psutil.disk_usage(BOT_DIR)
        result['free_mb'] = round(disk.free / (1024 * 1024), 0)
        result['total_mb'] = round(disk.total / (1024 * 1024), 0)
        result['pct_free'] = round(disk.percent, 1)
        if result['pct_free'] < 10:
            result['ok'] = False
            result['warning'] = f"Disk almost full ({result['pct_free']}% free)"
    except Exception as e:
        log.warning(f"Disk check failed: {e}")
    return result


def is_market_hours() -> bool:
    """Check if it's currently IDX market hours (weekdays 09:00-15:30 WIB)."""
    now_wib = datetime.now(WIB)
    weekday = now_wib.weekday()
    if weekday >= 5:
        return False
    hour, minute = now_wib.hour, now_wib.minute
    market_start = 9 * 60
    market_end = 15 * 60 + 30
    current_minutes = hour * 60 + minute
    return market_start <= current_minutes <= market_end


def send_alert(bot_token: str, admin_chat_id: str, message: str):
    """Send alert to admin via Telegram."""
    import ssl
    import urllib.request
    import urllib.parse

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': admin_chat_id,
        'text': message,
        'parse_mode': 'HTML',
    }).encode()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            result = json.loads(resp.read())
            if result.get('ok'):
                log.info(f"Alert sent to {admin_chat_id}")
            else:
                log.error(f"sendMessage failed: {result}")
    except Exception as e:
        log.error(f"Failed to send alert: {e}")


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
    parser.add_argument('--json', action='store_true',
                        help='Output machine-readable JSON')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress stdout when using --json')
    parser.add_argument('--skip-market-check', action='store_true',
                        help='Always run even outside market hours')
    parser.add_argument('--alert-only-down', action='store_true',
                        help='Only alert when bot is completely down, not degraded')
    args = parser.parse_args()

    if args.quiet:
        log.setLevel(logging.WARNING)

    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    admin_chat_id = os.environ.get('ADMIN_CHAT_ID')

    if not bot_token:
        if args.json:
            print(json.dumps({'ok': False, 'error': 'TELEGRAM_BOT_TOKEN not set'}))
            sys.exit(2)
        log.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(2)

    # Collect all health checks
    checks = {
        'bot': check_bot_alive(bot_token),
        'database': check_database(),
        'bot_lock': check_bot_lock(),
        'disk': check_disk_space(),
    }

    # Determine overall status
    bot_ok = checks['bot']['ok']
    db_ok = checks['database']['ok']
    lock_ok = checks['bot_lock']['ok']
    disk_ok = checks['disk']['ok']

    # Overall: 0=healthy, 1=degraded, 2=down
    if bot_ok and db_ok and lock_ok and disk_ok:
        overall = 'healthy'
        exit_code = 0
    elif not bot_ok:
        overall = 'down'
        exit_code = 2
    else:
        overall = 'degraded'
        exit_code = 1

    now_wib = datetime.now(WIB)
    output = {
        'status': overall,
        'timestamp': now_wib.isoformat(),
        'checks': checks,
    }

    # JSON output mode
    if args.json:
        print(json.dumps(output, indent=2 if not args.quiet else None))
        sys.exit(exit_code)

    # Human-readable output
    print(f"🏥 Ochobot Health: {overall.upper()}")

    if checks['bot']['ok']:
        print(f"   Bot API: OK (@{checks['bot'].get('bot_username', '?')}, "
              f"{checks['bot']['response_time_ms']}ms)")
    else:
        print(f"   Bot API: DOWN — {checks['bot']['error']}")

    print(f"   Database: {'OK' if db_ok else 'FAIL'} "
          f"({checks['database']['size_mb']} MB"
          + (f", WAL {checks['database']['wal_size_mb']} MB)" if checks['database']['wal_size_mb'] > 0 else ")"))
    if checks['database'].get('warning'):
        print(f"   ⚠️  {checks['database']['warning']}")

    lock_info = checks['bot_lock']
    if lock_info['pid']:
        alive_str = 'running' if lock_info['alive'] else 'DEAD PID'
        print(f"   Bot lock: PID {lock_info['pid']} ({alive_str})")
    else:
        print(f"   Bot lock: {lock_info['error'] or 'not found'}")

    print(f"   Disk: {checks['disk']['pct_free']}% free "
          f"({checks['disk']['free_mb']:.0f} MB free)")

    if overall == 'down' and admin_chat_id:
        state = load_state()
        consecutive_failures = state.get('consecutive_failures', 0) + 1
        state['consecutive_failures'] = consecutive_failures
        save_state(state)

        should_alert = consecutive_failures == 1 or consecutive_failures >= 30
        if should_alert:
            svc_ok = get_bot_status_from_systemd()
            svc_status = "RUNNING" if svc_ok else "STOPPED"
            msg = (
                f"🔴 <b>Bot Ochobot DOWN!</b>\n\n"
                f"🕐 {now_wib.strftime('%d/%m %H:%M')} WIB\n"
                f"⏱️ Gagal #{consecutive_failures}x\n"
                f"⚙️ Service: {svc_status}\n\n"
                f"<i>Cek: journalctl -u bot-saham -n 30</i>"
            )
            send_alert(bot_token, admin_chat_id, msg)
            log.info(f"Alert sent for failure #{consecutive_failures}")
    elif overall != 'down':
        state = load_state()
        if state.get('consecutive_failures', 0) > 0:
            log.info(f"Bot recovered after {state['consecutive_failures']} failures")
        state['consecutive_failures'] = 0
        save_state(state)

    if not args.skip_market_check and not is_market_hours():
        log.info("Outside market hours, no alert needed")

    log.info(f"Health check: {overall} | exit={exit_code}")
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
