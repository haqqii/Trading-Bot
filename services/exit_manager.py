"""Aturan keluar (exit): breakeven setelah TP1 dan trailing stop setelah TP2.

Sebelum ini SL dihitung sekali dari ATR lalu dipantau apa adanya. Sinyal
yang sudah kena TP1 masih bisa balik jadi loss penuh - padahal pesan TP1
sudah menampilkan "SL: entry (BE)". Aturan di sini membuat SL benar-benar
bergerak, jadi pesan itu jujur dan R:R ikut membaik.

Tahap SL:
  0. belum ada TP kena  -> SL asli (ATR + support/resistance)
  1. TP1 kena           -> SL ke harga entry (breakeven)
  2. TP2 kena           -> SL maksimal(TP1, harga - k*ATR) = trailing

SL hanya boleh membaik, tidak pernah turun lagi ke level yang lebih buruk,
jadi profit yang sudah terkunci tidak hilang karena harga berbalik.

Modul ini murni (tanpa I/O, tanpa akses jaringan) supaya bot live dan
backtest memakai aturan yang sama - kalau bedanya, hasil backtest tidak
berlaku untuk produksi.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Jarak trailing stop dari harga, dalam kelipatan ATR.
TRAILING_ATR_MULT = 1.5


def _num(value: Any, default: float = 0.0) -> float:
    """Angka finite atau default (None/NaN/inf tidak boleh lolos)."""
    if value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num != num or num in (float('inf'), float('-inf')):
        return default
    return num


def _is_short(direction: Any) -> bool:
    return str(direction or '').upper() == 'SHORT'


def breakeven_sl(entry: Any) -> float:
    """Level SL saat TP1 kena: harga entry (modal kembali, minus fee)."""
    return _num(entry)


def trailing_sl(price: Any, atr: Any, entry: Any, direction: str = 'LONG',
                tp1: Any = None, mult: float = TRAILING_ATR_MULT) -> float:
    """SL trailing: jauh `mult` x ATR dari harga, minimal di level TP1.

    Lantai TP1 dipakai karena begitu TP2 kena, TP1 sudah jadi profit yang
    harus dikunci - SL tidak boleh turun ke bawah TP1.
    """
    price = _num(price)
    atr = _num(atr)
    entry = _num(entry)
    floor = _num(tp1, entry) if tp1 else entry
    dist = max(atr * max(mult, 0.0), 0.0)

    if _is_short(direction):
        return min(price + dist, floor)
    return max(price - dist, floor)


def update_sl(current_sl: Any, entry: Any, direction: str,
              tp_hit: Optional[Dict[str, Any]], price: Any, atr: Any,
              tp1: Any = None, mult: float = TRAILING_ATR_MULT) -> float:
    """Level SL baru berdasarkan TP mana saja yang sudah kena.

    `current_sl` jadi lantai: hasilnya tidak pernah lebih buruk dari SL
    yang sekarang, sehingga SL yang sudah digeser ke breakeven/trailing
    tidak turun lagi saat harga berbalik.
    """
    current = _num(current_sl)
    hit = tp_hit or {}
    if not hit.get('tp1'):
        # Belum ada TP yang kena - SL tidak berubah.
        return current

    entry = _num(entry)
    if hit.get('tp2'):
        cand = trailing_sl(price, atr, entry, direction, tp1=tp1, mult=mult)
    else:
        cand = breakeven_sl(entry)

    if _is_short(direction):
        return min(current, cand)
    return max(current, cand)


def effective_sl(base_sl: Any, entry: Any, direction: str,
                 tp_hit: Optional[Dict[str, Any]], price: Any, atr: Any,
                 tp1: Any = None, mult: float = TRAILING_ATR_MULT) -> float:
    """SL efektif untuk dipakai saat mengecek apakah SL kena.

    `base_sl` = SL hasil hitung ulang (ATR + S/R) pada siklus ini.
    Kalau belum ada TP yang kena, hasilnya sama persis dengan `base_sl`.
    """
    return update_sl(base_sl, entry, direction, tp_hit, price, atr,
                     tp1=tp1, mult=mult)


def exit_outcome(tp_hit: Optional[Dict[str, Any]]) -> str:
    """Jenis exit kalau SL yang aktif kena.

    - 'trailing'  : TP2 sudah kena, SL yang aktif = trailing stop (profit).
    - 'beakeven'  : TP1 sudah kena, SL yang aktif = harga entry.
    - 'sl'        : belum ada TP yang kena, SL yang aktif = SL asli.
    """
    hit = tp_hit or {}
    if hit.get('tp2'):
        return 'trailing'
    if hit.get('tp1'):
        return 'beakeven'
    return 'sl'
