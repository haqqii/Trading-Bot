"""
Chart generation service - Professional candlestick charts.
Style: TradingView-like with MA, Bollinger Bands, Volume.
"""
import io
import logging
import warnings
import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

logger = logging.getLogger(__name__)


class ChartService:
    """Service for generating professional candlestick charts"""

    @staticmethod
    def get_mpl_config():
        """Configure matplotlib for dark TradingView-like theme"""
        matplotlib.rcParams['font.family'] = 'DejaVu Sans'
        matplotlib.rcParams['font.size'] = 9
        matplotlib.rcParams['figure.facecolor'] = '#131722'
        matplotlib.rcParams['axes.facecolor'] = '#131722'
        matplotlib.rcParams['axes.edgecolor'] = '#363a45'
        matplotlib.rcParams['axes.labelcolor'] = '#787b86'
        matplotlib.rcParams['xtick.color'] = '#787b86'
        matplotlib.rcParams['ytick.color'] = '#787b86'
        matplotlib.rcParams['text.color'] = '#d1d4dc'
        matplotlib.rcParams['grid.color'] = '#1e222d'
        matplotlib.rcParams['grid.alpha'] = 0.8
        matplotlib.rcParams['axes.spines.top'] = False
        matplotlib.rcParams['axes.spines.right'] = False
        return plt

    def generate_price_chart(self, ticker: str, interval: str = '1h', period: str = '5d',
                            indicators=None, width: int = 900, height: int = 520):
        """Generate a professional candlestick chart with TradingView style."""
        try:
            plt = self.get_mpl_config()

            if indicators is None:
                indicators = ['ma', 'bb', 'volume', 'sr']

            # Map interval
            interval_to_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440}
            interval_mins = interval_to_min.get(interval, 60)

            if interval_mins >= 60 and period in ['1d', '2d']:
                period = '5d'
            if interval_mins >= 1440 and period in ['1d', '2d', '3d', '5d']:
                period = '14d'

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

            stock = yf.Ticker(ticker)
            hist = stock.history(interval=interval, period=period, timeout=15)

            if hist.empty or len(hist) < 20:
                return None

            df = hist.copy()
            n = len(df)

            # Calculate Moving Averages
            df['MA7'] = df['Close'].rolling(7).mean()
            df['MA25'] = df['Close'].rolling(25).mean()
            df['MA99'] = df['Close'].rolling(99).mean()

            # Calculate Bollinger Bands
            bb_period = 20
            df['BB_MID'] = df['Close'].rolling(bb_period).mean()
            bb_std = df['Close'].rolling(bb_period).std()
            df['BB_UPPER'] = df['BB_MID'] + (2 * bb_std)
            df['BB_LOWER'] = df['BB_MID'] - (2 * bb_std)

            # Calculate S/R levels
            sr_levels = self._calculate_sr_levels(df)

            # Create figure with 2 subplots
            fig = plt.figure(figsize=(width/100, height/100), dpi=100)
            gs = fig.add_gridspec(2, 1, height_ratios=[5, 1], hspace=0.02)

            ax_price = fig.add_subplot(gs[0])
            ax_vol = fig.add_subplot(gs[1])

            # X positions for bars
            x = np.arange(n)

            # === CANDLESTICK CHART ===
            colors = []
            for i in range(n):
                if df['Close'].iloc[i] >= df['Open'].iloc[i]:
                    colors.append('#26a69a')  # Green
                else:
                    colors.append('#ef5350')  # Red

            # Draw wicks
            for i in range(n):
                ax_price.plot([x[i], x[i]], [df['Low'].iloc[i], df['High'].iloc[i]],
                            color=colors[i], linewidth=0.8, solid_capstyle='round')

            # Draw bodies
            body_height = np.abs(df['Close'].values - df['Open'].values)
            body_bottom = np.minimum(df['Open'].values, df['Close'].values)

            ax_price.bar(x, body_height, bottom=body_bottom, color=colors, width=0.6, edgecolor=colors, linewidth=0.5)

            # Bollinger Bands - dotted blue lines
            if 'bb' in indicators:
                ax_price.plot(x, df['BB_UPPER'], color='#2962ff', linewidth=0.8, linestyle=':', alpha=0.8)
                ax_price.plot(x, df['BB_MID'], color='#2962ff', linewidth=0.8, linestyle=':', alpha=0.6)
                ax_price.plot(x, df['BB_LOWER'], color='#2962ff', linewidth=0.8, linestyle=':', alpha=0.8)
                ax_price.fill_between(x, df['BB_LOWER'], df['BB_UPPER'], alpha=0.05, color='#2962ff')

            # MA lines
            if 'ma' in indicators:
                ax_price.plot(x, df['MA7'], color='#faf105', linewidth=1.2, label='MA7')
                ax_price.plot(x, df['MA25'], color='#00bcd4', linewidth=1.2, label='MA25')
                ax_price.plot(x, df['MA99'], color='#e91e63', linewidth=1.5, label='MA99')

            # S/R lines
            if 'sr' in indicators and sr_levels:
                for level, label in sr_levels['resistances'][:1]:
                    ax_price.axhline(y=level, color='#ff5252', linewidth=1, linestyle='--', alpha=0.7)
                for level, label in sr_levels['supports'][:1]:
                    ax_price.axhline(y=level, color='#4caf50', linewidth=1, linestyle='--', alpha=0.7)

            # Price info
            current_price = df['Close'].iloc[-1]
            high_price = df['High'].iloc[-1]
            low_price = df['Low'].iloc[-1]
            open_price = df['Open'].iloc[-1]
            first_price = df['Close'].iloc[0]
            change = ((current_price - first_price) / first_price) * 100
            change_color = '#4caf50' if change >= 0 else '#ff5252'

            ticker_name = ticker.replace('.JK', '').replace('-USD', '')
            ax_price.set_title(f'{ticker_name}', fontsize=11, fontweight='bold', pad=8, color='#eaeaea')

            # OHLCV data box
            ohlcv_text = f'O {open_price:.0f}   H {high_price:.0f}   L {low_price:.0f}   C {current_price:.0f}'
            ax_price.text(0.99, 0.97, ohlcv_text,
                        transform=ax_price.transAxes, fontsize=8, color='#787b86',
                        ha='right', va='top',
                        bbox=dict(boxstyle='round,pad=0.3', facecolor='#131722', edgecolor='#363a45', alpha=0.9))

            # Change percentage
            ax_price.text(0.01, 0.97, f'{change:+.2f}%',
                        transform=ax_price.transAxes, fontsize=10, fontweight='bold',
                        color=change_color, ha='left', va='top')

            ax_price.legend(loc='upper left', fontsize=8, framealpha=0.7,
                          labelcolor='#d1d4dc', facecolor='#131722')
            ax_price.grid(True, alpha=0.3, color='#1e222d')
            ax_price.tick_params(labelbottom=False)
            ax_price.set_facecolor('#131722')

            # Set x/y limits
            price_min = df['BB_LOWER'].min()
            price_max = df['BB_UPPER'].max()
            price_range = price_max - price_min
            ax_price.set_ylim(price_min - price_range * 0.05, price_max + price_range * 0.12)
            ax_price.set_xlim(-0.5, n - 0.5)

            # === VOLUME CHART ===
            if 'volume' in indicators:
                vol_colors = ['#26a69a' if df['Close'].iloc[i] >= df['Open'].iloc[i] else '#ef5350' for i in range(n)]
                ax_vol.bar(x, df['Volume'] / 1e6, color=vol_colors, alpha=0.7, width=0.6)
                ax_vol.set_ylabel('Vol', fontsize=8, color='#787b86')
                ax_vol.grid(True, alpha=0.3, color='#1e222d')
                ax_vol.set_facecolor('#131722')
                ax_vol.tick_params(labelbottom=False, colors='#787b86')
                ax_vol.set_xlim(-0.5, n - 0.5)
            else:
                ax_vol.set_visible(False)

            # X-axis labels
            tick_step = max(1, n // 8)
            tick_positions = x[::tick_step]
            tick_labels = [df.index[i].strftime('%H:%M') for i in range(0, n, tick_step)]
            ax_vol.set_xticks(tick_positions)
            ax_vol.set_xticklabels(tick_labels, fontsize=8, color='#787b86')

            plt.tight_layout()

            # Save
            buf = io.BytesIO()
            plt.savefig(buf, format='png', facecolor='#131722', edgecolor='none', dpi=100,
                       bbox_inches='tight', pad_inches=0.5)
            buf.seek(0)
            plt.close()

            return buf

        except Exception as e:
            logger.error(f"Chart error for {ticker}: {e}")
            try:
                plt.close()
            except:
                pass
            return None

    def _calculate_sr_levels(self, df):
        """Calculate S/R levels using pivot points"""
        try:
            last_high = df['High'].iloc[-1]
            last_low = df['Low'].iloc[-1]
            last_close = df['Close'].iloc[-1]

            pp = (last_high + last_low + last_close) / 3
            r1 = 2 * pp - last_low
            r2 = pp + (last_high - last_low)
            s1 = 2 * pp - last_high
            s2 = pp - (last_high - last_low)

            supports = [(s1, 'S1'), (s2, 'S2')]
            resistances = [(r1, 'R1'), (r2, 'R2')]

            return {'supports': supports, 'resistances': resistances}
        except:
            return None

    def generate_crypto_chart(self, ticker: str, interval: str = '1h', period: str = '3d', indicators=None):
        """Generate chart for crypto"""
        if not ticker.endswith('-USD') and not ticker.endswith('-USDT'):
            ticker = f"{ticker}-USD"
        return self.generate_price_chart(ticker, interval, period, indicators)


# Singleton instance
chart_service = ChartService()
