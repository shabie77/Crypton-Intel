"""
digest/digest_formatter.py

Assembles all pre-computed sections into the final Telegram message.
Pure string formatting. No logic, no API calls.
Target: 2200-2500 characters.
"""
from datetime import datetime, timezone, timedelta


def get_ist_datetime_str() -> str:
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return now_ist.strftime("%A, %B %d, %Y | %I:%M %p IST")


def get_trend_emoji(snapshot: dict) -> str:
    trend_1d = snapshot.get("trend_1d", "BEAR")
    return "🟢" if trend_1d == "BULL" else "🔴"


def format_free_digest(
    pulse_label: str,
    pulse_score: int,
    context: str,
    risk: str,
    coins: list[dict],
    liq_total: str,
    liq_label: str,
    liq_emoji: str,
    liq_long_pct: int,
    liq_short_pct: int,
    liq_read: str,
    news: list[dict],
    takeaway: str,
    pro_count: int,
    macro_event: dict | None = None,
) -> str:
    sep = "━━━━━━━━━━━━━━━━━━━━━"
    dt_str = get_ist_datetime_str()

    msg = f"📊 <b>CRYPTON INTEL — MARKET UPDATE</b>\n"
    msg += f"📅 {dt_str}\n\n"
    msg += f"🌡️ Market Pulse: <b>{pulse_label} ({pulse_score}/100)</b>\n\n"
    msg += f"📌 <b>Market Context:</b>\n{context}\n\n"
    msg += f"⚠️ <b>Today's Risk:</b>\n{risk}\n"

    # ── Coin Sections ──
    for coin in coins:
        sym = coin["symbol"]
        price = coin["price"]
        change = coin["change_24h"]
        rsi = coin["rsi"]
        rsi_label = coin["rsi_label"]
        vol_label = coin["vol_label"]
        vol_emoji = coin["vol_emoji"]
        vol_delta = coin["vol_delta"]
        low_24h = coin["low_24h"]
        high_24h = coin["high_24h"]
        read = coin["read"]
        trend_emoji = coin["trend_emoji"]

        arrow = "▲" if change >= 0 else "▼"
        sign = "+" if change >= 0 else ""

        msg += f"\n{sep}\n\n"
        msg += f"{trend_emoji} <b>{sym} — ${price:,.2f} ({arrow}{sign}{change:.2f}%)</b>\n\n"
        msg += f"⚡ RSI: {rsi:.1f} — {rsi_label}\n"
        msg += f"📊 Volume: {vol_label} {vol_emoji} ({vol_delta:+d}% vs 20D avg)\n"
        msg += f"📉 24H Range: ${low_24h:,.2f} → ${high_24h:,.2f}\n\n"
        msg += f"📖 <i>Read:</i>\n{read}\n"

    # ── Liquidations ──
    msg += f"\n{sep}\n\n"
    msg += f"💥 <b>24H LIQUIDATIONS</b>\n\n"
    if liq_label == "DATA UNAVAILABLE":
        msg += f"Total: DATA UNAVAILABLE ⚪\n\n"
    else:
        msg += f"Total: {liq_total} [{liq_label} {liq_emoji}]\n"
        msg += f"Longs: {liq_long_pct}% | Shorts: {liq_short_pct}%\n\n"
        msg += f"📖 <i>Liquidation Read:</i>\n{liq_read}\n"

    # ── News ──
    if news:
        msg += f"\n{sep}\n\n"
        msg += f"📰 <b>TOP MARKET NEWS</b>\n\n"
        for item in news:
            msg += f"{item['emoji']} <b>{item['tier']}</b>\n{item['title']}\n\n"

    # ── Macro Event (optional) ──
    if macro_event:
        msg += f"\n{sep}\n\n"
        msg += f"⚠️ <b>MACRO ALERT</b>\n"
        msg += f"{macro_event['title']} — {macro_event['time']}\n"
        msg += f"{macro_event.get('note', 'Monitor market reaction.')}\n"

    # ── Takeaway ──
    msg += f"\n{sep}\n\n"
    msg += f"📌 <b>Market Takeaway:</b>\n{takeaway}\n"

    # ── Pro Upsell ──
    msg += f"\n{sep}\n\n"
    msg += f"💎 <b>PRO traders are receiving:</b>\n\n"
    msg += "• Key support & resistance levels\n"
    msg += "• Multi-timeframe structure analysis\n"
    msg += "• High-confidence setup detection\n"
    msg += "• Exact invalidation zones\n"
    msg += "• Personalized watchlist intelligence\n\n"
    msg += "→ Type /upgrade to unlock Pro Intel\n"

    # ── Footer ──
    msg += f"\n{sep}\n"
    msg += "⚠️ Market analysis only. Not financial advice.\n"
    msg += "📡 Data: Binance | CoinGlass | TradingEconomics"

    # ── Character count guard ──
    if len(msg) > 2500:
        # Trim context and takeaway to stay within limit
        msg = msg.replace(context, context[:200] + "...")
        msg = msg.replace(takeaway, takeaway[:200] + "...")

    return msg
