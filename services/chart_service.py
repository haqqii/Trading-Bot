"""
Chart generation service.
"""
import io
import logging
import warnings
import yfinance as yf
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


class ChartService:
    """Service for generating price charts"""

    @staticmethod
    def get_mpl_config():
        """Configure matplotlib for headless rendering"""
        matplotlib.rcParams['font.family'] = 'DejaVu Sans'
        matplotlib.rcParams['font.size'] = 8
        matplotlib.rcParams['axes.titlesize'] = 10
        matplotlib.rcParams['axes.labelsize'] = 8
        matplotlib.rcParams['xtick.labelsize'] = 7
        matplotlib.rcParams['ytick.labelsize'] = 7
        matplotlib.rcParams['figure.facecolor'] = '#1a1a2e'
        matplotlib.rcParams['axes.facecolor'] = '#16213e'
        matplotlib.rcParams['axes.edgecolor'] = '#0f3460'
        matplotlib.rcParams['axes.labelcolor'] = '#e8e8e8'
        matplotlib.rcParams['xtick.color'] = '#e8e8e8'
        matplotlib.rcParams['ytick.color'] = '#e8e8e8'
        matplotlib.rcParams['text.color'] = '#e8e8e8'
        matplotlib.rcParams['grid.color'] = '#0f3460'
        matplotlib.rcParams['grid.alpha'] = 0.5
        return plt

    def generate_price_chart(self, ticker: str, interval: str = '1h', period: str = '5d',
                            indicators=None, width: int = 800, height: int = 480):
        """Generate a price chart image with technical indicators."""
        try:
            plt = self.get_mpl_config()

            if indicators is None:
                indicators = ['ma', 'rsi', 'volume']

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

            # Calculate indicators
            df['MA20'] = df['Close'].rolling(20).mean()
            df['MA50'] = df['Close'].rolling(50).mean()
            df['MA200'] = df['Close'].rolling(200).mean()

            # RSI
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))

            # MACD
            ema12 = df['Close'].ewm(span=12, adjust=False).mean()
            ema26 = df['Close'].ewm(span=26, adjust=False).mean()
            df['MACD'] = ema12 - ema26
            df['MACD_SIGNAL'] = df['MACD'].ewm(span=9, adjust=False).mean()
            df['MACD_HIST'] = df['MACD'] - df['MACD_SIGNAL']

            # Bollinger Bands
            bb_middle = df['Close'].rolling(20).mean()
            bb_std = df['Close'].rolling(20).std()
            df['BB_UPPER'] = bb_middle + (2 * bb_std)
            df['BB_LOWER'] = bb_middle - (2 * bb_std)

            # Determine number of subplots
            n_subplots = 1
            if 'rsi' in indicators:
                n_subplots += 1
            if 'macd' in indicators:
                n_subplots += 1
            if 'volume' in indicators:
                n_subplots += 1

            # Create figure
            fig, axes = plt.subplots(n_subplots, 1, figsize=(width/100, height/100), dpi=100,
                                      gridspec_kw={'height_ratios': [3] + [1] * (n_subplots - 1)})

            if n_subplots == 1:
                axes = [axes]

            ax = axes[0]

            # Plot price
            ax.plot(df.index, df['Close'], color='#00d4ff', linewidth=1.5, label='Price')

            if 'ma' in indicators:
                if not df['MA20'].isna().all():
                    ax.plot(df.index, df['MA20'], color='#ffd700', linewidth=1, alpha=0.8, label='MA20')
                if not df['MA50'].isna().all():
                    ax.plot(df.index, df['MA50'], color='#ff6b6b', linewidth=1, alpha=0.8, label='MA50')
                if not df['MA200'].isna().all():
                    ax.plot(df.index, df['MA200'], color='#4ecdc4', linewidth=1, alpha=0.8, label='MA200')

            if 'bollinger' in indicators:
                ax.fill_between(df.index, df['BB_UPPER'], df['BB_LOWER'], alpha=0.1, color='#9b59b6')
                ax.plot(df.index, df['BB_UPPER'], color='#9b59b6', linewidth=0.5, alpha=0.5)
                ax.plot(df.index, df['BB_LOWER'], color='#9b59b6', linewidth=0.5, alpha=0.5)

            ax.set_title(f'{ticker} - {period} ({interval})', color='white', fontweight='bold')
            ax.legend(loc='upper left', fontsize=7)
            ax.grid(True, alpha=0.3)

            current_price = df['Close'].iloc[-1]
            change = ((df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0]) * 100
            color = '#00ff88' if change >= 0 else '#ff4757'
            ax.annotate(f'${current_price:.2f} ({change:+.1f}%)',
                       xy=(df.index[-1], current_price),
                       xytext=(10, 0), textcoords='offset points',
                       fontsize=9, color=color, fontweight='bold')

            current_idx = 0

            # RSI subplot
            if 'rsi' in indicators:
                current_idx += 1
                ax_rsi = axes[current_idx]
                ax_rsi.plot(df.index, df['RSI'], color='#ff6b6b', linewidth=1)
                ax_rsi.axhline(y=70, color='#ff4757', linestyle='--', linewidth=0.5, alpha=0.5)
                ax_rsi.axhline(y=30, color='#00ff88', linestyle='--', linewidth=0.5, alpha=0.5)
                ax_rsi.fill_between(df.index, 70, 100, alpha=0.1, color='#ff4757')
                ax_rsi.fill_between(df.index, 0, 30, alpha=0.1, color='#00ff88')
                ax_rsi.set_ylim(0, 100)
                ax_rsi.set_ylabel('RSI', fontsize=7)
                ax_rsi.grid(True, alpha=0.3)
                ax_rsi.set_title('RSI (14)', fontsize=7)

            # MACD subplot
            if 'macd' in indicators:
                current_idx += 1
                ax_macd = axes[current_idx]
                ax_macd.plot(df.index, df['MACD'], color='#00d4ff', linewidth=1, label='MACD')
                ax_macd.plot(df.index, df['MACD_SIGNAL'], color='#ffd700', linewidth=1, label='Signal')
                colors = ['#00ff88' if v >= 0 else '#ff4757' for v in df['MACD_HIST']]
                ax_macd.bar(df.index, df['MACD_HIST'], color=colors, alpha=0.5, width=0.8)
                ax_macd.axhline(y=0, color='white', linestyle='-', linewidth=0.3)
                ax_macd.legend(loc='upper left', fontsize=6)
                ax_macd.grid(True, alpha=0.3)
                ax_macd.set_title('MACD (12,26,9)', fontsize=7)

            # Volume subplot
            if 'volume' in indicators:
                current_idx += 1
                ax_vol = axes[current_idx]
                vol_colors = ['#00ff88' if df['Close'].iloc[i] >= df['Open'].iloc[i] else '#ff4757'
                              for i in range(len(df))]
                ax_vol.bar(df.index, df['Volume'], color=vol_colors, alpha=0.7, width=0.8)
                vol_ma = df['Volume'].rolling(20).mean()
                ax_vol.plot(df.index, vol_ma, color='#ffd700', linewidth=0.8, alpha=0.8)
                ax_vol.set_ylabel('Volume', fontsize=7)
                ax_vol.grid(True, alpha=0.3)
                ax_vol.set_title('Volume', fontsize=7)

            plt.tight_layout()

            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', facecolor='#1a1a2e', edgecolor='none', dpi=100)
            buf.seek(0)
            plt.close()

            return buf

        except Exception as e:
            logger.error(f"Chart generation error for {ticker}: {e}")
            try:
                plt.close()
            except:
                pass
            return None

    def generate_crypto_chart(self, ticker: str, interval: str = '1h', period: str = '3d', indicators=None):
        """Generate chart for crypto ticker"""
        if not ticker.endswith('-USD') and not ticker.endswith('-USDT'):
            ticker = f"{ticker}-USD"
        return self.generate_price_chart(ticker, interval, period, indicators)


# Singleton instance
chart_service = ChartService()
