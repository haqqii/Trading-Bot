"""
Stock data fetching service using Yahoo Finance, TradingView, and Finnhub.
"""
import os
import time
import logging
import requests
import yfinance as yf
import pandas as pd
import warnings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.cache import _price_cache
from utils.rate_limiter import _yahoo_limiter, _circuit_breakers, exponential_backoff
from utils.indicators import calculate_rsi, calculate_macd, calculate_bollinger_bands, calculate_volume_metrics, calculate_atr, calculate_sr_levels

logger = logging.getLogger(__name__)

# HTTP session with connection pooling
_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=20,
    max_retries=0,  # We handle retries manually
    pool_block=False
)
_session.mount('http://', _adapter)
_session.mount('https://', _adapter)
_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

# Finnhub API key from environment
FINNHUB_API_KEY = os.getenv('FINNHUB_API_KEY', '')

# Yahoo Finance blacklist
YAHOO_BLACKLIST = {
    'XBIN', 'BMAS', 'XPIN', 'MPRO', 'XMSK', 'XBSK', 'XRDN',
    'XISI', 'XISB', 'XIIC', 'XIIF', 'XIPI', 'XISR', 'XIML',
    'XJI', 'XIHD', 'XIFE', 'XIPB', 'XPLQ', 'XBLQ', 'XMLF',
    'XBSK', 'XSSI', 'XCEG', 'XPTD', 'XPDV', 'XPFT', 'XPSG',
    'XBIN', 'XKID', 'XKIV', 'XKMS', 'XSSK', 'XSBC', 'XRDN',
    'XCID', 'XCIS', 'XMGB', 'XMIG', 'R-LQ45X', 'SSTM',
}


class StockService:
    """Service for fetching stock data"""

    def __init__(self):
        self.cache = _price_cache

    def get_stock_data(self, ticker: str, interval: str = '5m', period: str = '5d', retry: int = 1):
        """Get stock data from Yahoo Finance with rate limiting and circuit breaker"""
        # Check cache first
        cache_key = f"{ticker}:{interval}:{period}"
        cached = _price_cache.get(cache_key)
        if cached is not None:
            return cached

        breaker = _circuit_breakers.get('yahoo_stock')

        if breaker and not breaker.can_execute():
            logger.warning(f"Yahoo stock circuit breaker OPEN for {ticker}, skipping")
            return None

        for attempt in range(retry):
            try:
                _yahoo_limiter.wait_if_needed()

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")

                stock = yf.Ticker(ticker)
                hist = stock.history(interval=interval, period=period, timeout=8)  # Reduced from 10s

                if hist.empty or len(hist) < 50:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                        continue
                    return None

                df = hist.copy()
                df['MA_FAST'] = df['Close'].rolling(8).mean()
                df['MA_SLOW'] = df['Close'].rolling(21).mean()
                df['RSI'] = calculate_rsi(df['Close'])

                macd_data = calculate_macd(df['Close'])
                df['MACD'] = macd_data['macd']
                df['MACD_SIGNAL'] = macd_data['signal']
                df['MACD_HIST'] = macd_data['histogram']

                bb_data = calculate_bollinger_bands(df['Close'])
                df['BB_UPPER'] = bb_data['upper']
                df['BB_MIDDLE'] = bb_data['middle']
                df['BB_LOWER'] = bb_data['lower']

                volume_data = calculate_volume_metrics(df['Volume'])
                df['VOLUME_MA'] = volume_data['ma']
                df['VOLUME_RATIO'] = volume_data['ratio']

                latest = df.iloc[-1]
                atr_val = calculate_atr(df['High'], df['Low'], df['Close'], 14).iloc[-1]

                # Calculate Support & Resistance levels
                try:
                    sr_data = calculate_sr_levels(df['High'], df['Low'], df['Close'], df['Volume'])
                except Exception as e:
                    logger.debug(f"S/R calculation failed for {ticker}: {e}")
                    sr_data = {}

                if breaker:
                    breaker.record_success()

                # Safely get stock name
                stock_info = stock.info or {}
                stock_name = stock_info.get('longName') or stock_info.get('shortName') or ticker

                return {
                    'name': stock_name,
                    'price': latest['Close'],
                    'change': ((latest['Close'] - df.iloc[-2]['Close']) / df.iloc[-2]['Close']) * 100,
                    'ma_fast': latest['MA_FAST'],
                    'ma_slow': latest['MA_SLOW'],
                    'rsi': latest['RSI'],
                    'volume': latest['Volume'],
                    'atr': atr_val,
                    'candles': len(df),
                    'source': 'yahoo',
                    'macd': latest['MACD'],
                    'macd_signal': latest['MACD_SIGNAL'],
                    'macd_hist': latest['MACD_HIST'],
                    'bb_upper': latest['BB_UPPER'],
                    'bb_middle': latest['BB_MIDDLE'],
                    'bb_lower': latest['BB_LOWER'],
                    'volume_ma': latest['VOLUME_MA'],
                    'volume_ratio': latest['VOLUME_RATIO'],
                    'raw_df': df,  # For pattern detection
                    # Support & Resistance
                    'sr': sr_data or {},
                    'support': sr_data.get('nearest_support', {}).get('level') if sr_data and sr_data.get('nearest_support') else None,
                    'resistance': sr_data.get('nearest_resistance', {}).get('level') if sr_data and sr_data.get('nearest_resistance') else None,
                }

                # Cache successful result
                _price_cache.set(cache_key, result)
                return result
            except Exception as e:
                if breaker:
                    breaker.record_failure()
                logger.error(f"Error getting stock data for {ticker}: {e}")
                if attempt < retry - 1:
                    time.sleep(exponential_backoff(attempt, base_delay=0.5, max_delay=4))
                    continue
                return None

        return None

    def get_stock_data_tradingview(self, ticker: str, retry: int = 1):
        """Get stock data from TradingView API"""
        breaker = _circuit_breakers.get('tradingview')

        if breaker and not breaker.can_execute():
            logger.warning(f"TradingView circuit breaker OPEN for {ticker}, skipping")
            return None

        for attempt in range(retry):
            try:
                time.sleep(0.1)

                symbol = f"IDX:{ticker}"
                url = "https://scanner.tradingview.com/indonesia/scan"

                payload = {
                    'filter': [{'left': 'ticker', 'operation': 'equal', 'right': symbol}],
                    'columns': ['name', 'close', 'change', 'volume', 'RSI', 'SMA20', 'SMA50'],
                    'range': [0, 1]
                }

                resp = _session.post(url, json=payload, timeout=10)

                if resp.status_code != 200:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=10))
                        continue
                    return None

                data = resp.json()
                if not data.get('data'):
                    return None

                item = data['data'][0]
                d = item.get('d', [])

                if len(d) < 7:
                    return None

                price = d[1] if d[1] else 0
                change = d[2] if d[2] else 0
                volume = d[3] if d[3] else 0
                rsi = d[4] if d[4] else 50
                sma20 = d[5] if d[5] else price
                sma50 = d[6] if d[6] else price

                if price <= 0:
                    return None

                if breaker:
                    breaker.record_success()

                return {
                    'name': ticker,
                    'price': price,
                    'change': change,
                    'ma_fast': sma20,
                    'ma_slow': sma50,
                    'rsi': rsi,
                    'volume': volume,
                    'atr': price * 0.015,
                    'candles': 50,
                    'source': 'tradingview',
                    'macd': change * 0.01 if change else 0,
                    'macd_signal': 0,
                    'macd_hist': change * 0.01 if change else 0,
                    'bb_upper': price * 1.03,
                    'bb_middle': price,
                    'bb_lower': price * 0.97,
                    'volume_ma': volume,
                    'volume_ratio': 1.0,
                    'raw_df': None,  # TradingView doesn't provide raw OHLCV
                }
            except Exception as e:
                if breaker:
                    breaker.record_failure()
                logger.error(f"TradingView error for {ticker}: {e}")
                if attempt < retry - 1:
                    time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=10))
                    continue
                return None

        return None

    def get_stock_data_combined(self, ticker: str, interval: str = '5m', period: str = '5d'):
        """Get stock data - try Yahoo Finance first, then TradingView, then Finnhub"""
        base_ticker = ticker.replace('.JK', '')
        if base_ticker in YAHOO_BLACKLIST:
            data = self.get_stock_data_tradingview(base_ticker)
            if data:
                return data
            # Try Finnhub as backup for blacklisted tickers
            if FINNHUB_API_KEY:
                return self.get_stock_data_finnhub(base_ticker)
            return None

        data = self.get_stock_data(ticker, interval, period)
        if data:
            return data

        data = self.get_stock_data_tradingview(base_ticker)
        if data:
            return data

        # Try Finnhub as final fallback
        if FINNHUB_API_KEY:
            return self.get_stock_data_finnhub(base_ticker)

        return None

    def get_stock_data_finnhub(self, ticker: str, retry: int = 1):
        """Get stock data from Finnhub API"""
        if not FINNHUB_API_KEY:
            return None

        breaker = _circuit_breakers.get('finnhub')
        if breaker and not breaker.can_execute():
            logger.warning(f"Finnhub circuit breaker OPEN for {ticker}, skipping")
            return None

        for attempt in range(retry):
            try:
                time.sleep(0.1)

                # Finnhub uses Indonesia: prefix for IDX stocks
                symbol = f"IDX:{ticker}"
                url = f"https://finnhub.io/api/v1/scan/technical-indicator?symbol={symbol}&token={FINNHUB_API_KEY}"

                resp = _session.get(url, timeout=10)

                if resp.status_code != 200:
                    if attempt < retry - 1:
                        time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=10))
                        continue
                    if breaker:
                        breaker.record_failure()
                    return None

                data = resp.json()
                if not data or data.get('s') != 'no_signals':
                    # Get quote data
                    quote_url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
                    quote_resp = _session.get(quote_url, timeout=10)

                    if quote_resp.status_code != 200:
                        if breaker:
                            breaker.record_failure()
                        return None

                    quote = quote_resp.json()
                    if not quote or quote.get('c') == 0:
                        if breaker:
                            breaker.record_failure()
                        return None

                    price = quote.get('c', 0)
                    change = quote.get('dp', 0)

                    if price <= 0:
                        if breaker:
                            breaker.record_failure()
                        return None

                    if breaker:
                        breaker.record_success()

                    return {
                        'price': price,
                        'change': change,
                        'rsi': 50,
                        'ma_fast': price,
                        'ma_slow': price,
                        'macd': 0,
                        'macd_signal': 0,
                        'macd_hist': 0,
                        'bb_upper': price * 1.02,
                        'bb_middle': price,
                        'bb_lower': price * 0.98,
                        'volume_ratio': 1.0,
                        'atr': price * 0.015,
                        'candles': 100,
                        'source': 'finnhub'
                    }

            except Exception as e:
                logger.error(f"Finnhub error for {ticker}: {e}")
                if attempt < retry - 1:
                    time.sleep(exponential_backoff(attempt, base_delay=1.0, max_delay=10))
                    continue
                if breaker:
                    breaker.record_failure(str(e))

        return None

    def load_stocks(self):
        """Load all IDX stocks from TradingView, fallback to local idx_stocks.py"""
        url = "https://scanner.tradingview.com/indonesia/scan"
        stocks = {}

        for page in range(5):
            offset = page * 200
            payload = {
                'filter': [],
                'symbols': {'query': {'tickers': []}},
                'columns': ['name', 'description'],
                'range': [offset, offset + 200]
            }

            try:
                resp = _session.post(url, json=payload, timeout=10)
                data = resp.json()
                items = data.get('data', [])
                if not items:
                    break
                for item in items:
                    symbol = item.get('s', '')
                    if symbol.startswith('IDX:'):
                        ticker = symbol.replace('IDX:', '')
                        d = item.get('d', [])
                        name = d[1] if len(d) > 1 else (d[0] if d else ticker)
                        stocks[ticker] = name
            except Exception as e:
                logger.warning(f"TradingView error (page {page}): {e}")
                break

        # Fallback to local idx_stocks.py if TradingView failed
        if not stocks:
            logger.warning("TradingView returned 0 stocks, falling back to local idx_stocks.py")
            try:
                from data.idx_stocks import ALL_IDX_STOCKS
                stocks = dict(ALL_IDX_STOCKS)
                logger.info(f"Loaded {len(stocks)} stocks from local idx_stocks.py")
            except ImportError:
                logger.error("Could not import idx_stocks.py fallback")

        logger.info(f"Loaded {len(stocks)} stocks total")
        return stocks


# Singleton instance
stock_service = StockService()
