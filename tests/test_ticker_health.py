"""
Unit tests for services/ticker_health.py
Tests the registry of tickers that have no price data.
"""
import sys
import os
import time

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Registry dengan file status di folder sementara."""
    from services import ticker_health
    monkeypatch.setattr(ticker_health, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ticker_health, 'STATE_FILE', str(tmp_path / 'ticker_unavailable.json'))
    monkeypatch.setattr(ticker_health, '_entries', {})
    monkeypatch.setattr(ticker_health, '_loaded', False)
    return ticker_health


class TestMarkAndCheck:
    """Tests for mark_unavailable and is_unavailable."""

    def test_unknown_ticker_is_available(self, registry):
        """Ticker yang tidak ditandai harus dianggap tersedia."""
        assert registry.is_unavailable('BBCA') is False

    def test_marked_ticker_is_unavailable(self, registry):
        """Setelah ditandai, ticker dilewati."""
        registry.mark_unavailable('BIKE', reason='no data')
        assert registry.is_unavailable('BIKE') is True

    def test_mark_is_case_insensitive(self, registry):
        """Kunci registry tidak_case-sensitive."""
        registry.mark_unavailable('bike')
        assert registry.is_unavailable('BIKE') is True
        assert registry.is_unavailable('Bike') is True

    def test_jk_suffix_is_stripped(self, registry):
        """Suffix .JK dibuang, BIKE.JK sama dengan BIKE."""
        registry.mark_unavailable('BIKE.JK')
        assert registry.is_unavailable('BIKE') is True
        assert registry.is_unavailable('BIKE.JK') is True

    def test_empty_ticker_ignored(self, registry):
        """Ticker kosong tidak boleh jadi kunci."""
        registry.mark_unavailable('')
        registry.mark_unavailable(None)
        assert registry.is_unavailable('') is False
        assert registry.list_unavailable() == {}

    def test_clear_removes_mark(self, registry):
        """clear() membuat ticker bisa dicoba lagi."""
        registry.mark_unavailable('BIKE')
        assert registry.is_unavailable('BIKE') is True
        registry.clear('BIKE')
        assert registry.is_unavailable('BIKE') is False

    def test_clear_unknown_ticker_is_safe(self, registry):
        """clear() untuk ticker yang tidak ada tidak boleh error."""
        registry.clear('TIDAKADA')
        assert registry.is_unavailable('TIDAKADA') is False


class TestExpiry:
    """Tests for TTL behaviour."""

    def test_expired_entry_is_not_unavailable(self, registry):
        """Setelah TTL habis, ticker dicoba lagi."""
        registry.mark_unavailable('BIKE', ttl=1)
        assert registry.is_unavailable('BIKE', now=time.time() + 0.5) is True
        assert registry.is_unavailable('BIKE', now=time.time() + 10) is False

    def test_expired_entry_is_removed(self, registry):
        """Entri kedaluwarsa dibuang dari daftar."""
        registry.mark_unavailable('BIKE', ttl=1)
        registry.is_unavailable('BIKE', now=time.time() + 10)
        assert 'BIKE' not in registry.list_unavailable()

    def test_mark_does_not_shorten_existing(self, registry):
        """Penandaan ulang tidak memperpendek masa lewati."""
        registry.mark_unavailable('BIKE', ttl=10000)
        registry.mark_unavailable('BIKE', ttl=1)
        assert registry.is_unavailable('BIKE', now=time.time() + 100) is True


class TestPersistence:
    """Tests that the registry survives a restart."""

    def test_entries_saved_to_file(self, registry):
        """Status harus ditulis ke file."""
        registry.mark_unavailable('BIKE', reason='no data')
        assert os.path.exists(registry.STATE_FILE)

    def test_entries_reloaded_after_restart(self, registry, monkeypatch):
        """Setelah restart, tanda masih terbaca."""
        registry.mark_unavailable('BIKE', reason='no data')
        # Simulasikan restart: cache memori dibuang.
        monkeypatch.setattr(registry, '_entries', {})
        monkeypatch.setattr(registry, '_loaded', False)
        assert registry.is_unavailable('BIKE') is True

    def test_reload_keeps_reason(self, registry, monkeypatch):
        """Alasan tersimpan dan terbaca lagi."""
        registry.mark_unavailable('BIKE', reason='tidak ada data harga')
        monkeypatch.setattr(registry, '_entries', {})
        monkeypatch.setattr(registry, '_loaded', False)
        registry.is_unavailable('BIKE')
        assert registry.list_unavailable()['BIKE']['reason'] == 'tidak ada data harga'

    def test_corrupt_file_does_not_crash(self, registry, monkeypatch):
        """File rusak tidak boleh membuat bot gagal start."""
        with open(registry.STATE_FILE, 'w', encoding='utf-8') as f:
            f.write('{bukan json')
        monkeypatch.setattr(registry, '_entries', {})
        monkeypatch.setattr(registry, '_loaded', False)
        assert registry.is_unavailable('BIKE') is False
