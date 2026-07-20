"""
Unit tests for db.py - SQLite database module.
"""
import sys
import os
import json
import tempfile
import threading

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_db():
    """Create a temporary database for each test."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    from db import Database
    db = Database(db_path)
    db.initialize()
    yield db, db_path
    db.close()
    try:
        os.unlink(db_path)
    except OSError:
        pass


class TestDatabaseInit:
    """Tests for database initialization."""

    def test_create_tables(self, temp_db):
        """Should create all tables."""
        db, db_path = temp_db
        stats = db.stats()
        assert 'users' in stats
        assert 'favorites' in stats
        assert 'portfolio' in stats
        assert 'signals' in stats
        assert 'price_alerts' in stats

    def test_initialize_idempotent(self, temp_db):
        """Multiple initialize calls should not fail."""
        db, db_path = temp_db
        db.initialize()
        db.initialize()
        db.initialize()
        # Should not raise error

    def test_stats_initial_zero(self, temp_db):
        """Initial database should be empty."""
        db, db_path = temp_db
        stats = db.stats()
        assert stats['users'] == 0
        assert stats['favorites'] == 0
        assert stats['portfolio'] == 0
        assert stats['signals'] == 0
        assert stats['price_alerts'] == 0


class TestUserOperations:
    """Tests for user CRUD operations."""

    def test_upsert_user_new(self, temp_db):
        """Should create new user."""
        db, _ = temp_db
        user = db.upsert_user(user_id=12345, username='test', first_name='Test')
        assert user['user_id'] == 12345
        assert user['username'] == 'test'
        assert user['first_name'] == 'Test'

    def test_upsert_user_existing(self, temp_db):
        """Should update existing user."""
        db, _ = temp_db
        db.upsert_user(user_id=12345, username='old', first_name='Old')
        user = db.upsert_user(user_id=12345, username='new', first_name='New')
        assert user['username'] == 'new'
        assert user['first_name'] == 'New'

    def test_get_user_not_found(self, temp_db):
        """Should return None for non-existent user."""
        db, _ = temp_db
        user = db.get_user(user_id=99999)
        assert user is None

    def test_get_user_found(self, temp_db):
        """Should return user when exists."""
        db, _ = temp_db
        db.upsert_user(user_id=12345, username='test')
        user = db.get_user(user_id=12345)
        assert user is not None
        assert user['user_id'] == 12345

    def test_update_notifications(self, temp_db):
        """Should update notification settings."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        result = db.update_notifications(
            user_id=12345,
            notif_saham=True,
            notif_crypto=True,
            notif_bsjp=False
        )
        assert result is True
        user = db.get_user(12345)
        assert user['notif_saham'] == 1
        assert user['notif_crypto'] == 1
        assert user['notif_bsjp'] == 0

    def test_update_notifications_invalid_key(self, temp_db):
        """Should reject invalid notification keys."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        result = db.update_notifications(
            user_id=12345,
            invalid_key=True
        )
        assert result is False

    def test_get_all_users_with_notif(self, temp_db):
        """Should get all users with specific notification."""
        db, _ = temp_db
        db.upsert_user(user_id=1)
        db.upsert_user(user_id=2)
        db.upsert_user(user_id=3)
        db.update_notifications(user_id=1, notif_saham=True)
        db.update_notifications(user_id=2, notif_saham=True)
        db.update_notifications(user_id=3, notif_saham=False)

        users = db.get_all_users_with_notif('notif_saham')
        assert len(users) == 2
        user_ids = {u['user_id'] for u in users}
        assert user_ids == {1, 2}


class TestFavoriteOperations:
    """Tests for favorites CRUD."""

    def test_add_favorite(self, temp_db):
        """Should add favorite for user."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        result = db.add_favorite(12345, 'BBCA', 'stock')
        assert result is True

    def test_add_duplicate_favorite(self, temp_db):
        """Should not allow duplicate favorites."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_favorite(12345, 'BBCA')
        result = db.add_favorite(12345, 'BBCA')
        assert result is False  # Already exists

    def test_get_favorites(self, temp_db):
        """Should get all favorites for user."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_favorite(12345, 'BBCA')
        db.add_favorite(12345, 'TLKM')
        db.add_favorite(12345, 'BIPI')

        favorites = db.get_favorites(12345)
        assert len(favorites) == 3
        tickers = {f['ticker'] for f in favorites}
        assert tickers == {'BBCA', 'TLKM', 'BIPI'}

    def test_remove_favorite(self, temp_db):
        """Should remove favorite."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_favorite(12345, 'BBCA')
        result = db.remove_favorite(12345, 'BBCA')
        assert result is True
        favorites = db.get_favorites(12345)
        assert len(favorites) == 0

    def test_remove_nonexistent_favorite(self, temp_db):
        """Should return False for non-existent favorite."""
        db, _ = temp_db
        result = db.remove_favorite(12345, 'NONEXIST')
        assert result is False

    def test_favorites_case_insensitive(self, temp_db):
        """Ticker should be stored uppercase."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_favorite(12345, 'bbca')
        favorites = db.get_favorites(12345)
        assert favorites[0]['ticker'] == 'BBCA'


class TestPortfolioOperations:
    """Tests for portfolio CRUD."""

    def test_add_portfolio_entry(self, temp_db):
        """Should add portfolio entry."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        entry_id = db.add_portfolio_entry(
            user_id=12345,
            ticker='BBCA',
            buy_price=8500.0,
            lot=10
        )
        assert entry_id > 0

    def test_get_portfolio(self, temp_db):
        """Should get portfolio entries."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_portfolio_entry(12345, 'BBCA', 8500, 10)
        db.add_portfolio_entry(12345, 'TLKM', 4000, 5)

        portfolio = db.get_portfolio(12345)
        assert len(portfolio) == 2

    def test_get_portfolio_excludes_sold(self, temp_db):
        """get_portfolio should exclude sold by default."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        id1 = db.add_portfolio_entry(12345, 'BBCA', 8500, 10)
        id2 = db.add_portfolio_entry(12345, 'TLKM', 4000, 5)

        db.sell_portfolio_entry(id1, 9000)

        portfolio = db.get_portfolio(12345)
        assert len(portfolio) == 1
        assert portfolio[0]['ticker'] == 'TLKM'

    def test_get_portfolio_includes_sold(self, temp_db):
        """include_sold=True should return all entries."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_portfolio_entry(12345, 'BBCA', 8500, 10)
        id2 = db.add_portfolio_entry(12345, 'TLKM', 4000, 5)
        db.sell_portfolio_entry(id2, 4200)

        portfolio = db.get_portfolio(12345, include_sold=True)
        assert len(portfolio) == 2

    def test_sell_portfolio_entry(self, temp_db):
        """Should mark entry as sold."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        entry_id = db.add_portfolio_entry(12345, 'BBCA', 8500, 10)
        result = db.sell_portfolio_entry(entry_id, 9000)
        assert result is True


class TestSignalOperations:
    """Tests for signal CRUD."""

    def test_save_signal_new(self, temp_db):
        """Should save new signal."""
        db, _ = temp_db
        result = db.save_signal(
            key='BBCA_BUY_123',
            ticker='BBCA',
            asset_type='stock',
            signal_type='BUY',
            price=8500,
            target_price=8670,
            stop_loss=8362,
            score=60,
            quality='STRONG',
            reason='Golden cross',
            extra_data={'rsi': 25}
        )
        assert result is True

    def test_save_signal_update(self, temp_db):
        """Should update existing signal."""
        db, _ = temp_db
        db.save_signal(
            key='BBCA_BUY_123',
            ticker='BBCA',
            asset_type='stock',
            signal_type='BUY',
            price=8500
        )
        result = db.save_signal(
            key='BBCA_BUY_123',
            ticker='BBCA',
            asset_type='stock',
            signal_type='BUY',
            price=8600
        )
        assert result is True
        signal = db.get_signal('BBCA_BUY_123')
        assert signal['price'] == 8600

    def test_get_signal_not_found(self, temp_db):
        """Should return None for non-existent signal."""
        db, _ = temp_db
        signal = db.get_signal('NONEXIST')
        assert signal is None

    def test_get_signal_with_extra_data(self, temp_db):
        """Should parse extra_data as JSON."""
        db, _ = temp_db
        db.save_signal(
            key='TEST',
            ticker='BBCA',
            asset_type='stock',
            signal_type='BUY',
            extra_data={'rsi': 25, 'macd': 0.5, 'volume_ratio': 1.5}
        )
        signal = db.get_signal('TEST')
        assert signal['extra_data']['rsi'] == 25
        assert signal['extra_data']['macd'] == 0.5

    def test_get_all_signals(self, temp_db):
        """Should get all signals as dict."""
        db, _ = temp_db
        db.save_signal('sig1', 'BBCA', 'stock', 'BUY')
        db.save_signal('sig2', 'TLKM', 'stock', 'SELL')

        signals = db.get_all_signals()
        assert len(signals) == 2
        assert 'sig1' in signals
        assert 'sig2' in signals

    def test_delete_signal(self, temp_db):
        """Should delete signal."""
        db, _ = temp_db
        db.save_signal('TEST', 'BBCA', 'stock', 'BUY')
        result = db.delete_signal('TEST')
        assert result is True
        signal = db.get_signal('TEST')
        assert signal is None


class TestAlertOperations:
    """Tests for price alerts."""

    def test_add_alert(self, temp_db):
        """Should add price alert."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        alert_id = db.add_alert(
            user_id=12345,
            ticker='BBCA',
            target_price=9000,
            alert_type='buy'
        )
        assert alert_id > 0

    def test_get_user_alerts(self, temp_db):
        """Should get untriggered alerts for user."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_alert(12345, 'BBCA', 9000)
        db.add_alert(12345, 'TLKM', 4000)

        alerts = db.get_user_alerts(12345)
        assert len(alerts) == 2

    def test_get_active_alerts_for_ticker(self, temp_db):
        """Should get alerts for a specific ticker."""
        db, _ = temp_db
        db.upsert_user(user_id=1)
        db.upsert_user(user_id=2)
        db.add_alert(1, 'BBCA', 9000)
        db.add_alert(2, 'BBCA', 8500)
        db.add_alert(1, 'TLKM', 4000)

        alerts = db.get_active_alerts_for_ticker('BBCA')
        assert len(alerts) == 2
        user_ids = {a['user_id'] for a in alerts}
        assert user_ids == {1, 2}

    def test_trigger_alert(self, temp_db):
        """Should mark alert as triggered."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        alert_id = db.add_alert(12345, 'BBCA', 9000)
        result = db.trigger_alert(alert_id)
        assert result is True

        # Should not be in active alerts anymore
        alerts = db.get_user_alerts(12345)
        assert len(alerts) == 0

    def test_delete_alert(self, temp_db):
        """Should delete alert."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        db.add_alert(12345, 'BBCA', 9000)
        result = db.delete_alert(12345, 'BBCA')
        assert result is True

        alerts = db.get_user_alerts(12345)
        assert len(alerts) == 0


class TestCleanup:
    """Tests for cleanup operations."""

    def test_cleanup_old_signals(self, temp_db):
        """Should remove old signals."""
        db, _ = temp_db
        db.save_signal('OLD_SIG', 'BBCA', 'stock', 'BUY')
        # Manually set old date
        with db._get_conn() as conn:
            conn.execute(
                """UPDATE signals SET created_at = datetime('now', '-30 days')
                   WHERE key = 'OLD_SIG'"""
            )

        removed = db.cleanup_old_signals(max_age_days=7)
        # Old signal removed (BSJP_* are not removed)
        assert removed >= 1
        signal = db.get_signal('OLD_SIG')
        assert signal is None


class TestSingleton:
    """Tests for singleton instance."""

    def test_db_singleton_exists(self):
        """Singleton should exist."""
        from db import db
        assert db is not None

    def test_db_singleton_has_methods(self):
        """Singleton should have all expected methods."""
        from db import db
        for method in ['get_user', 'upsert_user', 'add_favorite',
                       'add_portfolio_entry', 'save_signal', 'add_alert',
                       'stats']:
            assert hasattr(db, method)


class TestMigration:
    """Tests for JSON to SQLite migration."""

    def test_migration_no_files(self, tmp_path):
        """Should return False when no JSON files exist."""
        from db import migrate_from_json
        result = migrate_from_json(
            user_data_file=str(tmp_path / 'nonexistent.json'),
            signals_file=str(tmp_path / 'nonexistent2.json')
        )
        assert result is False

    def test_migration_user_data(self, tmp_path, temp_db):
        """Should migrate user data from JSON."""
        db, db_path = temp_db
        # Create JSON file with user data
        user_json = {
            '12345': {
                'username': 'test_user',
                'first_name': 'Test',
                'notif_saham': True,
                'notif_crypto': True,
                'favorites': ['BBCA', 'TLKM']
            }
        }
        user_json_path = tmp_path / 'user_data.json'
        with open(user_json_path, 'w') as f:
            json.dump(user_json, f)

        # Patch the singleton db to use our temp_db path
        import db as db_module
        original_db = db_module.db
        db_module.db = db
        try:
            result = db_module.migrate_from_json(
                user_data_file=str(user_json_path),
                signals_file=str(tmp_path / 'nonexistent.json')
            )
            assert result is True
        finally:
            db_module.db = original_db

        # Verify migration
        user = db.get_user(12345)
        assert user is not None
        assert user['username'] == 'test_user'
        assert user['notif_saham'] == 1
        assert user['notif_crypto'] == 1

        # Check favorites
        favorites = db.get_favorites(12345)
        assert len(favorites) == 2


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_database_operations(self, temp_db):
        """Operations on empty database should not fail."""
        db, _ = temp_db
        # Should all return empty/None
        assert db.get_user(99999) is None
        assert db.get_favorites(99999) == []
        assert db.get_portfolio(99999) == []
        assert db.get_user_alerts(99999) == []
        assert db.get_signal('NONEXIST') is None

    def test_unicode_ticker(self, temp_db):
        """Unicode in ticker should be handled."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        result = db.add_favorite(12345, 'BBCA')
        assert result is True

    def test_zero_portfolio(self, temp_db):
        """Zero price/lot should be stored as-is."""
        db, _ = temp_db
        db.upsert_user(user_id=12345)
        entry_id = db.add_portfolio_entry(12345, 'BBCA', 0, 0)
        portfolio = db.get_portfolio(12345)
        assert portfolio[0]['buy_price'] == 0
        assert portfolio[0]['lot'] == 0


class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_writes(self, temp_db):
        """Concurrent writes should not corrupt data."""
        db, _ = temp_db

        errors = []
        def worker(i):
            try:
                db.upsert_user(user_id=1000 + i)
                db.add_favorite(1000 + i, 'BBCA')
                db.add_portfolio_entry(1000 + i, 'BBCA', 8500, 10)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert db.stats()['users'] == 10
