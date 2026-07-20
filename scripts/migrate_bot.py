"""
Bot migration utility.
Migrate from old bot token to new bot token with user notification.

Usage:
    python scripts/migrate_bot.py backup          # Backup database
    python scripts/migrate_bot.py broadcast       # Broadcast migration notice
    python scripts/migrate_bot.py full           # Full migration workflow

Configuration:
    Set OLD_BOT_TOKEN, NEW_BOT_TOKEN, MIGRATION_MESSAGE in environment
    or edit this file directly.
"""
import os
import sys
import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Bot
from db import db

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================================
# CONFIGURATION - Edit these values for your migration
# ============================================================
OLD_BOT_TOKEN = os.getenv('OLD_BOT_TOKEN', '')
NEW_BOT_TOKEN = os.getenv('NEW_BOT_TOKEN', '')
NEW_BOT_USERNAME = os.getenv('NEW_BOT_USERNAME', '@SignalOchobot')
MIGRATION_MESSAGE = (
    "🔄 *Bot Pindah!*\n\n"
    "Ochobot sudah pindah ke bot baru.\n\n"
    f"👉 Silakan mulai ulang di: {NEW_BOT_USERNAME}\n\n"
    "Data trading Anda (favorites, portfolio, alerts) "
    "tetap aman dan akan tersedia setelah /start di bot baru.\n\n"
    "Mohon maaf atas ketidaknyamanannya."
)


def backup_database() -> str:
    """Create a timestamped backup of the database."""
    db_path = 'ochobot.db'
    if not os.path.exists(db_path):
        logger.warning(f"Database file {db_path} not found, nothing to backup")
        return None

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f'ochobot_backup_{timestamp}.db'

    shutil.copy2(db_path, backup_path)
    file_size = os.path.getsize(backup_path)
    logger.info(f"✓ Database backed up: {backup_path} ({file_size:,} bytes)")
    return backup_path


def get_user_count() -> int:
    """Get total user count."""
    db.initialize()
    return db.stats().get('users', 0)


async def broadcast_migration_notice(token: str):
    """Send migration notice to all users using the old bot."""
    if not token:
        logger.error("OLD_BOT_TOKEN not set. Set environment variable or edit script.")
        return

    bot = Bot(token=token)
    db.initialize()

    users = db.get_all_users_with_notif('notif_saham')
    total_users = db.stats().get('users', 0)
    logger.info(f"Found {total_users} users, {len(users)} have notifications enabled")

    sent = 0
    failed = 0

    for user in users:
        try:
            await bot.send_message(
                chat_id=user['user_id'],
                text=MIGRATION_MESSAGE,
                parse_mode='Markdown'
            )
            sent += 1
            logger.info(f"✓ Sent to user {user['user_id']}")
        except Exception as e:
            failed += 1
            logger.warning(f"✗ Failed for user {user['user_id']}: {e}")

        # Rate limit: 25 messages per second
        await asyncio.sleep(0.04)

    logger.info(f"Broadcast complete: {sent} sent, {failed} failed")
    await bot.close()


def verify_new_bot(token: str) -> bool:
    """Verify new bot token works."""
    if not token:
        logger.error("NEW_BOT_TOKEN not set")
        return False

    async def check():
        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            logger.info(f"✓ Bot connected: @{me.username} ({me.first_name})")
            await bot.close()
            return True
        except Exception as e:
            logger.error(f"✗ Bot connection failed: {e}")
            await bot.close()
            return False

    return asyncio.run(check())


def full_migration_workflow():
    """Execute full migration workflow."""
    print("=" * 60)
    print("OCHOBOT MIGRATION WORKFLOW")
    print("=" * 60)

    # Step 1: Backup
    print("\n[1/4] Backing up database...")
    backup_path = backup_database()
    if not backup_path:
        print("⚠️  No database to backup (will create new one)")

    # Step 2: Stats before migration
    print("\n[2/4] Current database stats...")
    try:
        db.initialize()
        stats = db.stats()
        print(f"   Users: {stats['users']}")
        print(f"   Favorites: {stats['favorites']}")
        print(f"   Portfolio: {stats['portfolio']}")
        print(f"   Signals: {stats['signals']}")
        print(f"   Price Alerts: {stats['price_alerts']}")
    except Exception as e:
        print(f"   Error: {e}")

    # Step 3: Verify new bot
    print("\n[3/4] Verifying new bot token...")
    if not verify_new_bot(NEW_BOT_TOKEN):
        print("❌ New bot verification failed. Check NEW_BOT_TOKEN.")
        return False

    # Step 4: Broadcast
    if OLD_BOT_TOKEN:
        print("\n[4/4] Broadcasting migration notice via old bot...")
        print(f"   Message: {MIGRATION_MESSAGE[:80]}...")
        try:
            asyncio.run(broadcast_migration_notice(OLD_BOT_TOKEN))
            print("✓ Broadcast complete")
        except Exception as e:
            print(f"✗ Broadcast failed: {e}")
            print("   You can run this step manually later:")
            print("   python scripts/migrate_bot.py broadcast")
    else:
        print("\n[4/4] OLD_BOT_TOKEN not set, skipping broadcast")
        print("   Run manually: OLD_BOT_TOKEN=... python scripts/migrate_bot.py broadcast")

    # Final summary
    print("\n" + "=" * 60)
    print("MIGRATION READY")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Stop the old bot (Ctrl+C or systemctl stop)")
    print("2. Update .env with new token:")
    print(f"   TELEGRAM_BOT_TOKEN={NEW_BOT_TOKEN[:20]}...")
    print("3. Start the new bot:")
    print("   python main.py")
    print("4. Verify with /start in Telegram")
    print("5. (Optional) Delete old bot via @BotFather")

    return True


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nCurrent configuration:")
        print(f"  OLD_BOT_TOKEN: {'SET' if OLD_BOT_TOKEN else 'NOT SET'}")
        print(f"  NEW_BOT_TOKEN: {'SET' if NEW_BOT_TOKEN else 'NOT SET'}")
        print(f"  NEW_BOT_USERNAME: {NEW_BOT_USERNAME}")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == 'backup':
        backup_database()
    elif command == 'broadcast':
        if not OLD_BOT_TOKEN:
            print("Error: OLD_BOT_TOKEN not set")
            print("Usage: OLD_BOT_TOKEN=your_old_token python scripts/migrate_bot.py broadcast")
            sys.exit(1)
        asyncio.run(broadcast_migration_notice(OLD_BOT_TOKEN))
    elif command == 'verify':
        if not NEW_BOT_TOKEN:
            print("Error: NEW_BOT_TOKEN not set")
            sys.exit(1)
        success = verify_new_bot(NEW_BOT_TOKEN)
        sys.exit(0 if success else 1)
    elif command == 'full':
        full_migration_workflow()
    elif command == 'stats':
        db.initialize()
        stats = db.stats()
        for key, val in stats.items():
            print(f"  {key}: {val}")
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
