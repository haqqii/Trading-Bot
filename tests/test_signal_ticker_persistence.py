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

    def test_target_price_is_tp3(self, fake_db, monkeypatch):
        """target_price harus TP3 (target akhir), sama seperti save_active_signal()."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_TOWR_1': {
                'ticker_raw': 'TOWR', 'entry': 420,
                'tp1': 371.43, 'tp2': 322.86, 'tp3': 250.0,
                'sl': 492.86,
            },
        })
        ch.save_user_data()
        assert fake_db.saved[0]['target_price'] == 250.0

    def test_target_price_falls_back_to_tp(self, fake_db, monkeypatch):
        """Format lama (hanya ada 'tp') tetap dipakai."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_BBCA_1': {'ticker_raw': 'BBCA', 'entry': 100, 'tp': 115.0},
        })
        ch.save_user_data()
        assert fake_db.saved[0]['target_price'] == 115.0

    def test_target_price_falls_back_to_tp1_when_no_tp3(self, fake_db, monkeypatch):
        """Sinyal lama tanpa TP3 tidak boleh ditulis kosong."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_BBCA_1': {'ticker_raw': 'BBCA', 'entry': 100,
                             'tp1': 105.0, 'tp2': 110.0},
        })
        ch.save_user_data()
        assert fake_db.saved[0]['target_price'] == 105.0

    def test_tp_values_not_duplicated_into_extra_data(self, fake_db, monkeypatch):
        """TP1/TP2/TP3 disimpan di kolomnya, tidak di extra_data."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_TOWR_1': {
                'ticker_raw': 'TOWR', 'entry': 420,
                'tp1': 371.43, 'tp2': 322.86, 'tp3': 250.0, 'sl': 492.86,
            },
        })
        ch.save_user_data()
        extra = fake_db.saved[0]['extra_data'] or {}
        for k in ('tp1', 'tp2', 'tp3'):
            assert k not in extra

    def test_writes_asset_name(self, fake_db, monkeypatch):
        """Nama aset ikut ditulis ke kolom name.

        Auto-save menulis ulang baris yang sama dengan scanner. Kalau nama
        tidak ikut, kolom name jadi kosong dan notifikasi menampilkan
        ticker mentah ("AERO-USD") bukan nama aset ("Aerodrome Finance").
        """
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'CRYPTO_AERO-USD_1': {
                'ticker_raw': 'AERO-USD', 'entry': 0.72,
                'name': 'Aerodrome Finance',
            },
        })
        ch.save_user_data()
        assert fake_db.saved[0]['name'] == 'Aerodrome Finance'

    def test_name_falls_back_to_empty(self, fake_db, monkeypatch):
        """Sinyal tanpa nama tidak boleh error."""
        import handlers.command_handlers as ch
        monkeypatch.setattr(ch, 'last_buy_signals', {
            'STOCK_BBCA_1': {'ticker_raw': 'BBCA', 'entry': 100},
        })
        ch.save_user_data()
        assert fake_db.saved[0]['name'] == ''


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
