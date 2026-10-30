"""Regime gate (ADX) harus mengubah aturan yang dipakai, bukan menambah indikator.

Di pasar sideways, MA/MACD berbalik bolak-balik (noise) sehingga bobotnya
dikurangi; RSI ekstrem berlawanan arah dengan tren adalah jebakan, bukan
pembalikan - jadi poinya dikurangi. Tanpa ADX (data fallback) atau dengan
flag mati, skor harus persis sama seperti sebelumnya.
"""
from services.signal_service import (
    SignalService,
    detect_regime,
    set_features,
    REGIME_NONE,
    REGIME_RANGE,
    REGIME_TREND,
)

import pytest


@pytest.fixture(autouse=True)
def regime_gate_on():
    """Fitur ini diuji secara paksa: default produksinya MATI (hasil A/B
    tidak membaik - lihat docs/SPEC.md 3.1.1), jadi tiap test menyalakan
    flag sendiri dan mengembalikan nilai semula setelahnya."""
    saved = set_features(regime_gate=True)
    yield
    set_features(**saved)


def make_data(**overrides):
    """Payload minimal untuk mesin sinyal saham."""
    data = {
        'price': 100.0,
        'atr': 2.0,
        'volume_ratio': 1.0,
        'rsi': 50.0,
        'ma_fast': 100.0,
        'ma_slow': 100.0,
        'macd': 0.0,
        'macd_signal': 0.0,
        'macd_hist': 0.0,
        'bb_upper': 110.0,
        'bb_lower': 90.0,
        'vwap': 99.0,
        'htf_trend': 'up',
        'adx': 22.0,
        'timeframe': '60',
    }
    data.update(overrides)
    return data


class TestDetectRegime:
    """Klasifikasi regime dari ADX."""

    def test_strong_trend(self):
        assert detect_regime(30.0) == REGIME_TREND
        assert detect_regime(26.0) == REGIME_TREND

    def test_boundary_follows_adx_convention(self):
        # ADX > 25 = tren kuat (sama dengan 'adx_strong' di calculate_adx),
        # ADX < 20 = sideways. Tepat di batas = netral.
        assert detect_regime(25.0) == REGIME_NONE
        assert detect_regime(20.0) == REGIME_NONE

    def test_sideways(self):
        assert detect_regime(15.0) == REGIME_RANGE
        assert detect_regime(19.0) == REGIME_RANGE

    def test_middle_is_neutral(self):
        assert detect_regime(22.0) == REGIME_NONE

    def test_missing_adx_is_neutral(self):
        assert detect_regime(None) == REGIME_NONE
        assert detect_regime(float('nan')) == REGIME_NONE

    def test_flag_off_disables_regime(self):
        saved = set_features(regime_gate=False)
        try:
            assert detect_regime(30.0) == REGIME_NONE
        finally:
            set_features(**saved)


class TestRangeRegime:
    """Di pasar sideways, aturan momentum (MA/MACD) dikurangi."""

    def test_momentum_points_dampened_in_range(self):
        data = make_data(ma_fast=105.0, ma_slow=95.0, macd=1.0,
                         macd_signal=0.0, macd_hist=0.5, adx=15.0)
        range_signal = SignalService.generate_stock_signal(data)

        data_neutral = make_data(ma_fast=105.0, ma_slow=95.0, macd=1.0,
                                 macd_signal=0.0, macd_hist=0.5, adx=22.0)
        neutral_signal = SignalService.generate_stock_signal(data_neutral)

        assert range_signal['regime'] == REGIME_RANGE
        assert neutral_signal['regime'] == REGIME_NONE
        assert range_signal['buy_score'] < neutral_signal['buy_score']


class TestTrendRegime:
    """Di pasar bertren, RSI ekstrem berlawanan arah tren dijaga."""

    def test_counter_trend_rsi_dampened(self):
        # RSI 25 (oversold) tapi harga di bawah MA lambat = tren turun.
        data = SignalService.generate_stock_signal(make_data(
            rsi=25.0, ma_fast=99.0, ma_slow=100.0, price=95.0,
            vwap=94.0, htf_trend='down', adx=30.0))
        neutral = SignalService.generate_stock_signal(make_data(
            rsi=25.0, ma_fast=99.0, ma_slow=100.0, price=95.0,
            vwap=94.0, htf_trend='down', adx=22.0))

        assert data['regime'] == REGIME_TREND
        assert neutral['regime'] == REGIME_NONE
        assert data['buy_score'] < neutral['buy_score']
        assert any('counter-trend' in r for r in data['reason'].split(', '))

    def test_rsi_with_trend_alignment_keeps_points(self):
        # RSI 25 tapi harga di atas MA lambat = pullback di tren naik.
        aligned = SignalService.generate_stock_signal(make_data(
            rsi=25.0, ma_fast=101.0, ma_slow=100.0, price=105.0,
            vwap=104.0, htf_trend='up', adx=30.0))
        neutral = SignalService.generate_stock_signal(make_data(
            rsi=25.0, ma_fast=101.0, ma_slow=100.0, price=105.0,
            vwap=104.0, htf_trend='up', adx=22.0))

        # Tidak ada pengurangan karena searah dengan tren.
        assert aligned['buy_score'] == neutral['buy_score']


class TestGateDisabled:
    """Flag mati -> skor persis seperti sebelum ada regime gate."""

    def test_scores_identical_when_flag_off(self):
        # ADX tinggi (tren) - kalau gate aktif, RSI counter-trend dikurangi.
        data = make_data(rsi=25.0, ma_fast=99.0, ma_slow=100.0, price=95.0,
                         vwap=94.0, htf_trend='down', adx=30.0)
        saved = set_features(regime_gate=False)
        try:
            without_gate = SignalService.generate_stock_signal(data)
        finally:
            set_features(**saved)

        # ADX di tengah -> regime none, jadi tanpa gate sama saja.
        neutral = SignalService.generate_stock_signal(make_data(
            rsi=25.0, ma_fast=99.0, ma_slow=100.0, price=95.0,
            vwap=94.0, htf_trend='down', adx=22.0))

        assert without_gate['buy_score'] == neutral['buy_score']
        assert without_gate['sell_score'] == neutral['sell_score']
        assert without_gate['signal'] == neutral['signal']
        assert without_gate['regime'] == REGIME_NONE


class TestCryptoPath:
    """Jalur crypto juga pakai regime gate."""

    def test_crypto_regime_detected(self):
        data = {
            'price': 1000.0,
            'change': 0.0,
            'rsi': 50.0,
            'ma_fast': 1000.0,
            'ma_slow': 1000.0,
            'macd': 0.0,
            'macd_signal': 0.0,
            'macd_hist': 0.0,
            'bb_upper': 1100.0,
            'bb_lower': 900.0,
            'atr': 25.0,
            'volume_ratio': 1.0,
            'vwap': 999.0,
            'adx': 14.0,
            'plus_di': 20.0,
            'minus_di': 20.0,
            'adx_strong': False,
            'htf_trend': 'up',
            'timeframe': '60',
        }
        signal = SignalService.generate_crypto_signal(data)
        assert signal['regime'] == REGIME_RANGE

    def test_crypto_without_adx_is_neutral(self):
        data = {
            'price': 1000.0,
            'change': 0.0,
            'rsi': 50.0,
            'atr': 25.0,
            'volume_ratio': 1.0,
        }
        signal = SignalService.generate_crypto_signal(data)
        assert signal['regime'] == REGIME_NONE
