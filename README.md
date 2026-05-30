# 🤖 IDX Stock & Crypto Signal Bot

Telegram bot for getting Indonesian stock and crypto trading signals based on technical analysis.

## 📁 Project Structure

```
Bot Saham 2/
├── main.py                 # Entry point
├── config/                 # Configuration
│   ├── settings.py         # Settings & TIMEFRAMES
│   └── __init__.py
├── handlers/               # Command handlers
│   ├── command_handlers.py # All command handlers
│   ├── scheduler.py        # Background jobs
│   └── __init__.py
├── services/               # Business logic
│   ├── stock_service.py    # Stock data
│   ├── crypto_service.py    # Crypto data
│   ├── signal_service.py    # Signal generation
│   ├── chart_service.py     # Chart generation
│   └── __init__.py
├── utils/                  # Utilities
│   ├── formatters.py       # Message formatting
│   ├── cache.py            # In-memory cache
│   ├── indicators.py       # Technical indicators
│   ├── rate_limiter.py     # API rate limiting
│   └── __init__.py
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

### Crypto (24/7)
- **₿ Crypto Signals** - Signals for 250+ crypto pairs
- **💰 Dual Currency** - Price display in USD and IDR
- **📈 Price Alert** - Real-time notification for crypto price movements
- **🎯 TP/SL Tracking** - Auto notification when targets are reached (TP1, TP2, TP3, SL)

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
- **Weighted Scoring** - Combination of all indicators with weights
- **Interactive Charts** - Price chart with RSI, MACD, Volume (via `/chart`)

### Notifications
- **Stock Notifications** - TP/SL hit (09:00-15:00)
- **Favorites Notifications** - Alert if price changes ≥1% (stocks & crypto)
- **Morning Notifications** - Stock signals before market open (07:15)
- **Crypto Notifications** - Active 24/7
- **BSJP Notifications** - Auto at 15:00 (before market close)

## 🚀 Installation

1. **Clone or download this repository**

2. **Install dependencies:**
```bash
pip install -r requirements.txt
```

3. **Get Bot Token:**
   - Open Telegram
   - Search for @BotFather
   - Type `/newbot`
   - Follow instructions and save your bot token

4. **Create `.env` file:**
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

## ▶️ Running

```bash
python main.py
```

or click `start.bat` (Windows)

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

## 📊 Data Source

Bot uses multiple data sources as fallback:

1. **Yahoo Finance** - Primary for stocks and crypto
2. **TradingView** - Fallback for stock data
3. **Finnhub** - Last fallback (free registration at https://finnhub.io)
4. **CoinGecko** - Fallback for crypto data and USD/IDR exchange rate

### API Key Setup

Add API key in `.env` file:
```bash
FINNHUB_API_KEY=your_finnhub_api_key
```

### API Protection
- **Rate Limiting**: Request limit per minute
- **Circuit Breaker**: Auto-reset every 15 seconds on API error
- **Exponential Backoff**: Retry with increasing delay
- **In-Memory Caching**: Cache data to reduce API calls

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
