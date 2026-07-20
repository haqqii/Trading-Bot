"""
Unit tests for handlers/command_handlers.py
Tests utility functions and helpers (no Telegram mock needed).
"""
import sys
import os
import json
import tempfile
import shutil

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestStripMarkdownChars:
    """Tests for _strip_markdown_chars function."""

    def test_empty_string(self):
        """Empty string should return empty."""
        from handlers.command_handlers import _strip_markdown_chars
        assert _strip_markdown_chars('') == ''

    def test_none_input(self):
        """None input should return None."""
        from handlers.command_handlers import _strip_markdown_chars
        assert _strip_markdown_chars(None) is None

    def test_plain_text(self):
        """Plain text without formatting should be unchanged."""
        from handlers.command_handlers import _strip_markdown_chars
        assert _strip_markdown_chars('Just plain text') == 'Just plain text'

    def test_strip_asterisks(self):
        """Asterisks should be stripped."""
        from handlers.command_handlers import _strip_markdown_chars
        assert _strip_markdown_chars('*bold*') == 'bold'

    def test_strip_underscores(self):
        """Underscores should be stripped."""
        from handlers.command_handlers import _strip_markdown_chars
        assert _strip_markdown_chars('_italic_') == 'italic'

    def test_strip_backticks(self):
        """Backticks should be stripped."""
        from handlers.command_handlers import _strip_markdown_chars
        assert _strip_markdown_chars('`code`') == 'code'

    def test_strip_brackets(self):
        """Brackets should be stripped."""
        from handlers.command_handlers import _strip_markdown_chars
        result = _strip_markdown_chars('[link](url)')
        # The function only strips `[`, not `]` and `(` `)`
        # So the result should have `[` removed but other chars remain
        assert '[' not in result
        assert 'link' in result

    def test_unescape_first(self):
        """Escaped chars should be unescaped then stripped."""
        from handlers.command_handlers import _strip_markdown_chars
        # \* becomes *, then * is stripped
        assert _strip_markdown_chars(r'\*literal*') == 'literal'

    def test_mixed_formatting(self):
        """Mixed formatting should be handled."""
        from handlers.command_handlers import _strip_markdown_chars
        result = _strip_markdown_chars('*bold* and _italic_ with `code`')
        assert '*' not in result
        assert '_' not in result
        assert '`' not in result


class TestAtomicWrite:
    """Tests for _atomic_write function."""

    def test_creates_new_file(self):
        """Should create new file with data."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            data = {'key': 'value', 'count': 5}
            _atomic_write(filepath, data)

            assert os.path.exists(filepath)
            with open(filepath, 'r') as f:
                loaded = json.load(f)
            assert loaded == data

    def test_overwrites_existing_file(self):
        """Should overwrite existing file."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')

            # Write first
            _atomic_write(filepath, {'v': 1})
            # Overwrite
            _atomic_write(filepath, {'v': 2})

            with open(filepath, 'r') as f:
                loaded = json.load(f)
            assert loaded['v'] == 2

    def test_creates_backup(self):
        """Should create backup of existing file."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            backup = filepath + '.bak'

            # First write
            _atomic_write(filepath, {'v': 1})
            # Second write should create backup
            _atomic_write(filepath, {'v': 2})

            assert os.path.exists(backup)
            with open(backup, 'r') as f:
                backup_data = json.load(f)
            assert backup_data['v'] == 1

    def test_handles_unicode(self):
        """Should handle unicode characters."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            data = {'name': 'BIPI', 'description': 'PT Astrindo Nusantara 🏢'}
            _atomic_write(filepath, data)

            with open(filepath, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            assert loaded['description'] == 'PT Astrindo Nusantara 🏢'

    def test_writes_indented(self):
        """Should write with indentation for readability."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            _atomic_write(filepath, {'key': 'value'})

            with open(filepath, 'r') as f:
                content = f.read()
            # Indented JSON should have newlines
            assert '\n' in content
            assert '  ' in content


class TestUserDataPersistence:
    """Tests for user_data persistence functions."""

    def test_load_user_data_missing_files(self, monkeypatch, tmp_path):
        """Should handle missing files gracefully."""
        # Change working directory to tmp
        monkeypatch.chdir(tmp_path)
        from handlers import command_handlers as ch
        import db as db_module

        # Reset DB singleton to use temp path
        original_db = db_module.db
        test_db_path = str(tmp_path / 'data' / 'test.db')
        (tmp_path / 'data').mkdir(exist_ok=True)
        new_db = db_module.Database(test_db_path)
        db_module.db = new_db
        ch.db = new_db  # Also update reference in command_handlers

        try:
            ch.user_data_db = {}
            ch.last_buy_signals = {}
            ch.load_user_data()
            # Should not crash, dicts remain empty (no users in fresh DB)
            assert ch.user_data_db == {}
            assert ch.last_buy_signals == {}
        finally:
            db_module.db = original_db
            ch.db = original_db

    def test_load_user_data_main_file(self, monkeypatch, tmp_path):
        """Should migrate from JSON to SQLite on first load."""
        monkeypatch.chdir(tmp_path)
        from handlers import command_handlers as ch
        import db as db_module

        # Create test data file
        test_data = {
            '12345': {
                'username': 'alice',
                'first_name': 'Alice',
                'notif_saham': True,
                'favorites': ['BBCA']
            }
        }
        with open('user_data.json', 'w') as f:
            json.dump(test_data, f)

        # Reset singleton DB to use temp file
        original_db = db_module.db
        test_db_path = str(tmp_path / 'data' / 'test_load.db')
        (tmp_path / 'data').mkdir(exist_ok=True)
        new_db = db_module.Database(test_db_path)
        db_module.db = new_db
        ch.db = new_db

        try:
            ch.user_data_db = {}
            ch.last_buy_signals = {}
            ch.load_user_data()
            # Should have migrated the data
            user = db_module.db.get_user(12345)
            assert user is not None
            assert user['username'] == 'alice'
        finally:
            db_module.db = original_db
            ch.db = original_db

    def test_load_user_data_fallback_to_backup(self, monkeypatch, tmp_path):
        """Should fallback to backup during migration when main file is corrupt."""
        monkeypatch.chdir(tmp_path)
        from handlers import command_handlers as ch
        import db as db_module

        # Write corrupt main file
        with open('user_data.json', 'w') as f:
            f.write('NOT VALID JSON {{{')

        # Write valid backup
        backup_data = {
            '67890': {
                'username': 'bob',
                'first_name': 'Bob',
                'notif_saham': True
            }
        }
        with open('user_data.json.bak', 'w') as f:
            json.dump(backup_data, f)

        # Reset singleton DB to use temp file
        original_db = db_module.db
        test_db_path = str(tmp_path / 'data' / 'test_backup.db')
        (tmp_path / 'data').mkdir(exist_ok=True)
        new_db = db_module.Database(test_db_path)
        db_module.db = new_db
        ch.db = new_db

        try:
            ch.user_data_db = {}
            ch.last_buy_signals = {}
            # Should not crash even with corrupt JSON
            ch.load_user_data()
        finally:
            db_module.db = original_db
            ch.db = original_db

    def test_load_user_data_both_corrupt(self, monkeypatch, tmp_path):
        """Should handle both files corrupt."""
        monkeypatch.chdir(tmp_path)
        from handlers import command_handlers as ch
        import db as db_module

        # Write corrupt main file
        with open('user_data.json', 'w') as f:
            f.write('NOT VALID JSON {{{')
        # Write corrupt backup
        with open('user_data.json.bak', 'w') as f:
            f.write('ALSO NOT VALID')

        # Reset DB singleton to use temp path
        original_db = db_module.db
        test_db_path = str(tmp_path / 'data' / 'test_corrupt.db')
        (tmp_path / 'data').mkdir(exist_ok=True)
        new_db = db_module.Database(test_db_path)
        db_module.db = new_db
        ch.db = new_db

        try:
            ch.user_data_db = {}
            ch.last_buy_signals = {}
            ch.load_user_data()
            # Should not crash, dict should be empty (no users)
            assert ch.user_data_db == {}
        finally:
            db_module.db = original_db
            ch.db = original_db


class TestSignalDatetimeConversion:
    """Tests for datetime conversion in signals."""

    def test_string_datetime_converted(self, monkeypatch, tmp_path):
        """String datetime should be converted to datetime object."""
        monkeypatch.chdir(tmp_path)
        from handlers import command_handlers as ch
        import db as db_module
        from datetime import datetime

        # Reset DB singleton to use temp path
        original_db = db_module.db
        test_db_path = str(tmp_path / 'data' / 'test_signal.db')
        (tmp_path / 'data').mkdir(exist_ok=True)
        new_db = db_module.Database(test_db_path)
        db_module.db = new_db
        ch.db = new_db

        try:
            # Write test signals with string datetime
            signals = {
                'signal1': {
                    'ticker': 'BBCA',
                    'time': '2026-07-20T10:30:00',
                    'price': 8500
                }
            }
            with open('last_signals.json', 'w') as f:
                json.dump(signals, f)

            ch.last_buy_signals = {}
            ch.load_user_data()

            # Datetime should be converted
            assert isinstance(ch.last_buy_signals['signal1']['time'], datetime)
        finally:
            db_module.db = original_db
            ch.db = original_db

    def test_already_datetime_unchanged(self, monkeypatch, tmp_path):
        """String datetime that can't be parsed should fall back to now()."""
        monkeypatch.chdir(tmp_path)
        from handlers import command_handlers as ch
        import db as db_module
        from datetime import datetime

        # Reset DB singleton to use temp path
        original_db = db_module.db
        test_db_path = str(tmp_path / 'data' / 'test_signal2.db')
        (tmp_path / 'data').mkdir(exist_ok=True)
        new_db = db_module.Database(test_db_path)
        db_module.db = new_db
        ch.db = new_db

        try:
            # Write signal with bad datetime string
            signals = {
                'signal1': {
                    'ticker': 'BBCA',
                    'time': 'not-a-valid-datetime',
                    'price': 8500
                }
            }
            with open('last_signals.json', 'w') as f:
                json.dump(signals, f)

            ch.last_buy_signals = {}
            ch.load_user_data()

            # Bad datetime should fall back to current time
            assert isinstance(ch.last_buy_signals['signal1']['time'], datetime)
        finally:
            db_module.db = original_db
            ch.db = original_db


class TestTickerDetection:
    """Tests for ticker detection logic."""

    def test_known_stock_in_all_stocks(self):
        """Known IDX stocks should be detected as stock."""
        from handlers.command_handlers import ALL_STOCKS
        assert 'BBCA' in ALL_STOCKS
        assert 'TLKM' in ALL_STOCKS
        assert 'BIPI' in ALL_STOCKS

    def test_crypto_with_dash_suffix(self):
        """Crypto with -USD suffix should be in crypto list."""
        from handlers.command_handlers import ALL_STOCKS
        # Crypto tickers are NOT in ALL_STOCKS
        assert 'BTC-USD' not in ALL_STOCKS
        assert 'ETH-USD' not in ALL_STOCKS

    def test_stock_dict_has_name(self):
        """Each stock should have a name."""
        from handlers.command_handlers import ALL_STOCKS
        for ticker, name in list(ALL_STOCKS.items())[:10]:
            assert isinstance(name, str)
            assert len(name) > 0


class TestGlobalState:
    """Tests for global state variables."""

    def test_user_data_db_initially_empty(self):
        """user_data_db should be a dict."""
        from handlers import command_handlers as ch
        assert isinstance(ch.user_data_db, dict)

    def test_last_buy_signals_initially_empty(self):
        """last_buy_signals should be a dict."""
        from handlers import command_handlers as ch
        assert isinstance(ch.last_buy_signals, dict)


class TestEdgeCases:
    """Edge case tests."""

    def test_strip_markdown_with_newlines(self):
        """Should handle newlines in text."""
        from handlers.command_handlers import _strip_markdown_chars
        text = "*Bold*\nNormal text\n_italic_"
        result = _strip_markdown_chars(text)
        assert '\n' in result
        assert '*' not in result

    def test_strip_markdown_with_special_chars(self):
        """Should handle special characters mixed with markdown."""
        from handlers.command_handlers import _strip_markdown_chars
        text = "*Bold*: 100% gain!"
        result = _strip_markdown_chars(text)
        assert '100%' in result
        assert '!' in result
        assert '*' not in result

    def test_atomic_write_empty_dict(self):
        """Should write empty dict."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            _atomic_write(filepath, {})

            with open(filepath, 'r') as f:
                loaded = json.load(f)
            assert loaded == {}

    def test_atomic_write_nested_dict(self):
        """Should write nested data structures."""
        from handlers.command_handlers import _atomic_write
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            data = {
                'user1': {
                    'portfolio': {
                        'stocks': [
                            {'ticker': 'BBCA', 'lot': 100},
                            {'ticker': 'TLKM', 'lot': 50}
                        ]
                    },
                    'notifications': {
                        'saham': True,
                        'crypto': False,
                        'bsjp': True
                    }
                }
            }
            _atomic_write(filepath, data)

            with open(filepath, 'r') as f:
                loaded = json.load(f)
            assert loaded == data
            assert loaded['user1']['portfolio']['stocks'][0]['ticker'] == 'BBCA'
