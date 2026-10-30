"""Jalur data fallback tidak boleh bikin bot meledak.

Sumber fallback (TradingView, Finnhub) sengaja TIDAK lagi mengarang
indikator: field yang tidak ada dibiarkan None dan ditandai degraded.
Semua pemakai data itu harus tahan None - kalau tidak, satu ticker
dengan data tipis bisa menjatuhkan seluruh scan / pesan.
"""
import pytest

from services.signal_service import (
    signal_service,
    safe_num,
    finite_or_none,
)
from utils.formatters import (
    format_bsjp_msg,
    format_morning_msg,
    format_unified_stock_notification,
    _alasan_pemula,
    _intinya_pemula,
    fmt_num,
)
from handlers.scheduler._common import compute_trend


DEGRADED_STOCK = {
    'name': 'CONTOH.JK',
    'price': 100.0,
    'change': 0.0,
    'rsi': None,
    'ma_fast': None,
    'ma_slow': None,
    'macd': None,
    'macd_signal': None,
    'macd_hist': None,
    'bb_upper': None,
    'bb_lower': None,
    'atr': 1.5,
    'volume_ratio': 1.0,
    'candles': 100,
    'source': 'finnhub',
    'degraded': True,
    'indicator_missing': ['rsi', 'ma', 'macd', 'bb'],
}


class TestHelperSafeNum:
    """safe_num/finite_or_none jadi kontrak bersama semua pemakai data."""

    def test_safe_num_falls_back_on_none(self):
        assert safe_num(None, 50) == 50
        assert safe_num('abc', 7) == 7
        assert safe_num('12.5', 0) == 12.5

    def test_finite_or_none_marks_missing(self):
        assert finite_or_none(None) is None
        assert finite_or_none(float('nan')) is None
        assert finite_or_none(float('inf')) is None
        assert finite_or_none('abc') is None
        assert finite_or_none(42) == 42.0


class TestEngineWithMissingIndicators:
    """Mesin sinyal harus tetap jalan saat indikator hilang."""

    def test_stock_signal_degraded_does_not_crash(self):
        s = signal_service.generate_stock_signal(DEGRADED_STOCK)
        assert s['signal'] == 'HOLD'
        assert s['quality'] == 'WEAK'
        assert 'rsi' in s['missing_indicators']
        assert 'macd' in s['missing_indicators']
        # Tidak ada angka karangan: RSI default netral, bukan hasil tebakan.
        assert s['rsi'] == 50.0

    def test_crypto_signal_degraded_does_not_crash(self):
        data = {
            'price': 1000.0,
            'change': 0.0,
            'rsi': None,
            'ma_fast': None,
            'ma_slow': None,
            'macd': None,
            'macd_signal': None,
            'macd_hist': None,
            'bb_upper': None,
            'bb_lower': None,
            'atr': 25.0,
            'volume_ratio': 1.0,
            'degraded': True,
        }
        s = signal_service.generate_crypto_signal(data)
        assert s['signal'] == 'HOLD'
        assert s['quality'] == 'WEAK'
        assert s['tp1'] is None  # HOLD tidak punya TP/SL


class TestFormatterSurvivesMissingData:
    """Formatter tidak boleh meledak saat angka indikator hilang."""

    def test_fmt_num_handles_missing(self):
        assert fmt_num(None) == '-'
        assert fmt_num(float('nan')) == '-'
        assert fmt_num(12.6) == '13'

    def test_bsjp_message_with_missing_rsi(self):
        msg = format_bsjp_msg([{
            'ticker': 'CONTOH',
            'name': 'Contoh',
            'price': 100.0,
            'change': None,
            'rsi': None,
            'score': 3,
            'tp': 102.0,
            'sl': 98.5,
        }])
        assert 'RSI: -' in msg
        assert 'Contoh' in msg

    def test_morning_message_with_missing_rsi(self):
        msg = format_morning_msg([{
            'ticker': 'CONTOH',
            'name': 'Contoh',
            'price': 100.0,
            'change': None,
            'rsi': None,
            'tp': 103.0,
            'sl': 98.0,
        }])
        assert 'RSI: -' in msg

    def test_stock_notification_with_missing_indicators(self):
        msg = format_unified_stock_notification(
            notif_type='BUY',
            ticker='CONTOH.JK',
            name='Contoh',
            entry=100.0,
            current_price=100.0,
            tp1=102.0,
            tp2=104.0,
            tp3=106.0,
            sl=98.0,
            analysis_data={
                'indicators': {'rsi': None, 'macd': None, 'atr': None},
                'rsi': None,
                'change': None,
                'volume_ratio': None,
                'reasons': [],
            },
            change_pct=0.0,
            profit_loss=1.0,
        )
        assert 'Contoh' in msg

    def test_pemula_helpers_with_missing_data(self):
        data = {
            'price': 100.0,
            'rsi': None,
            'ma_fast': None,
            'ma_slow': None,
            'macd_hist': None,
            'volume_ratio': None,
            'change': None,
        }
        signal = {'signal': 'BUY', 'buy_score': 50, 'sell_score': 10}
        assert isinstance(_alasan_pemula(data, signal), list)
        assert isinstance(_intinya_pemula('BUY', data, {}), str)


class TestComputeTrendWithMissingData:
    """compute_trend dipakai untuk label tren di notifikasi."""

    def test_trend_with_none_indicators(self):
        d = {'price': 100.0, 'change': None, 'ma_fast': None, 'ma_slow': None}
        s = {'macd_hist': None, 'signal': 'HOLD'}
        assert isinstance(compute_trend(d, s), str)
