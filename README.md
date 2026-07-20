# 🤖 Ochobot - IDX Stock & Crypto Signal Bot

Telegram bot for getting Indonesian stock and crypto trading signals based on technical analysis.

## 📁 Project Structure

```
Bot Saham 2/
├── main.py                 # Entry point with graceful shutdown
├── config/                 # Configuration
│   ├── settings.py         # Settings & TIMEFRAMES
│   └── __init__.py
├── handlers/               # Command handlers
│   ├── command_handlers.py # All command handlers
│   ├── scheduler.py        # Background jobs (notifications)
│   └── __init__.py
├── services/               # Business logic
│   ├── stock_service.py    # Stock data (Yahoo, TradingView, Finnhub)
│   ├── crypto_service.py   # Crypto data (Yahoo, CoinGecko)
│   ├── signal_service.py   # Signal generation (BUY/SELL/HOLD/REVERSAL)
│   ├── chart_service.py    # Chart generation
│   └── news_service.py     # News fetching & sentiment analysis
├── utils/                  # Utilities
│   ├── formatters.py       # Message formatting for Telegram
│   ├── cache.py            # In-memory cache with TTL & stale-while-revalidate
│   ├── indicators.py       # Technical indicators
│   ├── rate_limiter.py     # API rate limiting & circuit breaker
│   ├── patterns.py         # Chart pattern detection
│   └── __init__.py
├── tests/                  # Unit tests (352 tests)
│   ├── test_indicators.py
│   ├── test_patterns.py
│   ├── test_signal_service.py
│   ├── test_news_service.py
│   ├── test_formatters.py
│   ├── test_cache_rate_limiter.py
│   ├── test_command_handlers.py
│   ├── test_scheduler.py
│   ├── test_stock_service.py
│   ├── test_crypto_service.py
│   └── test_chart_service.py
├── pytest.ini              # Pytest configuration
├── requirements.txt        # Runtime dependencies
├── requirements-test.txt    # Test dependencies
└── README.md
```

## 📋 Features

### Stocks (IDX)
- **📊 Stock Price** - View real-time prices of popular IDX stocks
- **🎯 Trading Signals** - BUY/SELL signals based on multi-indicators (RSI, MACD, Bollinger Bands, Volume)
- **⭐ Quality Rating** - Signal strength: STRONG (⭐), MODERATE (✨), WEAK (💫)
- **🌙 BSJP (Buy Afternoon Sell Morning)** - Intraday recommendation: buy in afternoon, sell in morning
- **☀️ Morning Watchlist** - Stock recommendations before market open (via notification)
- **📈 Price Alert** - Real-time notification when price rises/falls significantly
- **🎯 TP/SL Tracking** - Auto notification when TP or SL is reached
- **⭐ Favorites** - Monitor your favorite stocks/crypto (manual add)
- **💼 Portfolio Tracker** - Record and track your positions
- **📰 News Sentiment** - Indonesian stock-specific news from Google News RSS with sentiment analysis

### Crypto (24/7)
- **₿ Crypto Signals** - Signals for 250+ crypto pairs
- **💰 Dual Currency** - Price display in USD and IDR
- **📈 Price Alert** - Real-time notification for crypto price movements
- **🎯 TP/SL Tracking** - Auto notification when targets are reached (TP1, TP2, TP3, SL)
- **🔄 REVERSAL Detection** - Special signal for oversold rebounds

### Technical Analysis (Multi-Indicator Scoring)
- **RSI (Relative Strength Index)** - Overbought/Oversold detection
- **MACD (Moving Average Convergence Divergence)** - Trend confirmation
- **Bollinger Bands** - Volatility bands & price channel
- **Volume Analysis** - Volume spike detection
- **MA Crossover** - Golden cross / Death cross
- **Stochastic Oscillator** - Momentum with %K and %D crossover
- **VWAP (Volume Weighted Average Price)** - Average price based on volume
- **ADX (Average Directional Index)** - Trend strength & direction
- **Ichimoku Cloud** - Cloud analysis (Tenkan, Kijun, Senkou)
- **Fibonacci Retracement** - Support/resistance levels (23.6%, 38.2%, 50%, 61.8%)
- **Pattern Detection** - Channel, Triangle, Wedge patterns with confidence scores
- **Weighted Scoring** - Combination of all indicators with weights
- **Interactive Charts** - TradingView-style candlestick charts (via `/chart`)

### Notifications
- **Stock Notifications** - TP/SL hit (09:00-15:00 WIB)
- **Favorites Notifications** - Alert if price changes ≥1% (stocks & crypto)
- **Morning Notifications** - Stock signals before market open (07:15-08:00 WIB)
- **Crypto Notifications** - Active 24/7
- **BSJP Notifications** - Auto at 14:00-16:00 WIB (before market close)

### Reliability & Performance
- **API Protection** - Rate limiting + circuit breaker for all external APIs
- **Caching** - TTL-based cache with stale-while-revalidate fallback
- **Parallel Fetching** - Stock + news data fetched concurrently for fast response
- **Graceful Shutdown** - Clean Ctrl+C handling without traceback
- **Stock vs Crypto Priority** - IDX stocks take priority over ticker name collisions

## 🚀 Installation

1. **Clone or download this repository**

2. **Install runtime dependencies:**
```bash
pip install -r requirements.txt
```

3. **Install test dependencies (optional, for development):**
```bash
pip install -r requirements-test.txt
```

4. **Get Bot Token:**
   - Open Telegram
   - Search for @BotFather
   - Type `/newbot`
   - Follow instructions and save your bot token

5. **Create `.env` file:**
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Optional API keys for enhanced functionality
FINNHUB_API_KEY=your_finnhub_key
NEWS_API_KEY=your_newsapi_key
```

## ▶️ Running

```bash
python main.py
```

or on Windows, click `run_bot.bat`

## 🧪 Testing

The project has a comprehensive test suite with **352 tests** covering all major modules.

```bash
# Run all tests
python -m pytest tests/

# Run with verbose output
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_indicators.py

# Run with coverage
python -m pytest tests/ --cov=utils --cov=services --cov=handlers
```

**Test Coverage:**
| Module | Tests | Description |
|--------|-------|-------------|
| `utils/indicators.py` | 33 | RSI, MACD, BB, Stochastic, ADX, ATR, Fibonacci, Ichimoku, Pivot |
| `utils/patterns.py` | 35 | Channel, Triangle, Wedge pattern detection |
| `services/signal_service.py` | 38 | BUY/SELL/REVERSAL signal generation |
| `services/news_service.py` | 26 | Sentiment analysis, cache, HTML cleaning |
| `utils/formatters.py` | 47 | All message formatting functions |
| `utils/cache.py` & `utils/rate_limiter.py` | 45 | Cache TTL, rate limiting, circuit breaker |
| `handlers/command_handlers.py` | 29 | Markdown stripping, atomic file write |
| `handlers/scheduler.py` | 22 | Timezone, sent-file markers |
| `services/stock_service.py` | 26 | Blacklist, routing logic, API keys |
| `services/crypto_service.py` | 33 | Yahoo/CoinGecko routing, fallback pairs |
| `services/chart_service.py` | 18 | Matplotlib config, chart generation |

## 📱 How to Use

### Button Menu

| Button | Function |
|--------|----------|
| `📊 Harga` | Stock price list |
| `🎯 Sinyal` | Best BUY signals today |
| `⭐ Favorit` | Favorite stocks/crypto + notification toggle |
| `🌙 BSJP` | Buy Afternoon Sell Morning |
| `⏱️ TF` | Select timeframe |
| `💼 Portfolio` | User portfolio |
| `🔔 Notifikasi` | Notification settings (toggle menu) |
| `₿ Crypto` | Crypto signals |

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Start bot and view main menu |
| `/analisa [CODE]` | Detailed technical analysis (stock/crypto) |
| `/harga` | View stock price list |
| `/sinyal` or `/pagi` or `/morning` | Morning Watchlist |
| `/favorit` or `/fav` | View favorites |
| `/bsjp` | BSJP recommendation (Buy Afternoon Sell Morning) |
| `/crypto` | Crypto signals |
| `/tf` or `/timeframe` | Select timeframe (1m, 5m, 15m, 60m) |
| `/add [CODE]` | Add stock to favorites |
| `/remove [CODE]` | Remove from favorites |
| `/portfolio` or `/pf` | View portfolio |
| `/buy [CODE] [PRICE] [LOT]` | Record buy position |
| `/sell [CODE] [LOT]` | Record sell position |
| `/notifikasi` or `/notif` | Notification settings menu |
| `/help` or `/bantuan` | Command list |
| `/chart [CODE] [TF] [PERIOD]` or `/c` | Generate chart |

### Notification Menu (🔔)

Click **🔔 Notifikasi** button to toggle:

| Setting | Description |
|---------|-------------|
| Sinyal Saham | Stock TP/SL notifications |
| Sinyal Crypto | Crypto signals 24/7 |
| BSJP | Buy Afternoon Sell Morning notification |
| Alert Favorit | Price alert on favorites |
| Sinyal Pagi | Morning watchlist notification |

### Price Alerts

| Command | Description |
|---------|-------------|
| `/alertbuy [CODE] [PRICE]` | Alert when price drops to target |
| `/alertsell [CODE] [PRICE]` | Alert when price rises to target |
| `/alerts` | View all alerts |
| `/alertdel [CODE]` | Delete alert |

### Chart Commands

| Command | Description |
|---------|-------------|
| `/chart [CODE] [TF] [PERIOD]` | Generate price chart |
| `/chart BTC-USD` | BTC chart default (1h, 5d) |
| `/chart BBCA 15m 5d` | BBCA chart 15 minutes, 5 days |
| `/c` | Alias for `/chart` |

### Usage Examples

```
/start              → Start bot
/favorit            → View favorites
/add BBCA BREN     → Add stock to favorites
/tf 15              → Change timeframe to 15 minutes
/crypto             → View crypto signals
/bsjp               → BSJP recommendation
/notifikasi         → Notification settings menu
/alertbuy BBCA 9000 → Alert when BBCA drops to 9000
/chart BBCA 1h 5d   → Generate chart
```

## 📊 Data Sources

Bot uses multiple data sources with intelligent fallback:

### Stock Data
1. **Yahoo Finance** (primary) - Indonesian stocks (.JK suffix), real-time data
2. **TradingView** (fallback) - When Yahoo is rate-limited or unavailable
3. **Finnhub** (last fallback) - Requires API key

### Crypto Data
1. **Yahoo Finance** (primary) - Most crypto pairs
2. **CoinGecko** (fallback) - When Yahoo fails

### News Data
1. **Google News RSS** - Indonesian stock-specific news
2. **Finnhub** (fallback) - General market news
3. **NewsAPI** (last fallback) - English-language news

### API Key Setup (Optional)

Add API keys in `.env` file for enhanced functionality:
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_required

# Optional - improves fallback coverage
FINNHUB_API_KEY=your_finnhub_key
NEWS_API_KEY=your_newsapi_key
```

Free API keys:
- Finnhub: https://finnhub.io/
- NewsAPI: https://newsapi.org/

### API Protection & Reliability

- **Rate Limiting**: Per-API request limits (configurable)
- **Circuit Breaker**: Auto-open after N failures, auto-reset on market open
- **Exponential Backoff**: Retry with increasing delay
- **TTL Cache**: Fresh data with stale-while-revalidate fallback
- **In-Memory Caching**: Reduces API calls by ~80%

## ⏰ Notification Schedule

| Notification | Schedule | Active Time |
|-------------|----------|-------------|
| Morning | Once daily | 07:15 (weekdays) |
| Stock Favorites Alert | Every 2 minutes | 08:00-16:00 (weekdays) |
| Crypto Favorites Alert | Every 2 minutes | 24/7 |
| Crypto Signals | Every 5 minutes | 24/7 |
| Crypto TP/SL | Every 2 minutes | 24/7 |
| BSJP | Once daily | 15:00 (weekdays) |
| Price Alerts | Every 1 minute | 08:00-16:00 (weekdays) |

## 📈 Sample Output

### Crypto Signal Notification
```
━━━━━━━━━━━━━━━━━━━━━━━━━━
🟢 *₿ CRYPTO SIGNAL - BUY RECOMMENDATION*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *Bitcoin (BTC)*
💸 Gain: -1.0% → +6.0% ⚡️

🤖 CRYPTO SIGNAL DETECTED
📅 Detected: 25 Apr 2026 at 10:30 WIB 🔍

📈 Chart Pattern: UPTREND
📊 Reliability: 72%
📊 Leverage: 5x
💰 Entry: $82,000 | Rp 1,312,000,000
📐 ATR14: $400 (0.5% vol)
🛡️ SL: $80,000 (+1.0%)

━━━ ₿ MARKET SNAPSHOT ━━━
₿ BTC Dominance: 58.3%
😱 Fear & Greed: 45 - 🔴 Fear

━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 25 Apr 2026 at 10:30 WIB
₿ IDX Crypto Bot
```

### TP/SL Notifications
```
🏆 *₿ TP1 LOCKED! +2%*
💰 *₿ TP2 LOCKED! +4%*
💰 *₿ TP3 ACHIEVED!*
🔴 *₿ CUT LOSS - SL HIT!*
```

## 📊 Signal Quality

| Quality | Badge | Score | Description |
|---------|-------|-------|-------------|
| STRONG | ⭐ | ≥60 | 4+ indicator confirmations |
| MODERATE | ✨ | 45-59 | 3 indicator confirmations |
| WEAK | 💫 | 35-44 | 2 indicator confirmations |
| EARLY | 🟡 | 25-34 | 1 indicator confirmation (early signal) |

**Minimum BUY/SELL threshold: 35**

## 📐 Indicator Scoring (Weighted)

### Stocks (IDX)

| Indicator | Weight | BUY Signal | SELL Signal |
|-----------|--------|------------|-------------|
| RSI | 25% | <30 oversold | >70 overbought |
| MA Crossover | 20% | Golden cross | Death cross |
| MACD | 25% | MACD > Signal + hist (+) | MACD < Signal + hist (-) |
| Bollinger Bands | 15% | Near lower band | Near upper band |
| Volume | 15% | Vol spike + price up | Vol spike + price down |

### Crypto

| Indicator | Weight | BUY Signal | SELL Signal |
|-----------|--------|------------|-------------|
| RSI | 15% | <35 oversold | >70 overbought |
| MA Crossover | 10% | Golden cross | Death cross |
| MACD | 15% | MACD > Signal + hist (+) | MACD < Signal + hist (-) |
| Bollinger Bands | 10% | Near lower band | Near upper band |
| Stochastic | 10% | %K < 20 oversold | %K > 80 overbought |
| VWAP | 10% | Price > VWAP | Price < VWAP |
| ADX | 10% | ADX > 25 + +DI > -DI | ADX > 25 + -DI > +DI |
| Ichimoku | 10% | Tenkan > Kijun + cloud above | Tenkan < Kijun + cloud below |
| Volume + Momentum | 10% | Vol spike + momentum | Vol spike + downtrend |

## ⚠️ Disclaimer

This bot is only an analysis tool. Trading decisions are your own responsibility. Always do your research before trading.

## 📝 License

MIT License
