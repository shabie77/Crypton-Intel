"""
digest/pro_digest_formatter.py

Assembles the final Pro Telegram message from pre-computed data.
Pure string formatting — zero logic, zero API calls.
Matches the Gold Standard Pro Digest format exactly.
"""
from datetime import datetime, timezone, timedelta

FULL_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "Ripple",
    "ADA": "Cardano",
    "LTC": "Litecoin",
    "DOGE": "Dogecoin",
    "PEPE": "Pepe",
    "LINK": "Chainlink",
    "AVAX": "Avalanche",
    "ARB":  "Arbitrum",
    "HYPE": "Hyperliquid",
    "DOT":  "Polkadot",
    "MATIC": "Polygon",
    "BNB":  "BNB",
    "ATOM": "Cosmos",
    "NEAR": "NEAR Protocol",
    "FIL":  "Filecoin",
    "INJ":  "Injective",
    "SUI":  "Sui",
    "APT":  "Aptos",
}


def _get_full_name(symbol: str) -> str:
    return FULL_NAMES.get(symbol.upper(), symbol.upper())


def _format_price(price: float) -> str:
    """
    Correct price formatting:
    >= 1000 → comma separator (BTC: $77,005.00)
    1–999   → no comma (SOL: $88.30, ETH: $2,405.00)
    < 1     → 4 decimals (PEPE: $0.0001)
    """
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


def _format_level(price: float) -> str:
    """Same logic as _format_price but without the leading $."""
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


def _get_ist_datetime_str() -> str:
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return now_ist.strftime("%A, %B %d, %Y | %I:%M %p IST")


def _get_volatility_label(coins: list[dict]) -> tuple[str, str]:
    """Derives a simple volatility label from average RSI spread."""
    if not coins:
        return "NORMAL", "⚪"
    avg_rsi = sum(c.get("rsi_4h", 50) for c in coins) / len(coins)
    if avg_rsi > 70 or avg_rsi < 30:
        return "HIGH", "🟠"
    elif avg_rsi > 60 or avg_rsi < 40:
        return "ELEVATED", "🟡"
    else:
        return "NORMAL", "⚪"


def format_pro_digest(
    pulse_label: str,
    pulse_score: int,
    direction: str,
    outlook: str,
    coins: list[dict],
    liq_total: str,
    liq_label: str,
    liq_emoji: str,
    liq_long_pct: int,
    liq_short_pct: int,
    liq_read: str,
    news: list[dict],
    macro_event: dict | None = None,
) -> str:

    sep = "━━━━━━━━━━━━━━━━━━━━━"
    dt_str = _get_ist_datetime_str()
    vol_label, vol_emoji = _get_volatility_label(coins)

    # ── Header ────────────────────────────────────────────────────────────────
    msg  = f"💎 <b>CRYPTON INTEL PRO — DAILY DIGEST</b>\n"
    msg += f"📅 {dt_str}\n"
    msg += f"{sep}\n\n"
    msg += f"🌡️ Market Pulse: <b>{pulse_label} ({pulse_score}/100)</b>\n"
    msg += f"📊 Volatility: {vol_label} {vol_emoji}\n\n"

    if direction:
        msg += f"📌 <b>Current Market Direction:</b>\n{direction}\n"

    # ── Per-Coin Sections ─────────────────────────────────────────────────────
    for coin in coins:
        sym        = coin["symbol"]
        full_name  = _get_full_name(sym)
        price      = coin["price"]
        change     = coin["change_24h"]
        low_24h    = coin["low_24h"]
        high_24h   = coin["high_24h"]

        arrow = "🔼" if change >= 0 else "🔽"
        sign  = "+" if change >= 0 else ""

        msg += f"\n{sep}\n"
        msg += f"🪙 <b>{sym} — {full_name}</b>\n"
        msg += f"💰 Price: {_format_price(price)} ({arrow} {sign}{change:.1f}% 24h)\n"
        msg += f"📍 1D Range: {_format_level(low_24h)} — {_format_level(high_24h)}\n\n"

        # RSI
        msg += f"⚡ <b>RSI Status:</b>\n"
        msg += f"4H RSI: {coin['rsi_4h']:.1f} — {coin['rsi_4h_label']}\n"
        msg += f"1D RSI: {coin['rsi_1d']:.1f} — {coin['rsi_1d_label']}\n\n"

        # Volume
        vol_desc = {
            "HIGH":   "Strong buying activity supporting current move.",
            "NORMAL": "Normal market activity with stable demand.",
            "LOW":    "Weak activity — traders waiting for direction.",
        }.get(coin["vol_label"], "")
        msg += f"📊 <b>Volume:</b>\n"
        msg += f"{coin['vol_label']} {coin['vol_emoji']} ({coin['vol_delta']:+d}% vs 20D avg)\n"
        if vol_desc:
            msg += f"{vol_desc}\n"

        # Structure
        msg += f"\n🏗️ <b>Market Structure:</b>\n"
        msg += f"4H: {coin['structure_4h']}\n"
        msg += f"1D: {coin['structure_1d']}\n"
        msg += f"1W: {coin['structure_1w']}\n"

        # Key Levels
        msg += f"\n🎯 <b>Key Levels:</b>\n"
        if coin["support_4h"] > 0:
            msg += f"4H Support: {_format_level(coin['support_4h'])}\n"
        if coin["support_1d"] > 0:
            msg += f"1D Support: {_format_level(coin['support_1d'])}\n"
        msg += "\n"
        if coin["resistance_4h"] > 0:
            msg += f"4H Resistance: {_format_level(coin['resistance_4h'])}\n"
        if coin["resistance_1d"] > 0:
            msg += f"1D Resistance: {_format_level(coin['resistance_1d'])}\n"

        # Streak
        msg += f"\n🔥 <b>Trend Streak:</b>\n{coin['streak_display']}\n"

        # AI narrative sections (with deterministic fallbacks)
        trader_read  = coin.get("trader_read", "")
        what_matters = coin.get("what_matters", "")

        if not trader_read:
            trader_read = _fallback_trader_read(coin)
        if not what_matters:
            what_matters = _fallback_what_matters(coin)

        msg += f"\n📖 <b>Trader Read:</b>\n{trader_read}\n"
        msg += f"\n📌 <b>What Matters Now:</b>\n{what_matters}\n"

        # High Interest Zone
        if coin.get("high_interest_zone", 0) > 0:
            msg += (
                f"\n📍 <i>High Interest Zone: {_format_level(coin['high_interest_zone'])}</i>\n"
                f"<i>Strong movement likely if this level breaks.</i>\n"
            )

    # ── Liquidations ──────────────────────────────────────────────────────────
    msg += f"\n{sep}\n"
    msg += f"💥 <b>24H LIQUIDATIONS</b>\n\n"
    if liq_label == "DATA UNAVAILABLE":
        msg += "Total: DATA UNAVAILABLE ⚪\n"
    else:
        msg += f"Total Liquidations: {liq_total}\n"
        msg += f"Longs: {liq_long_pct}%\n"
        msg += f"Shorts: {liq_short_pct}%\n\n"
        msg += f"📌 <i>Market Read:</i>\n{liq_read}\n"

    # ── Macro Events ──────────────────────────────────────────────────────────
    if macro_event:
        impact = macro_event.get("impact", "🟠")
        msg += f"\n{sep}\n"
        msg += f"📅 <b>HIGH IMPACT EVENTS</b>\n\n"
        msg += f"{impact} {macro_event.get('time', '')} — {macro_event.get('title', '')}\n"
        note = macro_event.get("note", "")
        if note:
            msg += f"<i>Why it matters:</i>\n{note}\n"

    # ── News ──────────────────────────────────────────────────────────────────
    if news:
        msg += f"\n{sep}\n"
        msg += f"📰 <b>TOP MARKET NEWS</b>\n\n"
        for item in news[:3]:
            tier  = item.get("tier", "")
            title = item.get("title", "")
            emoji = item.get("emoji", "🔵")
            msg += f"{emoji} <b>{tier}</b>\n{title}\n\n"

    # ── Market Outlook ────────────────────────────────────────────────────────
    if outlook:
        msg += f"\n{sep}\n"
        msg += f"🧭 <b>TODAY'S MARKET OUTLOOK</b>\n\n{outlook}\n"

    # ── Footer ────────────────────────────────────────────────────────────────
    msg += f"\n{sep}\n"
    msg += "⚠️ Market analysis only. Not financial advice.\n\n"
    msg += "💎 Crypton Intel Pro Active\n"
    msg += "📡 Data: Binance | CoinGlass | TradingEconomics\n"
    msg += sep

    return msg


# ── Deterministic Fallback Texts (zero placeholder policy) ───────────────────

def _fallback_trader_read(coin: dict) -> str:
    bias = coin.get("bias", "NEUTRAL")
    reads = {
        "BULLISH_CONFIRMED": (
            f"{coin['symbol']} remains strong across higher timeframes. "
            "Buying pressure is active and the current structure supports continuation. "
            "Momentum is intact while price holds above key support."
        ),
        "BULLISH_WEAKENING": (
            f"{coin['symbol']} holds a bullish structure, but buying volume is fading. "
            "Momentum is slowing compared to earlier sessions. "
            "A pullback toward support would offer a healthier entry opportunity."
        ),
        "BEARISH_CONFIRMED": (
            f"{coin['symbol']} shows bearish structure with volume confirming the move. "
            "Sellers remain in control across key timeframes. "
            "No recovery signal has appeared at current levels."
        ),
        "BEARISH_WEAKENING": (
            f"{coin['symbol']} remains in a bearish structure, but selling pressure is easing. "
            "Price is approaching a key support zone where reaction is possible. "
            "Watch for volume confirmation before assuming a reversal."
        ),
        "RECOVERING": (
            f"{coin['symbol']} is recovering from recent lows with improving momentum. "
            "Structure is improving but full confirmation has not arrived yet. "
            "A clean close above 4H resistance would strengthen the bullish case."
        ),
        "NEUTRAL": (
            f"{coin['symbol']} is consolidating without a clear directional bias. "
            "Both buyers and sellers are waiting for stronger momentum. "
            "No strong setup is present at current levels."
        ),
    }
    return reads.get(bias, reads["NEUTRAL"])


def _fallback_what_matters(coin: dict) -> str:
    bias = coin.get("bias", "NEUTRAL")
    support_4h    = coin.get("support_4h", 0)
    resistance_4h = coin.get("resistance_4h", 0)

    sup_str = _format_level(support_4h)    if support_4h    > 0 else "key support"
    res_str = _format_level(resistance_4h) if resistance_4h > 0 else "key resistance"

    if "BULLISH" in bias:
        return (
            f"Holding above {sup_str} keeps the bullish structure valid. "
            f"A clean break above {res_str} would open the next target zone. "
            "Avoid chasing sharp spikes — wait for confirmed closes."
        )
    elif "BEARISH" in bias:
        return (
            f"Staying below {res_str} keeps sellers in control. "
            f"A close below {sup_str} may increase downside pressure. "
            "No long setups until structure shows clear improvement."
        )
    else:
        return (
            f"Watch for a decisive break of {res_str} or below {sup_str}. "
            "Either direction needs volume confirmation before acting. "
            "No strong setup currently active."
        )
