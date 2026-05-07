# -*- coding: utf-8 -*-
"""Update the analisa_cmd format to match user's requirements"""

import re

# Read the file
with open('handlers/command_handlers.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Check current state
print(f"File length: {len(content)} chars")

# Pattern for crypto section (first occurrence)
crypto_pattern = r'# Build message with clean format\s+bb_pos'
crypto_matches = list(re.finditer(crypto_pattern, content))

if crypto_matches:
    print(f"Found {len(crypto_matches)} matches for crypto pattern")

    # Process CRYPTO section (first match)
    crypto_start = crypto_matches[0].start()

    # Find the else: that leads to stock analysis
    else_pattern = r'\n\s+else:\s*\n\s+# Stock analysis'
    else_match = re.search(else_pattern, content[crypto_start:])

    if else_match:
        crypto_end = crypto_start + else_match.start()

        # New crypto section
        new_crypto = '''            # Build message with comprehensive format
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            upside = (bb_upper - price) / price * 100
            downside = (price - bb_lower) / price * 100

            # Conflict detection
            has_conflict = (rsi < 35 and not ma_golden) or (rsi > 65 and ma_golden)

            msg = f"""● Analisis {name.upper()} ({ticker})

┌──────────────────────────────────────────────────────┐
│                    📊 OVERVIEW                       │
├──────────────────────────────────────────────────────┤
│  Harga      : ${price:,.2f} ({price_idr:,.0f} IDR)  │
│  Change     : {change:+.2f}%                        │
│  RSI        : {rsi:.1f} {rsi_status}              │
│  Candles    : {candles}                            │
└──────────────────────────────────────────────────────┘

Indikator Teknikal

┌──────────────┬──────────────┬──────────────────────┐
│  Indikator   │ Value        │ Status               │
├──────────────┼──────────────┼──────────────────────┤
│ RSI          │ {rsi:,.1f}       │ {rsi_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MA Fast      │ ${ma_fast:,.2f}    │ {"Di atas harga" if ma_fast > price else "Di bawah harga"}     │
├──────────────┼──────────────┼──────────────────────┤
│ MA Slow      │ ${ma_slow:,.2f}    │ {"Di atas harga" if ma_slow > price else "Di bawah harga"}     │
├──────────────┼──────────────┼──────────────────────┤
│ MACD         │ {macd:,.2f}       │ {macd_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MACD Hist    │ {macd_hist:,.2f}      │ {macd_hist_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ BB Lower     │ ${bb_lower:,.2f}    │ Dekat support     │
├──────────────┼──────────────┼──────────────────────┤
│ Volume Ratio │ {volume_ratio:.2f}x      │ {vol_status:<20} │
└──────────────┴──────────────┴──────────────────────┘

Signal Analysis

{signal_emoji} Signal: **{signal}** ({quality.upper()})
Buy Score: {buy_score} | Sell Score: {sell_score}

Alasan {signal}:
'''

            # Add signal reasons
            if signal == 'BUY':
                for r in bullish_reasons:
                    msg += f"  - {r}\n"
            elif signal == 'SELL':
                for r in bearish_reasons:
                    msg += f"  - {r}\n"
            else:
                if bullish_reasons:
                    msg += "🟢 Bullish: " + ", ".join(bullish_reasons[:3]) + "\n"
                if bearish_reasons:
                    msg += "🔴 Bearish: " + ", ".join(bearish_reasons[:3]) + "\n"

            # Conflict warning
            if has_conflict:
                msg += """
Tapi ada kontradiksi:
  - RSI oversold tapi MA masih bearish
  - Tunggu konfirmasi sinyal
"""

            msg += f"""
Kesimpulan

┌──────────────────────────────────────────────────────┐
│  ⚠️  KONDISI {"KONFLIK" if has_conflict else "TERKINI":<51}│
├──────────────────────────────────────────────────────┤"""

            if bearish_reasons:
                msg += """
│  📉 BEARISH:                                        │"""
                for r in bearish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            if bullish_reasons:
                msg += """
│  📈 BULLISH:                                        │"""
                for r in bullish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            msg += """
│                                                      │
└──────────────────────────────────────────────────────┘
"""

            # Add TP/SL recommendations
            if signal in ['BUY', 'SELL']:
                entry = s.get('entry', price)
                tp1 = s.get('tp1')
                tp2 = s.get('tp2')
                tp3 = s.get('tp3')
                sl = s.get('sl')

                if tp1 and sl:
                    tp1_pct = (tp1 - entry) / entry * 100
                    sl_pct = (entry - sl) / entry * 100
                    rr = tp1_pct / sl_pct if sl_pct > 0 else 0

                    msg += f"""Rekomendasi

Skenario: Agresif
Action: **{'BUY' if signal == 'BUY' else 'SELL'}**
Entry: ${entry:,.2f}
SL: ${sl:,.2f}
Target: ${tp1:,.2f}
────────────────────────────────────────────────
Skenario: Konservatif
Action: HOLD
Entry: -
SL: -
Target: Tunggu breakout di atas MA

Saran: Tunggu sampai harga breakout di atas MA
dengan volume naik untuk konfirmasi.
"""

            msg += """
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *Disclaimer:* Analisis bukan
nasihat keuangan. Trading risiko
tanggung sendiri.
"""

        else:
'''

        # Replace crypto section
        content = content[:crypto_start] + new_crypto + content[crypto_end:]
        print(f"Replaced crypto section: {crypto_start} to {crypto_end}")
    else:
        print("Could not find end of crypto section")

# Now find and replace the STOCK section (the second occurrence)
stock_matches = list(re.finditer(crypto_pattern, content))

if len(stock_matches) >= 2:
    stock_start = stock_matches[1].start()

    # Find await update.message.reply_text
    reply_pattern = r'\n\s+await update\.message\.reply_text\(msg, parse_mode=\'Markdown\'\)'
    reply_match = re.search(reply_pattern, content[stock_start:])

    if reply_match:
        stock_end = stock_start + reply_match.end()

        # New stock section
        new_stock = '''            # Build message with comprehensive format
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
            upside = (bb_upper - price) / price * 100
            downside = (price - bb_lower) / price * 100

            # Conflict detection
            has_conflict = (rsi < 35 and not ma_golden) or (rsi > 65 and ma_golden)

            # MA status text
            ma_fast_status = "Di atas harga" if ma_fast > price else "Di bawah harga"
            ma_slow_status = "Di atas harga" if ma_slow > price else "Di bawah harga"

            msg = f"""● Analisis {name.upper()} ({ticker})

┌──────────────────────────────────────────────────────┐
│                    📊 OVERVIEW                       │
├──────────────────────────────────────────────────────┤
│  Harga      : Rp {price:,.0f}                           │
│  Change     : {change:+.2f}%                        │
│  RSI        : {rsi:.1f} {rsi_status}              │
│  Candles    : {candles}                            │
└──────────────────────────────────────────────────────┘

Indikator Teknikal

┌──────────────┬──────────────┬──────────────────────┐
│  Indikator   │ Value        │ Status               │
├──────────────┼──────────────┼──────────────────────┤
│ RSI          │ {rsi:,.1f}       │ {rsi_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MA Fast      │ {ma_fast:,.2f}     │ {ma_fast_status:<17}│
├──────────────┼──────────────┼──────────────────────┤
│ MA Slow      │ {ma_slow:,.2f}     │ {ma_slow_status:<17}│
├──────────────┼──────────────┼──────────────────────┤
│ MACD         │ {macd:,.2f}       │ {macd_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ MACD Hist    │ {macd_hist:,.2f}      │ {macd_hist_status:<20} │
├──────────────┼──────────────┼──────────────────────┤
│ BB Lower     │ {bb_lower:,.2f}     │ Dekat support     │
├──────────────┼──────────────┼──────────────────────┤
│ Volume Ratio │ {volume_ratio:.2f}x      │ {vol_status:<20} │
└──────────────┴──────────────┴──────────────────────┘

Signal Analysis

{signal_emoji} Signal: **{signal}** ({quality.upper()})
Buy Score: {buy_score} | Sell Score: {sell_score}

Alasan {signal}:
'''

            # Add signal reasons
            if signal == 'BUY':
                for r in bullish_reasons:
                    msg += f"  - {r}\n"
            elif signal == 'SELL':
                for r in bearish_reasons:
                    msg += f"  - {r}\n"
            else:
                if bullish_reasons:
                    msg += "🟢 Bullish: " + ", ".join(bullish_reasons[:3]) + "\n"
                if bearish_reasons:
                    msg += "🔴 Bearish: " + ", ".join(bearish_reasons[:3]) + "\n"

            # Conflict warning
            if has_conflict:
                msg += """
Tapi ada kontradiksi:
  - RSI oversold tapi MA masih bearish
  - Tunggu konfirmasi sinyal
"""

            msg += f"""
Kesimpulan

┌──────────────────────────────────────────────────────┐
│  ⚠️  KONDISI {"KONFLIK" if has_conflict else "TERKINI":<51}│
├──────────────────────────────────────────────────────┤"""

            if bearish_reasons:
                msg += """
│  📉 BEARISH:                                        │"""
                for r in bearish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            if bullish_reasons:
                msg += """
│  📈 BULLISH:                                        │"""
                for r in bullish_reasons[:4]:
                    msg += f"\n│     - {r:<50} │"

            msg += """
│                                                      │
└──────────────────────────────────────────────────────┘
"""

            # Add TP/SL recommendations
            if signal in ['BUY', 'SELL']:
                entry = s.get('entry', price)
                tp1 = s.get('tp1')
                tp2 = s.get('tp2')
                tp3 = s.get('tp3')
                sl = s.get('sl')

                if tp1 and sl:
                    tp1_pct = (tp1 - entry) / entry * 100
                    sl_pct = (entry - sl) / entry * 100
                    rr = tp1_pct / sl_pct if sl_pct > 0 else 0

                    msg += f"""Rekomendasi

Skenario: Agresif
Action: **{'BUY' if signal == 'BUY' else 'SELL'}**
Entry: Rp {entry:,.0f}
SL: Rp {sl:,.0f}
Target: Rp {tp1:,.0f}
────────────────────────────────────────────────
Skenario: Konservatif
Action: HOLD
Entry: -
SL: -
Target: Tunggu breakout di atas MA

Saran: Tunggu sampai harga breakout di atas MA
dengan volume naik untuk konfirmasi.
"""

            msg += """
━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *Disclaimer:* Analisis bukan
nasihat keuangan. Trading risiko
tanggung sendiri.
"""

        await update.message.reply_text(msg, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Analisa error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


# === BUTTONS ===
'''

        # Replace stock section
        content = content[:stock_start] + new_stock + content[stock_end:]
        print(f"Replaced stock section: {stock_start} to {stock_end}")
    else:
        print("Could not find end of stock section")
else:
    print(f"Only found {len(stock_matches)} matches - crypto may not have been replaced")

# Write the file back
with open('handlers/command_handlers.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("File updated successfully!")
