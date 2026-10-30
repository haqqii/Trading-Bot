"""Aturan keluar (breakeven + trailing) harus benar dan tidak pernah mundur.

SL yang sudah digeser ke breakeven tidak boleh turun lagi ke bawah entry
saat harga berbalik - kalau iya, profit yang sudah terkunci hilang dan
exit management justru menambah kerugian.
"""
import math

import pytest

from services.exit_manager import (
    TRAILING_ATR_MULT,
    breakeven_sl,
    effective_sl,
    exit_outcome,
    trailing_sl,
    update_sl,
)


class TestBreakeven:
    """Setelah TP1 kena, SL pindah ke harga entry."""

    def test_long_sl_moves_to_entry(self):
        sl = update_sl(95.0, 100.0, 'LONG', {'tp1': True}, 104.0, 2.0, tp1=104.0)
        assert sl == 100.0

    def test_short_sl_moves_to_entry(self):
        sl = update_sl(105.0, 100.0, 'SHORT', {'tp1': True}, 96.0, 2.0, tp1=96.0)
        assert sl == 100.0

    def test_without_tp_hit_sl_unchanged(self):
        assert update_sl(95.0, 100.0, 'LONG', {'tp1': False}, 101.0, 2.0) == 95.0
        assert update_sl(95.0, 100.0, 'LONG', {}, 101.0, 2.0) == 95.0
        assert update_sl(95.0, 100.0, 'LONG', None, 101.0, 2.0) == 95.0

    def test_breakeven_helper(self):
        assert breakeven_sl(123.45) == 123.45


class TestTrailing:
    """Setelah TP2 kena, SL mengikuti harga (minimal di level TP1)."""

    def test_trailing_follows_price_up(self):
        # Entry 100, TP1 104, ATR 2 -> trailing = harga - 1.5 ATR
        sl = trailing_sl(110.0, 2.0, 100.0, 'LONG', tp1=104.0)
        assert sl == pytest.approx(110.0 - 2.0 * TRAILING_ATR_MULT)

    def test_trailing_never_below_tp1(self):
        # Harga turun mendekati TP1: lantai tetap TP1.
        sl = trailing_sl(104.5, 2.0, 100.0, 'LONG', tp1=104.0)
        assert sl == 104.0

    def test_trailing_never_below_entry_without_tp1(self):
        sl = trailing_sl(100.5, 2.0, 100.0, 'LONG', tp1=None)
        assert sl == 100.0

    def test_short_trailing(self):
        sl = trailing_sl(90.0, 2.0, 100.0, 'SHORT', tp1=96.0)
        assert sl == pytest.approx(min(90.0 + 2.0 * TRAILING_ATR_MULT, 96.0))


class TestMonotonic:
    """SL yang sudah baik tidak boleh memburuk."""

    def test_long_sl_never_goes_down(self):
        sl = update_sl(100.0, 100.0, 'LONG', {'tp1': True, 'tp2': True},
                       108.0, 2.0, tp1=104.0)
        # Trailing di 108 -> 108 - 3 = 105, tapi SL lama (100) jadi lantai.
        assert sl >= 100.0

    def test_short_sl_never_goes_up(self):
        sl = update_sl(100.0, 100.0, 'SHORT', {'tp1': True, 'tp2': True},
                       92.0, 2.0, tp1=96.0)
        assert sl <= 100.0

    def test_locked_value_wins_over_candidate(self):
        # SL sudah digeser ke 108 (trailing sebelumnya), harga turun ->
        # SL tidak boleh ikut turun.
        sl = update_sl(108.0, 100.0, 'LONG', {'tp1': True, 'tp2': True},
                       104.0, 2.0, tp1=104.0)
        assert sl == 108.0


class TestSafeInput:
    """Data jelek tidak boleh membuat SL NaN atau exception."""

    def test_none_and_nan_inputs(self):
        sl = update_sl(None, None, 'LONG', {'tp1': True}, None, None)
        assert sl == 0.0
        assert not math.isnan(sl)

    def test_nan_current_sl(self):
        sl = update_sl(float('nan'), 100.0, 'LONG', {'tp1': True}, 101.0, 2.0)
        assert sl == 100.0

    def test_bad_direction_defaults_to_long(self):
        assert update_sl(95.0, 100.0, 'entah', {'tp1': True}, 101.0, 2.0) == 100.0


class TestEffectiveSl:
    """effective_sl = SL hitung ulang kalau belum ada TP yang kena."""

    def test_untouched_before_tp_hit(self):
        assert effective_sl(95.0, 100.0, 'LONG', {'tp1': False},
                            101.0, 2.0) == 95.0

    def test_moved_after_tp_hit(self):
        assert effective_sl(95.0, 100.0, 'LONG', {'tp1': True},
                            104.0, 2.0, tp1=104.0) == 100.0


class TestExitOutcome:
    """Exit di breakeven bukan cut loss."""

    def test_outcome_without_tp1(self):
        assert exit_outcome({'tp1': False}) == 'sl'
        assert exit_outcome({}) == 'sl'

    def test_outcome_with_tp1(self):
        assert exit_outcome({'tp1': True}) == 'beakeven'

    def test_outcome_with_tp2_is_trailing(self):
        """Setelah TP2, SL yang aktif = trailing stop (profit)."""
        assert exit_outcome({'tp1': True, 'tp2': True}) == 'trailing'
