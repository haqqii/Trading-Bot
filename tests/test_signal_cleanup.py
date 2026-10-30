"""cleanup_old_signals harus tahan campuran waktu naive dan aware.

Waktu sinyal datang dari dua sumber:
- dari DB: created_at SQLite CURRENT_TIMESTAMP = string UTC TANPA offset
  (naive),
- dari memori: now_wib() = datetime aware UTC+7.

Kalau keduanya disortir apa adanya, Python melempar
"can't compare offset-naive and offset-aware datetimes" dan job cleanup
gagal tiap 5 menit - sinyal lama tidak pernah dibersihkan.
"""
from datetime import datetime, timedelta, timezone

import handlers.command_handlers as ch
from handlers.scheduler._common import (
    SIGNAL_MAX_AGE_DAYS,
    SIGNAL_MAX_PER_TYPE,
    _signal_datetime,
    _signal_finished,
    cleanup_old_signals,
)
from utils.time_utils import now_wib


class TestSignalDatetime:
    """Normalisasi waktu sinyal."""

    def test_naive_db_string_is_read_as_utc(self):
        default = datetime.now(timezone.utc)
        dt = _signal_datetime('2026-10-30 12:00:00', default)
        assert dt.tzinfo is not None
        assert dt.hour == 12
        assert dt.utcoffset() == timedelta(0)

    def test_aware_datetime_keeps_its_instant(self):
        default = datetime.now(timezone.utc)
        aware = now_wib()
        dt = _signal_datetime(aware, default)
        assert dt.tzinfo is not None
        assert dt.timestamp() == aware.timestamp()

    def test_unreadable_value_uses_default(self):
        default = datetime.now(timezone.utc)
        assert _signal_datetime('bukan tanggal', default) == default
        assert _signal_datetime(None, default) == default
        assert _signal_datetime(12345, default) == default


class TestCleanupOldSignals:
    """Job cleanup (dipanggil tiap 5 menit) tidak boleh melempar."""

    def _signals(self, count: int, finished: bool = False) -> dict:
        signals: dict = {}
        for i in range(count):
            tp_hit = {'tp1': True, 'tp2': True, 'tp3': finished}
            # Half dari DB (naive UTC), half dari memori (aware WIB).
            signals[f'STOCK_DB_{i}'] = {
                'type': 'stock',
                'tp_hit': tp_hit,
                'time': (datetime.now(timezone.utc) - timedelta(hours=i + 1)).strftime('%Y-%m-%d %H:%M:%S'),
            }
            signals[f'STOCK_MEM_{i}'] = {
                'type': 'stock',
                'tp_hit': tp_hit,
                'time': now_wib() - timedelta(minutes=i),
            }
        return signals

    def test_mixed_naive_and_aware_signals_do_not_crash(self, monkeypatch):
        signals = self._signals(30)  # 60 sinyal, di atas batas 50 per tipe
        total = len(signals)
        monkeypatch.setattr(ch, 'last_buy_signals', signals)

        removed = cleanup_old_signals()  # sebelumnya TypeError

        # Posisi yang masih dipantau tidak boleh dibuang: job TP/SL iterate
        # peta yang sama, jadi sinyal yang hilang = notifikasi TP/SL hilang.
        assert removed == 0
        assert len(signals) == total

    def test_finished_signals_beyond_limit_are_trimmed(self, monkeypatch):
        signals = self._signals(30, finished=True)
        total = len(signals)
        monkeypatch.setattr(ch, 'last_buy_signals', signals)

        removed = cleanup_old_signals()

        assert removed == total - SIGNAL_MAX_PER_TYPE
        assert len(signals) == SIGNAL_MAX_PER_TYPE
        # Yang tersisa = yang paling baru.
        assert 'STOCK_MEM_0' in signals
        # Sinyal paling lama dibuang lebih dulu.
        assert 'STOCK_DB_29' not in signals

    def test_signal_beyond_max_age_is_removed(self, monkeypatch):
        lama = (datetime.now(timezone.utc) - timedelta(days=SIGNAL_MAX_AGE_DAYS + 3))
        signals = {
            'STOCK_LAMA': {'type': 'stock', 'time': lama.strftime('%Y-%m-%d %H:%M:%S')},
            'STOCK_BARU': {'type': 'stock', 'time': now_wib()},
        }
        monkeypatch.setattr(ch, 'last_buy_signals', signals)

        removed = cleanup_old_signals()

        assert removed == 1
        assert 'STOCK_LAMA' not in signals
        assert 'STOCK_BARU' in signals

    def test_signal_without_time_is_kept(self, monkeypatch):
        signals = {
            'STOCK_NO_TIME': {'type': 'stock'},
            'STOCK_BARU': {'type': 'stock', 'time': now_wib()},
        }
        monkeypatch.setattr(ch, 'last_buy_signals', signals)

        cleanup_old_signals()

        assert 'STOCK_NO_TIME' in signals


class TestSignalFinished:
    """Sinyal yang posisinya sudah tamat boleh dibuang oleh cleanup."""

    def test_tp3_hit_means_finished(self):
        assert _signal_finished({'tp_hit': {'tp1': True, 'tp2': True, 'tp3': True}}) is True
        assert _signal_finished({'tp_hit': {'tp1': True, 'tp2': True, 'tp3': False}}) is False
        assert _signal_finished({}) is False
