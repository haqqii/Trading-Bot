"""
SQLite database module for Ochobot.
Replaces JSON file storage with atomic, crash-safe SQLite database.

Features:
- Atomic writes (SQLite transactions)
- Concurrent access via WAL mode
- Thread-safe connection handling
- Automatic schema migrations
"""
import os
import sqlite3
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

DB_FILE = 'ochobot.db'

# Default notification settings for new users
DEFAULT_NOTIFICATIONS = {
    'notif_saham': True,
    'notif_crypto': False,
    'notif_bsjp': False,
    'notif_morning': False,
    'notif_alert_favorit': False,
}


class Database:
    """SQLite database wrapper with thread-safe operations."""

    def __init__(self, db_path: str = DB_FILE):
        self.db_path = db_path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def _get_conn(self):
        """Get thread-local connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                isolation_level=None,  # autocommit mode
                check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            # Enable WAL mode for concurrent reads
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        yield self._local.conn

    def initialize(self):
        """Create tables if they don't exist."""
        with self._init_lock:
            if self._initialized:
                return
            self._initialized = True
            self._create_tables()

    def _create_tables(self):
        """Create all required tables."""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    notif_saham INTEGER DEFAULT 1,
                    notif_crypto INTEGER DEFAULT 0,
                    notif_bsjp INTEGER DEFAULT 0,
                    notif_morning INTEGER DEFAULT 0,
                    notif_alert_favorit INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    asset_type TEXT NOT NULL DEFAULT 'stock',
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, ticker),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);

                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    buy_price REAL NOT NULL,
                    lot INTEGER NOT NULL,
                    buy_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sold INTEGER DEFAULT 0,
                    sell_price REAL,
                    sell_date TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_user ON portfolio(user_id);

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    ticker TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    price REAL,
                    target_price REAL,
                    stop_loss REAL,
                    score REAL,
                    quality TEXT,
                    reason TEXT,
                    extra_data TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
                CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

                CREATE TABLE IF NOT EXISTS price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    alert_type TEXT NOT NULL DEFAULT 'buy',
                    triggered INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_alerts_user ON price_alerts(user_id);
                CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON price_alerts(ticker);
            """)

    # === USER OPERATIONS ===

    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID."""
        self.initialize()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def upsert_user(self, user_id: int, username: str = None,
                    first_name: str = None) -> Dict:
        """Create or update user, returns the user dict."""
        self.initialize()
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO users
                       (user_id, username, first_name)
                       VALUES (?, ?, ?)""",
                    (user_id, username, first_name)
                )
            else:
                conn.execute(
                    """UPDATE users SET username = ?, first_name = ?,
                       updated_at = CURRENT_TIMESTAMP
                       WHERE user_id = ?""",
                    (username, first_name, user_id)
                )
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row)

    def update_notifications(self, user_id: int, **kwargs) -> bool:
        """Update notification settings for a user."""
        self.initialize()
        if not kwargs:
            return False
        # Whitelist allowed columns
        allowed = {'notif_saham', 'notif_crypto', 'notif_bsjp',
                   'notif_morning', 'notif_alert_favorit'}
        sets = []
        values = []
        for key, val in kwargs.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                values.append(1 if val else 0)
        if not sets:
            return False
        values.append(user_id)
        with self._get_conn() as conn:
            conn.execute(
                f"UPDATE users SET {', '.join(sets)}, "
                f"updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                values
            )
        return True

    def get_all_users_with_notif(self, notif_key: str) -> List[Dict]:
        """Get all users with a specific notification enabled."""
        self.initialize()
        if notif_key not in DEFAULT_NOTIFICATIONS:
            return []
        with self._get_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM users WHERE {notif_key} = 1"
            ).fetchall()
            return [dict(r) for r in rows]

    # === FAVORITES OPERATIONS ===

    def add_favorite(self, user_id: int, ticker: str,
                     asset_type: str = 'stock') -> bool:
        """Add a favorite for a user."""
        self.initialize()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO favorites (user_id, ticker, asset_type)
                       VALUES (?, ?, ?)""",
                    (user_id, ticker.upper(), asset_type)
                )
                return True
            except sqlite3.IntegrityError:
                return False  # Already exists

    def remove_favorite(self, user_id: int, ticker: str) -> bool:
        """Remove a favorite from a user."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND ticker = ?",
                (user_id, ticker.upper())
            )
            return cursor.rowcount > 0

    def get_favorites(self, user_id: int) -> List[Dict]:
        """Get all favorites for a user."""
        self.initialize()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM favorites WHERE user_id = ? ORDER BY added_at DESC",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # === PORTFOLIO OPERATIONS ===

    def add_portfolio_entry(self, user_id: int, ticker: str,
                            buy_price: float, lot: int) -> int:
        """Add a portfolio entry, returns the entry ID."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO portfolio (user_id, ticker, buy_price, lot)
                   VALUES (?, ?, ?, ?)""",
                (user_id, ticker.upper(), buy_price, lot)
            )
            return cursor.lastrowid

    def get_portfolio(self, user_id: int, include_sold: bool = False) -> List[Dict]:
        """Get portfolio entries for a user."""
        self.initialize()
        with self._get_conn() as conn:
            if include_sold:
                rows = conn.execute(
                    """SELECT * FROM portfolio WHERE user_id = ?
                       ORDER BY buy_date DESC""",
                    (user_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM portfolio WHERE user_id = ? AND sold = 0
                       ORDER BY buy_date DESC""",
                    (user_id,)
                ).fetchall()
            return [dict(r) for r in rows]

    def sell_portfolio_entry(self, entry_id: int, sell_price: float) -> bool:
        """Mark a portfolio entry as sold."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """UPDATE portfolio SET sold = 1, sell_price = ?,
                   sell_date = CURRENT_TIMESTAMP WHERE id = ?""",
                (sell_price, entry_id)
            )
            return cursor.rowcount > 0

    # === SIGNAL OPERATIONS ===

    def save_signal(self, key: str, ticker: str, asset_type: str,
                    signal_type: str, price: float = None,
                    target_price: float = None, stop_loss: float = None,
                    score: float = None, quality: str = None,
                    reason: str = None, extra_data: dict = None) -> bool:
        """Save or update a signal."""
        self.initialize()
        extra_json = json.dumps(extra_data) if extra_data else None
        with self._get_conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO signals
                       (key, ticker, asset_type, signal_type, price,
                        target_price, stop_loss, score, quality, reason,
                        extra_data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, ticker.upper(), asset_type, signal_type, price,
                     target_price, stop_loss, score, quality, reason,
                     extra_json)
                )
                return True
            except sqlite3.IntegrityError:
                # Key exists, update it
                conn.execute(
                    """UPDATE signals SET ticker = ?, asset_type = ?,
                       signal_type = ?, price = ?, target_price = ?,
                       stop_loss = ?, score = ?, quality = ?, reason = ?,
                       extra_data = ?, created_at = CURRENT_TIMESTAMP
                       WHERE key = ?""",
                    (ticker.upper(), asset_type, signal_type, price,
                     target_price, stop_loss, score, quality, reason,
                     extra_json, key)
                )
                return True

    def get_signal(self, key: str) -> Optional[Dict]:
        """Get a signal by key."""
        self.initialize()
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM signals WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            if data.get('extra_data'):
                try:
                    data['extra_data'] = json.loads(data['extra_data'])
                except json.JSONDecodeError:
                    data['extra_data'] = None
            return data

    def get_all_signals(self) -> Dict[str, Dict]:
        """Get all signals as a dict keyed by signal key."""
        self.initialize()
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM signals").fetchall()
            result = {}
            for row in rows:
                data = dict(row)
                if data.get('extra_data'):
                    try:
                        data['extra_data'] = json.loads(data['extra_data'])
                    except json.JSONDecodeError:
                        data['extra_data'] = None
                result[data['key']] = data
            return result

    def delete_signal(self, key: str) -> bool:
        """Delete a signal by key."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM signals WHERE key = ?", (key,)
            )
            return cursor.rowcount > 0

    def cleanup_old_signals(self, max_age_days: int = 7,
                            max_per_type: int = 50) -> int:
        """Remove old signals beyond max_age, and trim each type to max_per_type."""
        self.initialize()
        removed = 0
        with self._get_conn() as conn:
            # Remove old signals
            cursor = conn.execute(
                """DELETE FROM signals
                   WHERE created_at < datetime('now', ?)
                   AND key NOT LIKE 'BSJP_%'""",
                (f'-{max_age_days} days',)
            )
            removed += cursor.rowcount

            # Trim per type (keep most recent)
            for signal_type in ['stock', 'crypto']:
                cursor = conn.execute(
                    """DELETE FROM signals WHERE id IN (
                       SELECT id FROM signals WHERE asset_type = ?
                       ORDER BY created_at DESC
                       LIMIT -1 OFFSET ?
                   )""",
                    (signal_type, max_per_type)
                )
                removed += cursor.rowcount
        return removed

    # === PRICE ALERTS ===

    def add_alert(self, user_id: int, ticker: str, target_price: float,
                  alert_type: str = 'buy') -> int:
        """Add a price alert."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO price_alerts
                   (user_id, ticker, target_price, alert_type)
                   VALUES (?, ?, ?, ?)""",
                (user_id, ticker.upper(), target_price, alert_type)
            )
            return cursor.lastrowid

    def get_user_alerts(self, user_id: int) -> List[Dict]:
        """Get all untriggered alerts for a user."""
        self.initialize()
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM price_alerts WHERE user_id = ?
                   AND triggered = 0 ORDER BY created_at DESC""",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_alert(self, user_id: int, ticker: str) -> bool:
        """Delete a price alert."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """DELETE FROM price_alerts
                   WHERE user_id = ? AND ticker = ?""",
                (user_id, ticker.upper())
            )
            return cursor.rowcount > 0

    def get_active_alerts_for_ticker(self, ticker: str) -> List[Dict]:
        """Get all active alerts for a specific ticker."""
        self.initialize()
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM price_alerts WHERE ticker = ?
                   AND triggered = 0""",
                (ticker.upper(),)
            ).fetchall()
            return [dict(r) for r in rows]

    def trigger_alert(self, alert_id: int) -> bool:
        """Mark an alert as triggered."""
        self.initialize()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE price_alerts SET triggered = 1 WHERE id = ?",
                (alert_id,)
            )
            return cursor.rowcount > 0

    # === STATISTICS ===

    def stats(self) -> Dict[str, int]:
        """Get database statistics."""
        self.initialize()
        with self._get_conn() as conn:
            stats = {}
            for table in ['users', 'favorites', 'portfolio', 'signals', 'price_alerts']:
                row = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM {table}"
                ).fetchone()
                stats[table] = row['cnt']
            return stats

    def close(self):
        """Close the connection for current thread."""
        if hasattr(self._local, 'conn') and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None


# Singleton instance
db = Database()


# === MIGRATION HELPERS ===

def migrate_from_json(user_data_file: str = 'user_data.json',
                     signals_file: str = 'last_signals.json') -> bool:
    """One-time migration from JSON files to SQLite.

    Returns True if migration was performed, False if files don't exist
    or migration already done.
    """
    if not os.path.exists(user_data_file) and not os.path.exists(signals_file):
        return False

    logger.info("Starting JSON to SQLite migration...")
    db.initialize()

    # Migrate user data
    if os.path.exists(user_data_file):
        try:
            with open(user_data_file, 'r', encoding='utf-8') as f:
                user_data = json.load(f)
            for user_id_str, user_info in user_data.items():
                try:
                    user_id = int(user_id_str)
                except (ValueError, TypeError):
                    continue

                # Create user
                db.upsert_user(
                    user_id=user_id,
                    username=user_info.get('username'),
                    first_name=user_info.get('first_name')
                )

                # Update notification settings
                notif_settings = {
                    k: user_info.get(k, DEFAULT_NOTIFICATIONS.get(k, False))
                    for k in DEFAULT_NOTIFICATIONS
                }
                db.update_notifications(user_id, **notif_settings)

                # Migrate favorites
                favorites = user_info.get('favorites', [])
                if isinstance(favorites, list):
                    for ticker in favorites:
                        if isinstance(ticker, str):
                            db.add_favorite(user_id, ticker)

            logger.info(f"Migrated {len(user_data)} users from JSON")
        except Exception as e:
            logger.error(f"User data migration failed: {e}")

    # Migrate signals
    if os.path.exists(signals_file):
        try:
            with open(signals_file, 'r', encoding='utf-8') as f:
                signals = json.load(f)
            for key, signal_data in signals.items():
                ticker = signal_data.get('ticker', 'UNKNOWN')
                signal_type = signal_data.get('signal_type', 'BUY')
                asset_type = signal_data.get('asset_type', 'stock')
                db.save_signal(
                    key=key,
                    ticker=ticker,
                    asset_type=asset_type,
                    signal_type=signal_type,
                    price=signal_data.get('price') or signal_data.get('entry'),
                    target_price=signal_data.get('target_price') or signal_data.get('tp'),
                    stop_loss=signal_data.get('stop_loss') or signal_data.get('sl'),
                    score=signal_data.get('score'),
                    quality=signal_data.get('quality'),
                    reason=signal_data.get('reason'),
                    extra_data={k: v for k, v in signal_data.items()
                                if k not in ['ticker', 'signal_type', 'asset_type',
                                              'price', 'target_price', 'stop_loss',
                                              'score', 'quality', 'reason',
                                              'entry', 'tp', 'sl']}
                )
            logger.info(f"Migrated {len(signals)} signals from JSON")
        except Exception as e:
            logger.error(f"Signals migration failed: {e}")

    logger.info("Migration complete!")
    return True
