"""
digest/market_logic.py

All deterministic market analysis logic.
AI layer NEVER touches this. Pure Python facts.
"""
import asyncio

import database


def classify_rsi(rsi: float) -> tuple[str, str]:
    """Returns (label, emoji)."""
    if rsi < 30:
        return "Oversold", "ðŸ”´"
    if rsi < 60:
        return "Neutral", "âšª"
    if rsi < 70:
        return "Elevated momentum", "ðŸŸ "
    return "Overbought", "ðŸ”´"


def classify_volume(vol_ratio: float) -> tuple[str, str, int]:
    """Returns (label, emoji, delta_pct)."""
    delta_pct = int((vol_ratio - 1.0) * 100)
    if vol_ratio > 1.25:
        return "HIGH", "ðŸŸ¢", delta_pct
    if vol_ratio >= 0.75:
        return "NORMAL", "âšª", delta_pct
    return "LOW", "ðŸ”´", delta_pct


def classify_trend(snapshot: dict) -> str:
    """Returns a plain English trend state label."""
    trend_1d = snapshot.get("trend_1d", "BEAR")
    trend_4h = snapshot.get("trend_4h", "BEAR")
    rsi = snapshot.get("rsi", 50.0)

    if trend_1d == "BULL" and trend_4h == "BULL":
        if rsi > 65:
            return "Bullish â€” Late Stage"
        return "Bullish"
    if trend_1d == "BULL" and trend_4h == "BEAR":
        return "Bullish â€” Retracing"
    if trend_1d == "BEAR" and trend_4h == "BULL":
        return "Recovering"
    if rsi < 35:
        return "Bearish â€” Oversold"
    return "Bearish"


def get_bias_baseline(snapshot: dict) -> str:
    """Simple directional bias for AI context injection."""
    trend = classify_trend(snapshot)
    vol_ratio = snapshot.get("vol_ratio", 1.0)

    if "Bullish" in trend and vol_ratio >= 1.0:
        return "BULLISH_CONFIRMED"
    if "Bullish" in trend and vol_ratio < 0.75:
        return "BULLISH_WEAKENING"
    if "Bearish" in trend and vol_ratio >= 1.0:
        return "BEARISH_CONFIRMED"
    if "Bearish" in trend:
        return "BEARISH_WEAKENING"
    if "Recovering" in trend:
        return "RECOVERING"
    return "NEUTRAL"


def get_24h_range(snapshot: dict) -> tuple[float, float]:
    """Returns (low, high) from the most recent 4H candles covering 24H."""
    highs = snapshot.get("highs", [])
    lows = snapshot.get("lows", [])
    if not highs or not lows:
        price = snapshot.get("price", 0.0)
        return price, price
    recent_highs = highs[-6:]
    recent_lows = lows[-6:]
    return min(recent_lows), max(recent_highs)


async def update_streak(symbol: str, snapshot: dict) -> int:
    """
    Tracks consecutive bullish/bearish 1D trend closes.
    Positive = bullish streak days, negative = bearish streak days.
    Returns current streak count.
    """
    today_trend = classify_trend(snapshot)
    return await asyncio.to_thread(database.update_market_streak_atomic, symbol, today_trend)


def classify_liquidations(liq: dict) -> tuple[str, str, str, str]:
    """
    Returns (total_str, label, emoji, read_text).
    If data is unavailable, returns the fallback safely.
    """
    total = liq.get("total", 0) if liq else 0

    if total == 0:
        return "N/A", "DATA UNAVAILABLE", "âšª", "Liquidation data unavailable for this cycle."

    total_m = total / 1_000_000
    longs = liq.get("longs", 0)
    shorts = liq.get("shorts", 0)
    denom = longs + shorts if (longs + shorts) > 0 else 1
    long_pct = int((longs / denom) * 100)
    short_pct = 100 - long_pct

    if total_m < 50:
        label, emoji = "LOW", "ðŸŸ¢"
    elif total_m < 200:
        label, emoji = "NORMAL", "âšª"
    elif total_m < 500:
        label, emoji = "HIGH", "ðŸŸ "
    else:
        label, emoji = "EXTREME", "ðŸ”´"

    if long_pct > short_pct:
        read = f"Long liquidations dominate at {long_pct}%, showing bullish traders were recently flushed."
    elif short_pct > long_pct:
        read = f"Short liquidations dominate at {short_pct}%, showing bearish traders were squeezed out."
    else:
        read = "Liquidations are balanced between longs and shorts this cycle."

    return f"${total_m:.0f}M", label, emoji, read


def format_streak_display(streak: int, trend: str) -> str:
    """
    Formats the trend streak for display in the Pro Digest.
    Streak is the raw int from update_streak() (positive=bull, negative=bear).
    """
    abs_days = abs(streak) if streak != 0 else 1
    if "Bullish" in trend:
        direction = "Bullish"
    elif "Bearish" in trend:
        direction = "Bearish"
    else:
        direction = "Neutral"
    return f"{direction} â€” Day {abs_days}"
