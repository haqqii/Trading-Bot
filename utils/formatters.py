"""
Message formatters for Telegram bot.
"""
from datetime import datetime
from typing import Dict, List, Any, Tuple


# Separators
SEP = "═" * 35
SEP40 = "═" * 40

# Timeframes
TIMEFRAMES = {
    '1': {'name': '1 Menit', 'interval': '1m', 'period': '1d'},
    '5': {'name': '5 Menit', 'interval': '5m', 'period': '5d'},
    '15': {'name': '15 Menit', 'interval': '15m', 'period': '5d'},
    '60': {'name': '1 Jam', 'interval': '1h', 'period': '1mo'},
}


def format_signal_msg(signals: List[Tuple], tf: str = '5') -> str:
    """Format stock signals for display"""
    tf_name = TIMEFRAMES[tf]['name']
    msg = f"🎯 *SINYAL TRADING {tf_name}*\n"
    msg += "═" * 40 + "\n"
    msg += f"📅 {datetime.now().strftime('%d %b %Y • %H:%M')}\n\n"

    buy = [s for s in signals if s[3]['signal'] == 'BUY'][:10]
    sell = [s for s in signals if s[3]['signal'] == 'SELL'][:10]

    if buy:
        msg += f"🟢 *SINYAL BELI* ({len(buy)} saham)\n"
        msg += "─" * 40 + "\n"
        for t, n, d, s in buy:
            rsi = s.get('rsi', 50)
            macd_hist = s.get('macd_hist', 0)
            vol_ratio = s.get('volume_ratio', 1.0)
            score = s.get('buy_score', 0)
            quality = s.get('quality', 'WEAK')
            quality_emoji = "⭐" if quality == 'STRONG' else "✨" if quality == 'MODERATE' else "💫"

            msg += f"┌ {t} - {n} {quality_emoji}\n"
            msg += f"│ 💰 Entry: Rp {s['entry']:,.0f}\n"
            if s.get('tp1'):
                tp1_pct = ((s['tp1'] - s['entry']) / s['entry']) * 100
                tp2_pct = ((s['tp2'] - s['entry']) / s['entry']) * 100
                tp3_pct = ((s['tp3'] - s['entry']) / s['entry']) * 100
                msg += f"│ 🎯 TP1: Rp {s['tp1']:,.0f} (+{tp1_pct:.1f}%)\n"
                msg += f"│ 🎯 TP2: Rp {s['tp2']:,.0f} (+{tp2_pct:.1f}%)\n"
                msg += f"│ 🎯 TP3: Rp {s['tp3']:,.0f} (+{tp3_pct:.1f}%)\n"
            sl_pct = ((s['entry'] - s['sl']) / s['entry']) * 100
            msg += f"│ 🛡️ SL: Rp {s['sl']:,.0f} (-{sl_pct:.1f}%)\n"
            msg += f"│ 📊 RSI: {rsi:.0f} | MACD: {macd_hist:+.4f} | Vol: {vol_ratio:.1f}x\n"
            msg += f"│ 🎯 Score: {score}pts | Quality: {quality}\n└\n"

    if sell:
        msg += f"🔴 *SINYAL JUAL* ({len(sell)} saham)\n"
        msg += "─" * 40 + "\n"
        for t, n, d, s in sell:
            rsi = s.get('rsi', 50)
            macd_hist = s.get('macd_hist', 0)
            vol_ratio = s.get('volume_ratio', 1.0)
            score = s.get('sell_score', 0)
            quality = s.get('quality', 'WEAK')
            quality_emoji = "⭐" if quality == 'STRONG' else "✨" if quality == 'MODERATE' else "💫"

            msg += f"┌ {t} - {n} {quality_emoji}\n"
            msg += f"│ 💰 Entry: Rp {s['entry']:,.0f}\n"
            sl_pct = ((s['sl'] - s['entry']) / s['entry']) * 100
            msg += f"│ 🛡️ SL: Rp {s['sl']:,.0f} (+{sl_pct:.1f}%)\n"
            msg += f"│ 📊 RSI: {rsi:.0f} | MACD: {macd_hist:+.4f} | Vol: {vol_ratio:.1f}x\n"
            msg += f"│ 🎯 Score: {score}pts | Quality: {quality}\n└\n"

    if not buy and not sell:
        msg += "⚪ *TIDAK ADA SINYAL KUAT*\n\n"
        msg += "Sedang dalam fase konsolidasi.\n"
        msg += "Tunggu momen selanjutnya...\n"

    msg += "\n" + "═" * 40 + "\n"
    msg += "*⚠️ Trading risiko tanggung sendiri*\n"
    msg += "*⭐ STRONG | ✨ MODERATE | 💫 WEAK*"

    return msg


def format_crypto_msg(signals: List) -> str:
    """Format crypto signals for display"""
    if not signals:
        return f"""₿ *CRYPTO SIGNALS*

{SEP}

📊 Tidak ada sinyal saat ini.

💡 Sinyal membutuhkan konfirmasi dari:
• RSI + MACD + Stochastic + ADX + VWAP + Ichimoku

⏰ {datetime.now().strftime('%d %b %Y • %H:%M')}

⚠️ Crypto volatil, risiko tinggi"""

    msg = "₿ *CRYPTO SIGNALS*\n"
    msg += "═" * 40 + "\n"
    msg += f"📅 {datetime.now().strftime('%d %b %Y • %H:%M')}\n"
    msg += f"📊 Monitoring: {len(signals)} crypto\n\n"

    buy = [s for s in signals if s[2]['signal'] == 'BUY'][:10]
    sell = [s for s in signals if s[2]['signal'] == 'SELL'][:10]

    if buy:
        msg += f"🟢 *SINYAL BELI* ({len(buy)} crypto)\n"
        msg += "─" * 40 + "\n"
        for t, n, s, _ in buy:
            rsi = s.get('rsi', 50)
            macd_hist = s.get('macd_hist', 0)
            vol_ratio = s.get('volume_ratio', 1.0)
            score = s.get('buy_score', 0)
            quality = s.get('quality', 'WEAK')
            stoch_k = s.get('stoch_k', 50)
            adx = s.get('adx', 25)
            quality_emoji = "⭐" if quality == 'STRONG' else "✨" if quality == 'MODERATE' else "💫"

            msg += f"┌ {t} - {n} {quality_emoji}\n"
            msg += f"│ 💰 Entry: ${s['entry']:,.2f}\n"
            if s.get('tp1'):
                msg += f"│ 🎯 TP1: ${s['tp1']:,.2f}\n"
                msg += f"│ 🎯 TP2: ${s['tp2']:,.2f}\n"
                msg += f"│ 🎯 TP3: ${s['tp3']:,.2f}\n"
            msg += f"│ 🛡️ SL: ${s['sl']:,.2f}\n"
            msg += f"│ 📊 RSI: {rsi:.0f} | Stoch: {stoch_k:.0f} | ADX: {adx:.0f}\n"
            msg += f"│ 🎯 Score: {score}pts\n└\n"

    if sell:
        msg += f"🔴 *SINYAL JUAL* ({len(sell)} crypto)\n"
        msg += "─" * 40 + "\n"
        for t, n, s, _ in sell:
            rsi = s.get('rsi', 50)
            macd_hist = s.get('macd_hist', 0)
            vol_ratio = s.get('volume_ratio', 1.0)
            score = s.get('sell_score', 0)
            quality = s.get('quality', 'WEAK')
            quality_emoji = "⭐" if quality == 'STRONG' else "✨" if quality == 'MODERATE' else "💫"

            msg += f"┌ {t} - {n} {quality_emoji}\n"
            msg += f"│ 💰 Entry: ${s['entry']:,.2f}\n"
            msg += f"│ 🛡️ SL: ${s['sl']:,.2f}\n"
            msg += f"│ 📊 RSI: {rsi:.0f} | MACD: {macd_hist:+.4f} | Vol: {vol_ratio:.1f}x\n"
            msg += f"│ 🎯 Score: {score}pts\n└\n"

    if not buy and not sell:
        msg += "⚪ *TIDAK ADA SINYAL KUAT*\n\n"
        msg += "Sedang dalam fase konsolidasi.\n"
        msg += "Tunggu momen selanjutnya...\n\n"

    msg += "═" * 40 + "\n"
    msg += "*⚠️ Crypto volatil, risiko tinggi*\n"
    msg += "*⭐ STRONG | ✨ MODERATE | 💫 WEAK*"

    return msg


def format_bsjp_msg(signals: List) -> str:
    """Format BSJP signals for display"""
    if not signals:
        return f"""🌙 *BSJP (Beli Sore Jual Pagi)*

{SEP}

📊 Saat ini belum ada sinyal BSJP kuat.

💡 Sinyal BSJP muncul jam 14:00-15:00
   saat market akan close.

⚠️ Trading risiko tanggung sendiri"""

    msg = f"""🌙 *BSJP - BELI SORE JUAL PAGI*

{SEP}
📅 {datetime.now().strftime('%d %b %Y • %H:%M')}

🎯 Rekomendasi untuk besok pagi:

"""

    for i, s in enumerate(signals[:15], 1):
        msg += f"""{i}. *{s['ticker']}* - {s['name']}
   💵 Harga: Rp {s['price']:,.0f}
   📈 Perubahan: {s['change']:+.1f}%
   📊 RSI: {s['rsi']:.0f}
   ✅ Skor: {s['score']}/4
   🎯 TP: Rp {s['tp']:,.0f} (+2%)
   🛡️ SL: Rp {s['sl']:,.0f} (-1.5%)

"""

    msg += f"""{SEP}

📝 *Strategi BSJP:*
• Beli sore (sebelum close)
• Jual besok pagi (setelah open)
• Target +2%, SL -1.5%

⚠️ Trading risiko tanggung sendiri"""

    return msg


def format_morning_msg(signals: List) -> str:
    """Format morning watchlist signals"""
    if not signals:
        return """☀️ *REKOMENDASI PAGI*

━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Belum ada sinyal kuat untuk pagi ini.

💡 Sinyal terbaik muncul setelah
   data market kemarin tersedia.

⚠️ Trading risiko tanggung sendiri"""

    msg = f"""☀️ *REKOMENDASI PAGI*

━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 {datetime.now().strftime('%d %b %Y')}

🎯 Saham Potensi Naik Hari Ini:

"""

    for i, s in enumerate(signals[:10], 1):
        msg += f"""{i}. *{s['ticker']}* - {s['name']}
   💰 Harga: Rp {s['price']:,.0f}
   📈 Perubahan: {s['change']:+.1f}%
   📊 RSI: {s['rsi']:.0f}
   🎯 Target: {s['tp']:,.0f} (+3%)
   🛡️ SL: {s['sl']:,.0f} (-2%)

"""

    msg += """━━━━━━━━━━━━━━━━━━━━━━━━━━

📝 *Strategi:*
• Beli pagi setelah open (09:00-10:00)
• Target +3%, SL -2%
• Take profit bertahap

⚠️ Trading risiko tanggung sendiri"""

    return msg


def format_indicator_msg(ticker: str, name: str, indicators: Dict, price: str) -> str:
    """Format technical indicators for display"""
    em = {'ABOVE': '🟢', 'BELOW': '🔴', 'OVERBOUGHT': '🔴', 'OVERSOLD': '🟢',
          'NEUTRAL': '🟡', 'STRONG_TREND': '🟢', 'WEAK_TREND': '🟡',
          'VOLUME_SPIKE': '🔥', 'HIGH_VOLUME': '📈', 'LOW_VOLUME': '📉', 'NORMAL': '⚪',
          'POSITIVE': '🟢', 'NEGATIVE': '🔴',
          'UPPER_HALF': '🟢', 'LOWER_HALF': '🔴'}

    def e(key): return em.get(indicators.get(key, 'NEUTRAL'), '⚪')
    def f(val, fmt='.2f'): return f"{val:{fmt}}" if isinstance(val, (int, float)) else str(val)

    msg = f"📈 *INDIKATOR TEKNIS - {ticker}*\n"
    msg += "━" * 35 + "\n"
    msg += f"📌 *{name}*\n"
    msg += f"💰 Harga: {price}\n\n"

    # MACD
    macd_dir = '🟢' if indicators.get('macd_histogram') == 'POSITIVE' else '🔴'
    msg += f"📊 *MACD*\n"
    msg += f"   MACD Line: {f(indicators['macd']['value'])}\n"
    msg += f"   Signal: {f(indicators['macd']['signal'])}\n"
    msg += f"   Histogram: {macd_dir} {indicators['macd_histogram']}\n\n"

    # Bollinger Bands
    bb = indicators['bb']
    msg += f"📊 *BOLLINGER BANDS*\n"
    msg += f"   Upper: {f(bb['upper'])}\n"
    msg += f"   Middle: {f(bb['middle'])}\n"
    msg += f"   Lower: {f(bb['lower'])}\n"
    msg += f"   Posisi: {e('bb_position')} {indicators['bb_position']}\n\n"

    # RSI
    msg += f"📊 *RSI*\n"
    msg += f"   Value: {f(indicators['rsi'])}\n"
    if indicators['rsi'] > 70:
        msg += f"   Signal: 🔴 Overbought\n\n"
    elif indicators['rsi'] < 30:
        msg += f"   Signal: 🟢 Oversold\n\n"
    else:
        msg += f"   Signal: 🟡 Neutral\n\n"

    # ATR
    msg += f"📊 *ATR*\n"
    msg += f"   ATR: {f(indicators['atr'])} ({f(indicators['atr_pct'])}%)\n\n"

    # Volume
    msg += f"📊 *VOLUME*\n"
    msg += f"   Sekarang: {f(indicators['volume_now'], ',.0f')}\n"
    msg += f"   Rata-rata: {f(indicators['volume_avg'], ',.0f')}\n"
    msg += f"   Ratio: {f(indicators['volume_ratio'])}x {e('volume_signal')}\n"

    return msg


def format_price_dual(price: float, rate: float = 16000) -> Tuple[str, str]:
    """Format price in both USD and IDR"""
    if not price or price <= 0:
        return "N/A", "N/A"
    usd_str = f"${price:,.2f}"
    idr_str = f"Rp {price * rate:,.0f}"
    return usd_str, idr_str


# === UNIFIED NOTIFICATION FORMATTERS ===

# Notification type headers
NOTIF_HEADERS = {
    'BUY': '🟢 *₿ CRYPTO SIGNAL - REKOMENDASI BELI*',
    'REVERSAL': '🔄 *₿ REVERSAL CANDIDATE - OVERSOLD REBOUND*',
    'TP1': '🏆 *₿ TP1 TERKUNCI! +2%*',
    'TP2': '🎯 *₿ TP2 TERKUNCI! +4%*',
    'TP3': '💰 *₿ TP3 TERCAPAI!*',
    'SL': '🔴 *₿ CUT LOSS - SL TERKENA!*',
    'ALERT_BUY': '🟢 *ALERT BUY!*',
    'ALERT_SELL': '🔴 *ALERT SELL!*',
    'SCAN': '📊 *CRYPTO SCAN*',
}


def _fg_emoji(value: int) -> str:
    """Fear & Greed emoji"""
    if value >= 75: return '🟢 Ext Greed'
    if value >= 55: return '🟢 Greed'
    if value >= 45: return '🟡 Netral'
    if value >= 25: return '🔴 Fear'
    return '🔴 Ext Fear'


def _funding_emoji(rate: float) -> str:
    """Funding rate emoji"""
    if rate is None: return '?'
    if rate > 0.0005: return '🔴 Tinggi'
    if rate > 0.0001: return '🟡 Sedang'
    if rate < -0.0005: return '🟢 Sangat Rendah'
    if rate < -0.0001: return '🟢 Rendah'
    return '🟡 Netral'


def _fmt_price_dual(price: float, rate: float = 16000) -> str:
    """Format price in USD | IDR format"""
    if not price or price <= 0:
        return "N/A | N/A"
    return f"${price:,.2f} | Rp {price * rate:,.0f}"


def format_unified_crypto_notification(
    notif_type: str,
    ticker: str,
    name: str,
    entry: float,
    current_price: float,
    tp1: float,
    tp2: float,
    tp3: float,
    sl: float,
    analysis_data: Dict = None,
    market_data: Dict = None,
    change_pct: float = 0,
    profit_loss: float = 0,
    usd_idr_rate: float = 16000
) -> str:
    """
    Build rich unified notification message for crypto.

    Args:
        notif_type: 'BUY' | 'TP1' | 'TP2' | 'TP3' | 'SL' | 'ALERT_BUY' | 'ALERT_SELL'
        ticker: Symbol (e.g., 'BTC-USD')
        name: Full name (e.g., 'Bitcoin')
        entry: Entry price
        current_price: Current price
        tp1/tp2/tp3: Take profit levels
        sl: Stop loss level
        analysis_data: Technical analysis data
        market_data: Market snapshot data
        change_pct: Price change percentage
        profit_loss: Profit/loss percentage
        usd_idr_rate: USD to IDR conversion rate
    """
    analysis_data = analysis_data or {}
    market_data = market_data or {}

    # Get header - fallback to 'SCAN' if not recognized
    header = NOTIF_HEADERS.get(notif_type, NOTIF_HEADERS.get('SCAN', '📊 CRYPTO SIGNAL'))

    # Skip if entry is invalid
    if not entry or entry <= 0:
        return f"⚠️ Invalid data for {name} ({ticker})"

    sym = ticker.replace('-USD', '').replace('-USDT', '')
    ts = datetime.now().strftime('%d %b %Y pukul %H:%M WIB')

    # Calculate gain display
    ind = analysis_data.get('indicators') or {}
    atr_val = ind.get('atr', 0) if ind else 0

    if entry > 0 and atr_val > 0:
        tp3_pct = ((tp3 - entry) / entry) * 100 if tp3 else 0
        sl_pct = ((sl - entry) / entry) * 100
        gain_str = f"{sl_pct:+.1f}% → {tp3_pct:+.1f}% ⚡️"
    else:
        if notif_type == 'BUY':
            gain_str = '+2% → +6% ⚡️'
        elif notif_type in ('SL',):
            gain_str = f'{profit_loss:.2f}% 🔴'
        else:
            gain_str = f'+{profit_loss:.2f}% ⚡️'

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(header)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"📌 *{name} ({sym})*")
    lines.append(f"💸 Gain: {gain_str}")
    lines.append("")

    # Special section for REVERSAL signals
    if notif_type == 'REVERSAL':
        lines.append("⚠️ *REVERSAL CANDIDATE*")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
        reversal_reasons = analysis_data.get('reversal_reasons', [])
        if reversal_reasons:
            for reason in reversal_reasons:
                lines.append(f"   ✅ {reason}")
        lines.append("")
        lines.append("💡 RSI oversold + momentum rising")
        lines.append("   Entry di support, potensi rebound")
        lines.append("")

    # AI call info
    lines.append("🤖 *CRYPTO SIGNAL DETECTED*")
    lines.append(f"📅 Detected: {ts} 🔍")
    lines.append("")

    # Chart Pattern
    pat = analysis_data.get('pattern')
    if pat:
        lines.append(f"📈 Chart Pattern: {pat.get('type', 'N/A').replace('_', ' ')}")
        lines.append(f"📊 Reliability: {pat.get('reliability', 0)}%")
    else:
        lines.append("📈 Chart Pattern: None detected")

    # Display detected chart patterns
    detected_patterns = analysis_data.get('patterns', [])
    if detected_patterns:
        lines.append("")
        lines.append("📐 *Chart Patterns Detected:*")
        for p in detected_patterns:
            strength_bar = "█" * int(p.get('strength', 0) * 5)
            lines.append(f"   🔹 {p.get('name', 'Unknown')} [{strength_bar}]")
            if p.get('description'):
                lines.append(f"      {p.get('description')}")

    # Leverage
    lev = analysis_data.get('leverage')
    if lev:
        lines.append(f"📊 Leverage: {lev}x")

    # Entry / TP / SL
    sl_display = sl
    sl_pct_dynamic = ((sl - entry) / entry) * 100 if entry > 0 and atr_val > 0 else -3
    sl_label = f"*({sl_pct_dynamic:+.1f}%)*"

    if notif_type == 'TP1':
        sl_display = entry
        sl_label = "*(BE)*"
    elif notif_type == 'TP2':
        sl_display = tp1
        sl_label = "*(TP1)*"

    lines.append(f"💰 Entry: {_fmt_price_dual(entry, usd_idr_rate)}")
    if atr_val > 0 and entry > 0:
        lines.append(f"📐 ATR14: ${atr_val:.2f} ({(atr_val/entry)*100:.1f}% vol)")
    lines.append(f"🛡️ SL: {_fmt_price_dual(sl_display, usd_idr_rate)} {sl_label}")

    # Remaining TP levels
    remaining = []
    if notif_type == 'BUY':
        remaining = [
            ('TP1', tp1, ((tp1 - entry) / entry) * 100 if entry > 0 else 2),
            ('TP2', tp2, ((tp2 - entry) / entry) * 100 if entry > 0 else 4),
            ('TP3', tp3, ((tp3 - entry) / entry) * 100 if entry > 0 else 6),
        ]
    elif notif_type == 'TP1':
        remaining = [
            ('TP2', tp2, ((tp2 - entry) / entry) * 100 if entry > 0 else 4),
            ('TP3', tp3, ((tp3 - entry) / entry) * 100 if entry > 0 else 6),
        ]
    elif notif_type == 'TP2':
        remaining = [
            ('TP3', tp3, ((tp3 - entry) / entry) * 100 if entry > 0 else 6),
        ]

    for tp_name, tp_val, tp_pct in remaining:
        lines.append(f"🎯 {tp_name}: {_fmt_price_dual(tp_val, usd_idr_rate)} *({tp_pct:+.1f}%)*")

    # Support & Resistance levels
    sr = analysis_data.get('sr', {})
    if sr and notif_type == 'BUY':
        lines.append("")
        lines.append("📐 *Support & Resistance:*")
        if sr.get('nearest_support'):
            sup = sr['nearest_support']['level']
            lines.append(f"  🟢 Support: {_fmt_price_dual(sup, usd_idr_rate)}")
        if sr.get('nearest_resistance'):
            res = sr['nearest_resistance']['level']
            lines.append(f"  🔴 Resistance: {_fmt_price_dual(res, usd_idr_rate)}")
        # Show distance
        sr_dist = sr.get('sr_distance', {})
        if sr_dist.get('support_pct'):
            lines.append(f"  📏 Jarak: {sr_dist['support_pct']:.1f}% ke support")

    if notif_type in ('TP1', 'TP2', 'TP3', 'SL'):
        lines.append(f"💵 Current: {_fmt_price_dual(current_price, usd_idr_rate)}")

    lines.append("")

    # Market Snapshot section
    if market_data:
        lines.append("━━━ ₿ MARKET SNAPSHOT ━━━")

        if market_data.get('btc_dominance'):
            lines.append(f"₿ BTC Dominance: {market_data['btc_dominance']:.1f}%")

        fg = market_data.get('fear_greed')
        if fg:
            lines.append(f"😱 Fear & Greed: {fg['value']} - {_fg_emoji(fg['value'])}")

        fr = market_data.get('funding_rate')
        if fr is not None:
            lines.append(f"⚡ Funding: {fr*100:.4f}% {_funding_emoji(fr)}")

        lsr = market_data.get('long_short_ratio')
        if lsr is not None:
            ls_emoji = '🟢' if lsr > 1 else '🔴'
            lines.append(f"📊 L/S Ratio: {lsr} {ls_emoji}")

        ns = market_data.get('news_sentiment')
        if ns:
            lines.append(f"📰 News: {ns}")

        lines.append("")

    # AI Insight
    ai = analysis_data.get('ai_insight')
    if ai:
        lines.append("━━━ 🤖 CRYPTO AI INSIGHT ━━━")
        lines.append(f"💡 {ai}")
        lines.append("")

    # Final line
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {ts}")
    lines.append("₿ IDX Crypto Bot")

    return '\n'.join(lines)


def format_unified_stock_notification(
    notif_type: str,
    ticker: str,
    name: str,
    entry: float,
    current_price: float,
    tp1: float,
    tp2: float,
    tp3: float,
    sl: float,
    analysis_data: Dict = None,
    change_pct: float = 0,
    profit_loss: float = 0,
    entry_low: float = 0,
    entry_high: float = 0
) -> str:
    """
    Build rich unified notification for stock signals.
    """
    analysis_data = analysis_data or {}

    # Get header - match crypto format with dynamic type
    if notif_type == 'BUY':
        header = "🟢 *SAHAM SIGNAL - REKOMENDASI BELI*"
    elif notif_type == 'TP1':
        header = "🎯 *SAHAM TP1 TERCAPAI!*"
    elif notif_type == 'TP2':
        header = "🎯 *SAHAM TP2 TERCAPAI!*"
    elif notif_type == 'TP3':
        header = "🎯 *SAHAM TP3 TERCAPAI!*"
    elif notif_type == 'SL':
        header = "🛡️ *SAHAM SL TERCAPAI!*"
    else:
        header = f"📊 *SAHAM SIGNAL - {notif_type}*"

    ts = datetime.now().strftime('%d %b %Y pukul %H:%M WIB')

    # Gain display
    ind = analysis_data.get('indicators') or {}
    atr_val = ind.get('atr', 0) if ind else 0

    if entry > 0 and atr_val > 0:
        tp3_pct = ((tp3 - entry) / entry) * 100 if tp3 else 0
        sl_pct = ((sl - entry) / entry) * 100
        gain_str = f"{sl_pct:+.1f}% → {tp3_pct:+.1f}% ⚡️"
    else:
        gain_str = '+1% → +3% ⚡️'

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(header)
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(f"📌 *{name} ({ticker})*")
    lines.append(f"💸 Gain: {gain_str}")
    lines.append("")

    lines.append("🤖 *SAHAM SIGNAL DETECTED*")
    lines.append(f"📅 Detected: {ts}")
    lines.append("")

    # Chart Pattern & Reliability
    pattern = analysis_data.get('pattern', {})
    chart_pattern = pattern.get('type', 'NEUTRAL')
    reliability = pattern.get('reliability', 50)
    score = analysis_data.get('score', 0)
    quality = analysis_data.get('quality', 'WEAK')
    rsi = analysis_data.get('rsi', 0)
    reasons = analysis_data.get('reasons', [])
    change = analysis_data.get('change', 0)
    volume_ratio = analysis_data.get('volume_ratio', 1)

    if chart_pattern == 'UPTREND':
        pattern_emoji = "📈"
    elif chart_pattern == 'DOWNTREND':
        pattern_emoji = "📉"
    elif chart_pattern == 'BREAKOUT':
        pattern_emoji = "🚀"
    elif chart_pattern == 'PULLBACK':
        pattern_emoji = "📊"
    else:
        pattern_emoji = "➡️"

    lines.append(f"{pattern_emoji} Chart Pattern: {chart_pattern}")
    lines.append(f"📊 Reliability: {reliability}%")

    # Display detected chart patterns
    detected_patterns = analysis_data.get('patterns', [])
    if detected_patterns:
        lines.append("")
        lines.append("📐 *Chart Patterns Detected:*")
        for p in detected_patterns:
            strength_bar = "█" * int(p.get('strength', 0) * 5)
            lines.append(f"   🔹 {p.get('name', 'Unknown')} [{strength_bar}]")
            if p.get('description'):
                lines.append(f"      {p.get('description')}")

    lines.append(f"📊 RSI: {rsi:.0f} | Score: {score} ({quality})")
    lines.append(f"📊 Change: {change:+.1f}% | Vol: {volume_ratio:.1f}x")
    lines.append("")

    # Entry / TP / SL
    if entry_low > 0 and entry_high > 0:
        lines.append(f"💰 Entry: Rp {entry_low:,.0f} - {entry_high:,.0f}")
    else:
        lines.append(f"💰 Entry: Rp {entry:,.0f}")
    if atr_val > 0 and entry > 0:
        lines.append(f"📐 ATR14: Rp {atr_val:,.0f} ({(atr_val/entry)*100:.1f}% vol)")

    sl_display = sl
    sl_pct = ((sl - entry) / entry) * 100 if entry > 0 and atr_val > 0 else -1.5
    sl_label = f"*({sl_pct:+.1f}%)*"
    if notif_type == 'TP1':
        sl_display = entry
        sl_label = "*(BE)*"
    elif notif_type == 'TP2':
        sl_display = tp1
        sl_label = "*(TP1)*"
    lines.append(f"🛡️ SL: Rp {sl_display:,.0f} {sl_label}")

    # Remaining TP levels
    remaining = []
    if notif_type == 'BUY':
        remaining = [
            ('TP1', tp1, ((tp1 - entry) / entry) * 100 if entry > 0 else 1),
            ('TP2', tp2, ((tp2 - entry) / entry) * 100 if entry > 0 else 2),
            ('TP3', tp3, ((tp3 - entry) / entry) * 100 if entry > 0 else 3),
        ]
    elif notif_type == 'TP1':
        remaining = [
            ('TP2', tp2, ((tp2 - entry) / entry) * 100 if entry > 0 else 2),
            ('TP3', tp3, ((tp3 - entry) / entry) * 100 if entry > 0 else 3),
        ]
    elif notif_type == 'TP2':
        remaining = [
            ('TP3', tp3, ((tp3 - entry) / entry) * 100 if entry > 0 else 3),
        ]

    for tp_name, tp_val, tp_pct in remaining:
        lines.append(f"🎯 {tp_name}: Rp {tp_val:,.0f} *({tp_pct:+.1f}%)*")

    # Alasan penguatan (hanya untuk BUY)
    if notif_type == 'BUY' and reasons:
        lines.append("")
        lines.append("📋 *Alasan:*")
        for reason in reasons[:5]:
            lines.append(f"  ✅ {reason}")

    # Support & Resistance levels
    sr = analysis_data.get('sr', {})
    if sr and notif_type == 'BUY':
        lines.append("")
        lines.append("📐 *Support & Resistance:*")
        if sr.get('nearest_support'):
            sup = sr['nearest_support']['level']
            lines.append(f"  🟢 Support: Rp {sup:,.0f}")
        if sr.get('nearest_resistance'):
            res = sr['nearest_resistance']['level']
            lines.append(f"  🔴 Resistance: Rp {res:,.0f}")
        # Show distance
        sr_dist = sr.get('sr_distance', {})
        if sr_dist.get('support_pct'):
            lines.append(f"  📏 Jarak: {sr_dist['support_pct']:.1f}% ke support")

    if notif_type in ('TP1', 'TP2', 'TP3', 'SL'):
        lines.append(f"💵 Current: Rp {current_price:,.0f}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {ts}")
    lines.append("📊 IDX Saham Bot")

    return '\n'.join(lines)
