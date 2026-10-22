"""
Unit tests for the bulk-marking guard in services/ticker_health.py

Kalau banyak ticker sekaligus tidak punya data, itu hampir pasti masalah
sumber data — bukan banyak ticker mati. Kalau dibiarkan, semua ticker
ditandai dan bot diam selama 24 jam.
"""
import sys
import os
import time

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from services import ticker_health
    monkeypatch.setattr(ticker_health, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ticker_health, 'STATE_FILE', str(tmp_path / 'ticker_unavailable.json'))
    monkeypatch.setattr(ticker_health, '_entries', {})
    monkeypatch.setattr(ticker_health, '_loaded', False)
    monkeypatch.setattr(ticker_health, '_warned_bulk_at', 0.0)
    return ticker_health


class TestBulkGuard:
    """Tests for MAX_MARKS_PER_HOUR."""

    def test_marks_below_threshold(self, registry):
        """Di bawah ambang, ticker tetap ditandai."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            assert registry.mark_unavailable(f'T{i}') is True

    def test_stops_marking_above_threshold(self, registry):
        """Di atas ambang, penandaan dihentikan."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            registry.mark_unavailable(f'T{i}')
        assert registry.mark_unavailable('LEBIH') is False
        assert registry.is_unavailable('LEBIH') is False

    def test_bulk_event_is_reported_as_error(self, registry, caplog):
        """Kejadiannya dilaporkan sebagai error, bukan Info."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            registry.mark_unavailable(f'T{i}')
        with caplog.at_level('ERROR'):
            registry.mark_unavailable('LEBIH')
        errors = [r for r in caplog.records if r.levelname == 'ERROR']
        assert len(errors) == 1
        assert 'sumber data' in errors[0].getMessage()

    def test_guard_resets_after_an_hour(self, registry):
        """Setelah satu jam, penandaan boleh lagi."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            registry.mark_unavailable(f'T{i}')
        assert registry.mark_unavailable('LEBIH') is False
        # Geser semua penandaan ke lebih dari satu jam lalu.
        for info in registry._entries.values():
            info['at'] = time.time() - 3601
        assert registry.mark_unavailable('BARU') is True

    def test_bulk_warning_not_repeated_every_call(self, registry, caplog):
        """Peringatan tidak diulang tiap pemanggilan."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            registry.mark_unavailable(f'T{i}')
        with caplog.at_level('ERROR'):
            registry.mark_unavailable('A')
            registry.mark_unavailable('B')
            registry.mark_unavailable('C')
        errors = [r for r in caplog.records if r.levelname == 'ERROR']
        assert len(errors) == 1
