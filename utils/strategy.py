"""Aturan strategi yang sudah diukur, dipakai bersama oleh mesin live dan
harness backtest.

Tujuannya satu: angka yang ada di docs/SPEC.md 3.1.2 harus bisa
direproduksi persis oleh mesin yang mengirim notifikasi. Jadi aturan
tidak boleh ditulis dua kali di tempat berbeda.

Gerbang tren (crypto)
---------------------
Aturan yang dipakai: sinyal SELL hanya diambil kalau tren timeframe
sedang NAIK, sinyal BUY/REVERSAL tidak diambil.

Dasar ukurnya (17 ticker, harian, 2 tahun, model keluar sama dengan bot
live - lihat docs/SPEC.md 3.1.2):

    SELL + tren naik    : +4,29 %/trade (t=+2,97; 13/17 ticker positif)
    BUY  + tren turun   : -0,42 %/trade (t=-0,61)
    tanpa gerbang       : -0,66 %/trade (t=-1,92)

Perhatikan arahnya: yang menang bukan "beli dip" (mean reversion ke atas
di pasar turun - itu justru yang kalah di tahun crash), tapi "jual rally
di pasar naik". Untuk spot tanpa shorting, sinyal SELL berarti keluar
dari posisi, bukan membuka short.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Timeframe yang gerbang trennya dipakai. Hanya harian yang sudah diukur
# positif; timeframe intraday diukur negatif (lihat SPEC 3.1.1).
TREND_GATE_TIMEFRAMES: Tuple[str, ...] = ('1440',)

# Arah sinyal yang boleh lolos gerbang per timeframe, dan tren yang
# dibutuhkan untuk arah itu.
TREND_GATE_RULES: Dict[str, Dict[str, Any]] = {
    '1440': {
        'directions': ('SELL',),
        'trend': 'up',
    },
}

# Jumlah bar minimum supaya MA50 vs MA100 bisa dihitung. Di bawah ini
# calculate_htf_trend mengembalikan 'flat' yang artinya "tidak diketahui",
# bukan "netral".
TREND_GATE_MIN_BARS = 101

# Sumber data yang definisi trennya sama dengan yang diukur (MA50 vs MA100
# dari bar timeframe). Jalur CoinGecko memakai sparkline 7 jam - definisi
#beda, jadi tandanya tidak boleh dipakai di gerbang ini.
TREND_GATE_SOURCE = 'yahoo'


def trend_gate_rule(timeframe: Any) -> Optional[Dict[str, Any]]:
    """Aturan gerbang tren untuk satu timeframe (None = tidak digerbang)."""
    return TREND_GATE_RULES.get(str(timeframe))


def passes_trend_gate(direction: str, htf_trend: Any, timeframe: Any,
                      source: Optional[str] = None,
                      candles: Optional[int] = None) -> bool:
    """True kalau sinyal boleh diteruskan ke notifikasi.

    Args:
        direction: arah sinyal ('BUY' atau 'SELL').
        htf_trend: nilai htf_trend dari data ('up'/'down'/'flat').
        timeframe: timeframe sinyal (kunci yang sama dengan peta TF live).
        source: sumber data ('yahoo'/'coingecko'). None = tidak diketahui.
        candles: jumlah bar data yang dihitung.

    Timeframe yang tidak ada di aturan -> sinyal diteruskan (tidak ada
    yang diukur untuk timeframe itu). Data yang tidak bisa dipercaya
    -> sinyal diblokir, bukan diteruskan: entah trennya tidak diketahui,
    jadi tidak boleh dipakai mengambil risiko.
    """
    rule = trend_gate_rule(timeframe)
    if not rule:
        return True

    if direction not in rule['directions']:
        return False

    if htf_trend != rule['trend']:
        return False

    # Tren harus dihitung dari seri yang panjangnya cukup dan definisi
    # yang sama dengan yang diukur.
    if source is not None and source != TREND_GATE_SOURCE:
        return False
    if candles is not None and int(candles) < TREND_GATE_MIN_BARS:
        return False

    return True


def describe_trend_gate() -> str:
    """Ringkasan aturan gerbang tren untuk log."""
    parts = []
    for tf, rule in TREND_GATE_RULES.items():
        dirs = ', '.join(rule['directions'])
        parts.append(f"TF {tf}: {dirs} hanya kalau tren '{rule['trend']}'")
    return '; '.join(parts)
