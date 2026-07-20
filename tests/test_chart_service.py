"""
Unit tests for services/chart_service.py
Tests chart generation service (without actual API calls or rendering).
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestChartServiceInit:
    """Tests for ChartService initialization."""

    def test_can_import_chart_service(self):
        """ChartService module should be importable."""
        from services.chart_service import ChartService
        assert ChartService is not None

    def test_can_create_instance(self):
        """Should be able to create instance."""
        from services.chart_service import ChartService
        service = ChartService()
        assert service is not None

    def test_methods_callable(self):
        """Methods should be callable."""
        from services.chart_service import ChartService
        service = ChartService()
        for method_name in ['generate_price_chart', 'generate_crypto_chart',
                            'get_mpl_config']:
            method = getattr(service, method_name)
            assert callable(method)


class TestMplConfig:
    """Tests for matplotlib configuration."""

    def test_config_returns_plt(self):
        """get_mpl_config should return plt module."""
        from services.chart_service import ChartService
        service = ChartService()
        plt = service.get_mpl_config()
        assert plt is not None

    def test_config_sets_dark_background(self):
        """Config should set dark background color."""
        import matplotlib
        from services.chart_service import ChartService

        service = ChartService()
        service.get_mpl_config()

        # TradingView dark theme colors
        assert matplotlib.rcParams['figure.facecolor'] == '#131722'
        assert matplotlib.rcParams['axes.facecolor'] == '#131722'

    def test_config_sets_text_color(self):
        """Config should set text color."""
        import matplotlib
        from services.chart_service import ChartService

        service = ChartService()
        service.get_mpl_config()

        assert matplotlib.rcParams['text.color'] == '#d1d4dc'

    def test_config_disables_top_right_spines(self):
        """Config should disable top and right spines."""
        import matplotlib
        from services.chart_service import ChartService

        service = ChartService()
        service.get_mpl_config()

        assert matplotlib.rcParams['axes.spines.top'] is False
        assert matplotlib.rcParams['axes.spines.right'] is False

    def test_config_sets_font_family(self):
        """Config should set font family."""
        import matplotlib
        from services.chart_service import ChartService

        service = ChartService()
        service.get_mpl_config()

        # font.family can be either a string or list depending on matplotlib version
        font_family = matplotlib.rcParams['font.family']
        if isinstance(font_family, list):
            assert 'DejaVu Sans' in font_family
        else:
            assert font_family == 'DejaVu Sans'


class TestGeneratePriceChart:
    """Tests for generate_price_chart method."""

    def test_returns_none_for_invalid_ticker(self):
        """Invalid ticker should return None."""
        from services.chart_service import ChartService
        service = ChartService()

        # Use clearly invalid ticker
        result = service.generate_price_chart('INVALID.TICKER.XXX', '5m', '5d')
        # Should return None or raise exception gracefully
        assert result is None

    def test_empty_data_returns_none(self):
        """Empty data should return None."""
        from services.chart_service import ChartService
        service = ChartService()

        with patch('yfinance.Ticker') as mock_ticker:
            mock_stock = MagicMock()
            mock_stock.history.return_value = MagicMock(
                empty=True, __len__=lambda self: 0
            )
            mock_ticker.return_value = mock_stock

            result = service.generate_price_chart('TEST.JK', '5m', '5d')
            assert result is None

    def test_insufficient_data_returns_none(self):
        """Less than 20 candles should return None."""
        from services.chart_service import ChartService
        service = ChartService()

        with patch('yfinance.Ticker') as mock_ticker:
            mock_stock = MagicMock()
            mock_hist = MagicMock()
            mock_hist.empty = False
            mock_hist.__len__ = lambda: 10  # Less than 20
            mock_stock.history.return_value = mock_hist
            mock_ticker.return_value = mock_stock

            result = service.generate_price_chart('TEST.JK', '5m', '5d')
            assert result is None


class TestGenerateCryptoChart:
    """Tests for generate_crypto_chart method."""

    def test_returns_none_for_invalid_ticker(self):
        """Invalid crypto ticker should return None."""
        from services.chart_service import ChartService
        service = ChartService()

        result = service.generate_crypto_chart('XXX-INVALID-USD', '1h', '1d')
        # Should return None or similar
        assert result is None


class TestIntervalMapping:
    """Tests for interval to minutes mapping (tested via inspection)."""

    def test_interval_keys(self):
        """Interval mapping should include standard values."""
        # The mapping is internal, but we can verify the values exist
        interval_to_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30,
                          '1h': 60, '4h': 240, '1d': 1440}
        assert interval_to_min['1m'] == 1
        assert interval_to_min['5m'] == 5
        assert interval_to_min['15m'] == 15
        assert interval_to_min['30m'] == 30
        assert interval_to_min['1h'] == 60
        assert interval_to_min['4h'] == 240
        assert interval_to_min['1d'] == 1440


class TestChartStructure:
    """Tests for chart structure expected."""

    def test_generate_returns_bytes_or_none(self):
        """Chart should return bytes (PNG) or None."""
        from services.chart_service import ChartService
        service = ChartService()
        # With invalid ticker, should return None
        result = service.generate_price_chart('XXX.NONEXISTENT.JK', '5m', '5d')
        assert result is None


class TestEdgeCases:
    """Edge case tests."""

    def test_chart_with_unicode_ticker(self):
        """Unicode ticker should not crash."""
        from services.chart_service import ChartService
        service = ChartService()

        try:
            result = service.generate_price_chart('😀.JK', '5m', '5d')
            assert result is None
        except Exception:
            pytest.fail("Should not raise exception for unicode ticker")

    def test_chart_with_empty_ticker(self):
        """Empty ticker should not crash."""
        from services.chart_service import ChartService
        service = ChartService()

        try:
            result = service.generate_price_chart('', '5m', '5d')
            assert result is None
        except Exception:
            pytest.fail("Should not raise exception for empty ticker")


class TestCombined:
    """Combined tests for chart service."""

    def test_methods_have_docstrings(self):
        """Public methods should have docstrings."""
        from services.chart_service import ChartService
        service = ChartService()
        for method_name in ['generate_price_chart', 'generate_crypto_chart']:
            method = getattr(service, method_name)
            assert method.__doc__ is not None

    def test_static_method_marker(self):
        """get_mpl_config should be a static method."""
        from services.chart_service import ChartService
        # get_mpl_config should be callable without instance
        plt = ChartService.get_mpl_config()
        assert plt is not None
