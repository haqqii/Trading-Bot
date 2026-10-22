"""
Unit tests for the ticker stored with each signal.

Signal menyimpan tickernya di 'ticker_raw'. Kalau kode baca 'ticker',
nilainya fallback 'UNKNOWN' — dan auto-save menimpa ticker yang benar di
database, sehingga setelah restart TP/SL tidak pernah bisa dicek.
"""
import sys
import os

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeDb:
    """Tangkap apa yang ditulis ke tabel signals."""

    def __init__(self):
        self.saved = []

    def initialize(self):
        pass

    def save_signal(self, **kwargs):
        self.saved.append(kwargs)

    def update_user(self, *args, **kwargs):
        pass

    def replace_user_favorites(self, *args, **kwargs):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    import handlers.command_handlers as ch
    db = FakeDb()
    monkeypatch.setattr(ch, 'db', db)
    monkeypatch.setattr(ch, 'user_data_db', {})
    monkeypatch.setattr(ch, 'last_buy_signals', {})
    return db


class TestSaveUserDataTicker:
    """Tests for save_user_data()."""

    def test_uses_ticker_raw(self, fake_db, monkeypatch):
        """Ticker diambil dari 'ticker_raw'."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_AREA_1': {'ticker_raw': 'AREA', 'entry': 100, 'tp1': 110},
        })
        ch.save_user_data()
        assert len(fake_db.saved) == 1
        assert fake_db.saved[0]['ticker'] == 'AREA'

    def test_falls_back_to_ticker_key(self, fake_db, monkeypatch):
        """Format lama (key 'ticker') tetap diterima."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_BBCA_1': {'ticker': 'BBCA', 'entry': 100},
        })
        ch.save_user_data()
        assert fake_db.saved[0]['ticker'] == 'BBCA'

    def test_never_writes_unknown_when_ticker_raw_present(self, fake_db, monkeypatch):
        """'UNKNOWN' tidak boleh menimpa ticker yang diketahui."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_TOWR_1': {'ticker_raw': 'TOWR', 'entry': 420},
        })
        ch.save_user_data()
        assert fake_db.saved[0]['ticker'] != 'UNKNOWN'
        assert fake_db.saved[0]['ticker'] == 'TOWR'


class TestPlaceholderTickers:
    """Placeholder bukan ticker sungguhan — jangan pernah ditandai."""

    def test_placeholder_not_marked(self, tmp_path, monkeypatch):
        from services import ticker_health
        monkeypatch.setattr(ticker_health, 'DATA_DIR', str(tmp_path))
        monkeypatch.setattr(ticker_health, 'STATE_FILE', str(tmp_path / 's.json'))
        monkeypatch.setattr(ticker_health, '_entries', {})
        monkeypatch.setattr(ticker_health, '_loaded', False)
        for placeholder in ('UNKNOWN', 'unknown', 'UNKNOWN.JK', 'NONE', '', None):
            assert ticker_health.mark_unavailable(placeholder) is False
        assert ticker_health.list_unavailable() == {}

    def test_placeholder_not_considered_unavailable(self, tmp_path, monkeypatch):
        from services import ticker_health
        monkeypatch.setattr(ticker_health, 'DATA_DIR', str(tmp_path))
        monkeypatch.setattr(ticker_health, 'STATE_FILE', str(tmp_path / 's.json'))
        monkeypatch.setattr(ticker_health, '_entries', {})
        monkeypatch.setattr(ticker_health, '_loaded', False)
        assert ticker_health.is_unavailable('UNKNOWN') is False
