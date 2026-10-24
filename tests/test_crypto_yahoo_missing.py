"""
Unit tests for services/crypto_yahoo_missing.py

Registry ticker crypto yang tidak ada di Yahoo Finance. Ticker seperti ini
belum tentu ticker mati - di CoinGecko ticker itu valid, cuma Yahoo yang
tidak punya candle-nya. Jadi yang dicatat hanya "tidak ada di Yahoo untuk
interval ini", bukan "ticker mati".
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
    from services import crypto_yahoo_missing as reg
    monkeypatch.setattr(reg, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(reg, 'STATE_FILE', str(tmp_path / 'crypto_yahoo_missing.json'))
    monkeypatch.setattr(reg, '_entries', {})
    monkeypatch.setattr(reg, '_loaded', False)
    monkeypatch.setattr(reg, '_warned_bulk_at', 0.0)
    return reg


class TestMarkAndCheck:
    """Tests untuk menandai dan mengecek ticker."""

    def test_not_missing_by_default(self, registry):
        """Ticker yang belum ditandai dianggap ada."""
        assert registry.is_missing('UNI-USD') is False

    def test_marked_ticker_is_missing(self, registry):
        """Ticker yang ditandai dianggap tidak ada di Yahoo."""
        registry.mark_missing('UNI-USD', '15m', reason='Yahoo 404')
        assert registry.is_missing('UNI-USD', '15m') is True

    def test_other_ticker_not_affected(self, registry):
        """Ticker lain tidak ikut kena."""
        registry.mark_missing('UNI-USD', '15m')
        assert registry.is_missing('MATIC-USD', '15m') is False

    def test_other_interval_not_affected(self, registry):
        """Interval berbeda = catatan berbeda.

        Ticker bisa punya candle harian tapi tidak intraday, jadi
        "tidak ada untuk 15m" tidak boleh berarti "tidak ada untuk 1d".
        """
        registry.mark_missing('UNI-USD', '15m')
        assert registry.is_missing('UNI-USD', '1d') is False

    def test_clear_removes_mark(self, registry):
        """Tanda bisa dihapus kalau ticker ternyata ada lagi."""
        registry.mark_missing('UNI-USD', '15m')
        registry.clear('UNI-USD', '15m')
        assert registry.is_missing('UNI-USD', '15m') is False

    def test_ticker_case_insensitive(self, registry):
        """Ticker case-insensitive."""
        registry.mark_missing('uni-usd', '15m')
        assert registry.is_missing('UNI-USD', '15m') is True


class TestExpiry:
    """Tanda yang sudah lewat masa harus dicoba lagi."""

    def test_expired_mark_is_not_missing(self, registry):
        """Ticker dicoba lagi setelah TTL habis."""
        registry.mark_missing('UNI-USD', '15m', ttl=1)
        assert registry.is_missing('UNI-USD', '15m', now=time.time() + 10) is False

    def test_expired_mark_is_removed(self, registry):
        """Tanda yang kedaluwarsa dibuang dari daftar."""
        registry.mark_missing('UNI-USD', '15m', ttl=1)
        registry.is_missing('UNI-USD', '15m', now=time.time() + 10)
        assert registry.list_missing() == {}

    def test_mark_extends_not_shortens(self, registry):
        """Penandaan ulang tidak boleh shorten TTL yang sudah lebih lama."""
        registry.mark_missing('UNI-USD', '15m', ttl=3600)
        first = registry.list_missing()['UNI-USD@15m']['until']
        registry.mark_missing('UNI-USD', '15m', ttl=60)
        assert registry.list_missing()['UNI-USD@15m']['until'] == first


class TestBulkGuard:
    """Terlalu banyak ticker hilang sekaligus = kemungkinan Yahoo yang rusak."""

    def test_marking_stops_after_threshold(self, registry):
        """Setelah ambang, penandaan dihentikan."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            assert registry.mark_missing(f'COIN{i}-USD', '15m') is True
        assert registry.mark_missing('COIN999-USD', '15m') is False

    def test_bulk_marked_ticker_still_usable(self, registry):
        """Ticker yang tidak ditandai tetap bisa dipakai."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            registry.mark_missing(f'COIN{i}-USD', '15m')
        registry.mark_missing('COIN999-USD', '15m')
        assert registry.is_missing('COIN999-USD', '15m') is False


class TestBulkGuardRelative:
    """Ambang harus relatif terhadap jumlah pair yang dipantau.

    Angka Yahoo tidak lengkap untuk crypto: dari 250 pair yang dipantau,
    dozens benar-benar tidak ada di Yahoo (stablecoin, token obscure).
    Ambang absolut yang kecil bikin guard salah menyala saat kondisinya
    normal - itu yang memunculkan alert "60+ ticker hilang dari Yahoo".
    """

    def test_limit_scales_with_universe(self, registry):
        """Universe besar = ambang ikut besar."""
        limit = max(registry.MAX_MARKS_PER_HOUR,
                    int(250 * registry.BULK_RATIO))
        for i in range(limit):
            assert registry.mark_missing(f'COIN{i}-USD', '15m',
                                         universe_size=250) is True
        assert registry.mark_missing('COIN999-USD', '15m',
                                     universe_size=250) is False

    def test_small_universe_keeps_absolute_floor(self, registry):
        """Universe kecil tetap pakai lantai absolut."""
        for i in range(registry.MAX_MARKS_PER_HOUR):
            assert registry.mark_missing(f'COIN{i}-USD', '15m',
                                         universe_size=10) is True
        assert registry.mark_missing('COIN999-USD', '15m',
                                     universe_size=10) is False

    def test_normal_absence_does_not_trip_guard(self, registry):
        """~70 ticker yang memang tidak ada di Yahoo bukan kondisi abnormal."""
        for i in range(70):
            assert registry.mark_missing(f'COIN{i}-USD', '4h',
                                         universe_size=250) is True


class TestBulkWarningLevel:
    """Kondisi provider bukan bug bot, jadi tidak boleh jadi alert admin."""

    @staticmethod
    def _trip_guard(registry, extra=0):
        """Isi registry sampai ambang relatif, lalu lewati ambang."""
        limit = max(registry.MAX_MARKS_PER_HOUR, int(250 * registry.BULK_RATIO))
        for i in range(limit):
            registry.mark_missing(f'COIN{i}-USD', '15m', universe_size=250)
        for i in range(limit, limit + extra):
            registry.mark_missing(f'COIN{i}-USD', '15m', universe_size=250)

    def test_bulk_message_is_warning_not_error(self, registry, caplog):
        """Pesan 'ticker sekaligus hilang' dicatat sebagai WARNING."""
        import logging
        with caplog.at_level(logging.DEBUG, logger=registry.logger.name):
            self._trip_guard(registry, extra=1)

        bulk = [r for r in caplog.records
                if 'sekaligus hilang dari Yahoo' in r.getMessage()]
        assert bulk, "pesan bulk harus tetap tercatat"
        assert bulk[0].levelno == logging.WARNING
        assert bulk[0].levelno < logging.ERROR

    def test_bulk_message_not_repeated_within_hour(self, registry, caplog):
        """Pesan bulk tidak diulang tiap menit dalam satu jam."""
        import logging
        with caplog.at_level(logging.DEBUG, logger=registry.logger.name):
            self._trip_guard(registry, extra=5)

        bulk = [r for r in caplog.records
                if 'sekaligus hilang dari Yahoo' in r.getMessage()]
        assert len(bulk) == 1


class TestPersistence:
    """Status harus bertahan antar restart."""

    def test_state_saved_to_file(self, registry, tmp_path):
        """Status ditulis ke file."""
        registry.mark_missing('UNI-USD', '15m')
        assert os.path.exists(str(tmp_path / 'crypto_yahoo_missing.json'))

    def test_state_reloaded_from_file(self, registry, tmp_path):
        """Status dibaca lagi dari file saat start."""
        registry.mark_missing('UNI-USD', '15m')
        registry._loaded = False
        registry._entries = {}
        assert registry.is_missing('UNI-USD', '15m') is True
