"""Test isi notifikasi crypto (perbaikan P0).

Yang dijaga di sini:
1. Sinyal jual punya judul sendiri ("REKOMENDASI JUAL"), bukan jatuh ke
   fallback "CRYPTO SCAN" yang bikin ambigu.
2. Sinyal jual menampilkan targetnya (TP + Support/Resistance) - justru
   itu yang paling dibutuhkan, dan arah persentasenya dibalik.
3. Tidak ada angka rekaan di pesan (dulu semua sinyal jual menulis
   "+1.00%" karena profit_loss dikirim hardcoded).
4. Harga coin subunit tidak lagi membulatkan semua level jadi sama.
5. Header TP1/TP2 memakai jarak sebenarnya, bukan angka hardcoded.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.formatters import (
    _fmt_price_usd,
    format_analisa_simple,
    format_unified_crypto_notification,
)


def _data(entry=0.5297, atr=0.012):
    return {
        'pattern': {'type': 'uptrend', 'reliability': 70},
        'patterns': [],
        'leverage': 3,
        'indicators': {'rsi': 55, 'macd': 0.0004, 'atr': atr},
        'sr': {
            'nearest_support': {'level': entry * 0.97},
            'nearest_resistance': {'level': entry * 1.06},
            'sr_distance': {'support_pct': 3.8},
        },
    }


def render(notif_type, entry=0.5297, tp1=0.5212, tp2=0.5128, tp3=0.4979,
           sl=0.5451, price=None, atr=0.012, profit_loss=1.0):
    return format_unified_crypto_notification(
        notif_type=notif_type,
        ticker='RUNE-USD',
        name='THORChain',
        entry=entry,
        current_price=price if price is not None else entry,
        tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
        analysis_data=_data(entry, atr),
        change_pct=6.0,
        profit_loss=profit_loss,
        usd_idr_rate=16000,
    )


class TestSellHeader:
    """Sinyal jual harus punya judul sendiri."""

    def test_sell_has_own_header(self):
        msg = render('SELL')
        assert 'REKOMENDASI JUAL' in msg
        assert 'CRYPTO SCAN' not in msg

    def test_sell_is_not_confused_with_reversal_or_buy(self):
        msg = render('SELL')
        assert 'REKOMENDASI BELI' not in msg
        assert 'REVERSAL' not in msg


class TestSellContent:
    """Sinyal jual harus menampilkan target dan arahnya benar."""

    def test_sell_shows_take_profit_levels(self):
        msg = render('SELL')
        assert 'TP1' in msg
        assert 'TP2' in msg
        assert 'TP3' in msg

    def test_sell_shows_support_resistance(self):
        msg = render('SELL')
        assert 'Support & Resistance' in msg

    def test_sell_gain_shows_direction(self):
        """Risiko ke atas, target ke bawah - ditulis eksplisit."""
        msg = render('SELL')
        assert 'risiko' in msg
        assert 'target' in msg
        assert 'harga naik' in msg
        assert 'harga turun' in msg

    def test_sell_has_no_fake_pl(self):
        """Dulu semua sinyal jual menulis '+1.00%' karena profit_loss
        dikirim hardcoded dari job scan."""
        msg = render('SELL', profit_loss=1.0)
        assert '+1.00%' not in msg

    def test_sell_tp_percent_is_positive_below_entry(self):
        """Untuk sinyal jual targetnya di bawah entry, jadi persentasenya
        jarak ke bawah (positif)."""
        msg = render('SELL')
        assert '(+1.6%)' in msg
        assert '(-1.6%)' not in msg


class TestTpHeaderUsesRealNumber:
    """Header TP1/TP2 tidak boleh pakai angka hardcoded."""

    def test_tp1_header_uses_real_distance(self):
        # entry 100, tp1 102 -> +2.0%
        msg = render('TP1', entry=100.0, tp1=102.0, tp2=104.0, tp3=106.0,
                     sl=97.0, atr=1.0, price=102.0)
        assert 'TP1 TERKUNCI! +2.0%' in msg

    def test_tp1_header_not_hardcoded(self):
        # Jarak TP1 yang sebenarnya 5% - kalau masih hardcoded, muncul
        # "+2%" dan user salah baca.
        msg = render('TP1', entry=100.0, tp1=105.0, tp2=107.0, tp3=110.0,
                     sl=97.0, atr=1.0, price=105.0)
        assert '+5.0%' in msg
        assert '+2%' not in msg

    def test_tp1_for_short_position_uses_distance(self):
        """Notifikasi TP1 untuk posisi short: target di bawah entry."""
        msg = render('TP1', entry=0.5297, tp1=0.5212, tp2=0.5128,
                     tp3=0.4979, sl=0.5451, atr=0.012, price=0.5200)
        assert 'TP1 TERKUNCI! +1.6%' in msg
        assert 'Sell:' in msg


class TestAdaptivePrice:
    """Coin subunit: level harga harus bisa dibedakan."""

    def test_sub_dollar_keeps_significant_digits(self):
        assert _fmt_price_usd(0.0625) == '$0.0625'

    def test_tiny_price_not_rounded_to_zero(self):
        assert _fmt_price_usd(0.003) == '$0.003'

    def test_normal_price_unchanged(self):
        assert _fmt_price_usd(54.61) == '$54.61'

    def test_large_price_unchanged(self):
        assert _fmt_price_usd(1234.5) == '$1,234.50'

    def test_minimum_two_decimals(self):
        assert _fmt_price_usd(0.5) == '$0.50'

    def test_invalid_values(self):
        assert _fmt_price_usd(0) == 'N/A'
        assert _fmt_price_usd(None) == 'N/A'


class TestAnalisaCryptoPrice:
    """Pesan /analisa untuk crypto juga harus pakai desimal adaptif."""

    def test_levels_are_distinguishable(self):
        data = {
            'price': 0.0625, 'change': 1.2, 'ma_fast': 0.061, 'ma_slow': 0.059,
            'macd_hist': 0.0001, 'rsi': 55, 'volume_ratio': 1.0, 'candles': 168,
            'atr': 0.003, 'sr': {},
        }
        msg = format_analisa_simple(
            ticker='TAKE', name='OVERTAKE', data=data,
            signal={'signal': 'BUY', 'entry': 0.0625, 'tp1': 0.0631,
                    'tp2': 0.0637, 'tp3': 0.0649, 'sl': 0.0613},
            sentiment=None, is_crypto=True, usd_idr_rate=16000,
            timeframe='1 Jam')
        assert '$0.0625' in msg
        assert '$0.0631' in msg
        assert '$0.06 ' not in msg
