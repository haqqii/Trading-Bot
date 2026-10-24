"""Timezone helpers shared across the bot.

The bot runs inside a Docker container whose local timezone is usually UTC
(the base images don't ship a TZ and nothing sets ``TZ=Asia/Jakarta``).
``datetime.now()`` therefore returns **UTC**, not WIB — using it for a
timestamp that is labelled "WIB" makes every message show a time that is
exactly 7 hours behind the user's clock.

Always convert from UTC explicitly instead of relying on the host timezone.
"""
from datetime import datetime, timezone, timedelta

# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))


def now_wib() -> datetime:
    """Get current time in WIB (UTC+7), regardless of the host timezone."""
    return datetime.now(timezone.utc).astimezone(WIB)


def wib_strftime(fmt: str) -> str:
    """Format the current WIB time with ``fmt``."""
    return now_wib().strftime(fmt)
