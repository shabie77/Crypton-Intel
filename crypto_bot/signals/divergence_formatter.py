"""
signals/divergence_formatter.py

Formats RSI divergence alerts into HTML Telegram messages and dispatches them.
Handles all 3 alert levels, 2 directions, and 3 timeframes cleanly.
"""
import asyncio

from utils.telegram import send_telegram_message


def format_divergence_alert(
    symbol: str,
    tf: str,
    direction: str,
    level: int,
    price: float,
    swing_data: dict,
    key_level,
    volume_confirmed: bool,
) -> str:
    """
    Build an HTML-formatted Telegram message for a divergence alert.

    Args:
        symbol:           Coin symbol e.g. "BTC"
        tf:               Timeframe string e.g. "4h", "1d", "1w"
        direction:        "BULLISH" or "BEARISH"
        level:            1, 2, or 3
        price:            Current market price
        swing_data:       Dict from detect_rsi_divergence() with price1/price2/rsi1/rsi2
        key_level:        Nearby S/R price level (None for Level 1)
        volume_confirmed: True only at Level 3
    """
    tier_emoji = {1: "📊", 2: "🎯", 3: "✅"}
    tier_label = {1: "WATCH", 2: "SETUP", 3: "CONFIRMED"}

    dir_emoji = "🟢" if direction == "BULLISH" else "🔴"
    dir_text  = "Bullish" if direction == "BULLISH" else "Bearish"

    price_move = "Lower Low"  if direction == "BULLISH" else "Higher High"
    rsi_move   = "Higher Low" if direction == "BULLISH" else "Lower High"

    # Header suffix per level
    header_suffix = {
        1: " Forming",
        2: " at Key Level",
        3: " at Key Level + Volume Exhaustion",
    }

    # Weekly rarity note
    weekly_tag = ""
    if tf.lower() == "1w":
        weekly_tag = "\n⭐ <b>WEEKLY TIMEFRAME — RARE SIGNAL</b>"

    tf_display = tf.upper()
    em         = tier_emoji.get(level, "📊")
    lbl        = tier_label.get(level, "WATCH")
    suffix     = header_suffix.get(level, "")

    lines = [
        f"{em} <b>RSI DIVERGENCE — {symbol} | {tf_display}</b>{weekly_tag}",
        "",
        f"{dir_emoji} <b>{dir_text} Divergence{suffix}</b>",
        "",
        f"Price:  {price_move}  →  <b>${swing_data['price2']:,.2f}</b>  (was ${swing_data['price1']:,.2f})",
        f"RSI:    {rsi_move}   →  <b>{swing_data['rsi2']:.1f}</b>  (was {swing_data['rsi1']:.1f})",
    ]

    # Level 2+: key level details
    if level >= 2 and key_level:
        zone_type = "Support" if direction == "BULLISH" else "Resistance"
        lines += [
            "",
            f"📍 Key {zone_type}:  <b>${key_level:,.2f}</b>",
            f"Price is reacting at a tested {zone_type.lower()} zone.",
        ]

    # Level 3: volume exhaustion
    if level == 3 and volume_confirmed:
        lines += [
            "",
            "📉 Volume declining at 2nd swing — exhaustion confirmed.",
        ]

    lines += [
        "",
        f"Signal: <b>{level}/3 — {lbl}</b>",
    ]

    if level == 1:
        lines.append("⚠️ Early signal. Wait for candle close before acting.")
    elif level == 2:
        lines.append("Risk is defined at the key level. Watch for confirmation candle.")
    else:
        lines.append("🔥 All 3 confluence factors aligned — highest conviction setup.")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        "<i>Market analysis only. Not financial advice.</i>",
    ]

    return "\n".join(lines)


async def dispatch_divergence_alert(session, user_ids: list, message: str) -> int:
    """
    Send the formatted message to each user ID.
    Uses the existing send_telegram_message(session, chat_id, text)
    which handles rate limits, retries, and HTML parse mode internally.

    Returns count of successfully delivered messages.
    """
    sent = 0
    for uid in user_ids:
        try:
            success = await send_telegram_message(session, str(uid), message)
            if success:
                sent += 1
            await asyncio.sleep(0.05)  # Respect Telegram global rate limit
        except Exception as e:
            print(f"⚠️ Divergence dispatch error → user {uid}: {e}")
    return sent
