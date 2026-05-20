"""
digest/pro_engine.py

Pro Digest Orchestrator.
Builds a fully personalised digest per Pro user based on their watchlist.
Reads from the shared market cache — fetches individually only for niche coins.
Makes ONE AI call per user per cycle.
Never shows placeholder text or errors to the user.
"""
import asyncio
import aiohttp
import database

from utils.binance_api import fetch_symbol_snapshot
from utils.config import load_config
from digest.market_logic import (
    classify_volume,
    classify_trend,
    get_bias_baseline,
    get_24h_range,
    update_streak,
    classify_liquidations,
    format_streak_display,
)
from digest.liquidation_engine import fetch_liquidation_data
from digest.news_engine import fetch_news
from digest.macro_engine import fetch_macro_event
from digest.digest_context import build_pro_fallback_context, generate_pro_context
from digest.pro_digest_formatter import format_pro_digest
from indicators.rsi import classify_rsi_pro
from indicators.levels import calculate_swing_levels
from indicators.bos import determine_structure


# ── Structure wording map ─────────────────────────────────────────────────────

STRUCTURE_WORDING = {
    "🟢 Bullish": "🟢 Buyers still forming higher highs",
    "🔴 Bearish": "🔴 Lower highs continue forming",
    "🟡 Range":   "🟡 Market moving sideways",
}


def _map_structure(raw: str) -> str:
    return STRUCTURE_WORDING.get(raw, raw)


def _build_coin_data(symbol: str, snap: dict, all_streaks: dict) -> dict:
    """
    Extracts and calculates all Pro-level data for one coin from a snapshot.
    Pure deterministic calculations — no AI, no API calls.
    """
    if snap is None:
        return {
            "symbol": symbol,
            "price": 0.0,
            "change_24h": 0.0,
            "low_24h": 0.0,
            "high_24h": 0.0,
            "rsi_4h": 50.0,
            "rsi_4h_label": "DATA UNAVAILABLE",
            "rsi_1d": 50.0,
            "rsi_1d_label": "DATA UNAVAILABLE",
            "vol_label": "DATA UNAVAILABLE",
            "vol_emoji": "⚪",
            "vol_delta": 0,
            "structure_4h": "DATA UNAVAILABLE",
            "structure_1d": "DATA UNAVAILABLE",
            "structure_1w": "DATA UNAVAILABLE",
            "support_4h": 0.0,
            "resistance_4h": 0.0,
            "support_1d": 0.0,
            "resistance_1d": 0.0,
            "streak_display": "Data unavailable",
            "high_interest_zone": 0.0,
            "trend": "Neutral",
            "bias": "NEUTRAL",
            "trader_read": "Market data unavailable for this coin this cycle.",
            "what_matters": "Awaiting fresh data.",
        }

    price      = snap.get("price", 0.0)
    change_24h = snap.get("change_24h", 0.0)
    vol_ratio  = snap.get("vol_ratio", 1.0)

    # Multi-TF RSI
    rsi_4h = snap.get("rsi_4h", snap.get("rsi", 50.0))
    rsi_1d = snap.get("rsi_1d", 50.0)
    rsi_1w = snap.get("rsi_1w", 50.0)

    rsi_4h_label, _ = classify_rsi_pro(rsi_4h)
    rsi_1d_label, _ = classify_rsi_pro(rsi_1d)

    # Volume
    vol_label, vol_emoji, vol_delta = classify_volume(vol_ratio)

    # 1D range
    low_24h, high_24h = get_24h_range(snap)

    # Market structure per timeframe
    highs_4h = snap.get("highs", [])
    lows_4h  = snap.get("lows", [])
    highs_1d = snap.get("highs_1d", [])
    lows_1d  = snap.get("lows_1d", [])
    highs_1w = snap.get("highs_1w", [])
    lows_1w  = snap.get("lows_1w", [])

    structure_4h_raw = determine_structure(highs_4h, lows_4h) if len(highs_4h) >= 6 else "🟡 Range"
    structure_1d_raw = determine_structure(highs_1d, lows_1d) if len(highs_1d) >= 6 else "🟡 Range"
    structure_1w_raw = determine_structure(highs_1w, lows_1w) if len(highs_1w) >= 6 else "🟡 Range"

    structure_4h = _map_structure(structure_4h_raw)
    structure_1d = _map_structure(structure_1d_raw)
    structure_1w = _map_structure(structure_1w_raw)

    # Key levels — swing pivot method
    levels_4h = calculate_swing_levels(highs_4h, lows_4h) if len(highs_4h) >= 6 else {"support": 0.0, "resistance": 0.0}
    levels_1d = calculate_swing_levels(highs_1d, lows_1d) if len(highs_1d) >= 6 else {"support": 0.0, "resistance": 0.0}

    # High Interest Zone = nearest 4H resistance
    high_interest_zone = levels_4h["resistance"]

    # Trend and streak (from state file — already updated by digest cycle)
    trend  = classify_trend(snap)
    bias   = get_bias_baseline(snap)
    
    coin_state = all_streaks.get(symbol, {})
    streak = coin_state.get("streak_days", 1)
    streak_trend = coin_state.get("trend", "Neutral")
    streak_display = format_streak_display(streak, streak_trend if streak_trend else trend)

    return {
        "symbol":            symbol,
        "price":             price,
        "change_24h":        change_24h,
        "low_24h":           low_24h,
        "high_24h":          high_24h,

        # RSI
        "rsi_4h":            rsi_4h,
        "rsi_4h_label":      rsi_4h_label,
        "rsi_1d":            rsi_1d,
        "rsi_1d_label":      rsi_1d_label,

        # Volume
        "vol_label":         vol_label,
        "vol_emoji":         vol_emoji,
        "vol_delta":         vol_delta,

        # Structure
        "structure_4h":      structure_4h,
        "structure_1d":      structure_1d,
        "structure_1w":      structure_1w,

        # Key levels
        "support_4h":        levels_4h["support"],
        "resistance_4h":     levels_4h["resistance"],
        "support_1d":        levels_1d["support"],
        "resistance_1d":     levels_1d["resistance"],

        # Streak
        "streak_display":    streak_display,

        # High interest zone
        "high_interest_zone": high_interest_zone,

        # AI context inputs
        "trend":             trend,
        "bias":              bias,

        # Placeholders — filled after AI call
        "trader_read":       "",
        "what_matters":      "",
    }


# ── Pro Digest Orchestrator ───────────────────────────────────────────────────

async def build_pro_digest(
    session: aiohttp.ClientSession,
    user_id: str,
    watchlist: list[str],
) -> str:
    """
    Builds a fully personalised Pro Digest for one user.
    Reads from shared cache where possible, fetches individually for niche coins.
    Makes one AI call per user. Falls back gracefully if AI fails.
    Returns formatted Telegram message string.
    """
    config = await load_config()

    # ── STEP 1: Gather snapshots (cache-first) ────────────────────────────────
    snapshots: dict[str, dict] = {}
    symbols_to_fetch = []

    from utils.state import (
        _MARKET_CACHE_LOCK,
        _MARKET_SNAPSHOT_CACHE,
        MARKET_SNAPSHOT_TTL_SECONDS,
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    for sym in watchlist[:5]:
        # Check shared cache first
        async with _MARKET_CACHE_LOCK:
            cached = _MARKET_SNAPSHOT_CACHE.get(sym)
        if cached:
            val, ts = cached
            if (now - ts).total_seconds() < MARKET_SNAPSHOT_TTL_SECONDS:
                snapshots[sym] = val
                continue
        # Not in cache or stale — fetch individually
        symbols_to_fetch.append(sym)

    if symbols_to_fetch:
        results = await asyncio.gather(
            *(fetch_symbol_snapshot(session, sym, config) for sym in symbols_to_fetch),
            return_exceptions=True
        )
        for sym, result in zip(symbols_to_fetch, results):
            if isinstance(result, Exception) or result is None:
                print(f"⚠️ Pro snapshot fetch error for {sym}: {result}")
                snapshots[sym] = None
                continue
            if result:
                snapshots[sym] = result
                # Update streak for non-BTC/ETH/SOL coins not tracked by free digest
                try:
                    await update_streak(sym, result)
                except Exception as e:
                    print(f"⚠️ update_streak error for {sym}: {e}")

    if not snapshots:
        return (
            "💎 <b>CRYPTON INTEL PRO</b>\n\n"
            "⚠️ Market data temporarily unavailable.\n"
            "Your personalized digest will resume on the next cycle."
        )

    # ── STEP 2: Calculate per-coin data deterministically ─────────────────────
    all_streaks = await asyncio.to_thread(database.get_market_streaks, watchlist[:5])
    
    coin_data_list = []
    for sym in watchlist[:5]:
        if sym not in snapshots:
            continue
        coin_data = _build_coin_data(sym, snapshots[sym], all_streaks)
        coin_data_list.append(coin_data)

    # ── STEP 3: Fetch shared data (liquidations, news, macro) ─────────────────
    liq_data, news, macro = await asyncio.gather(
        fetch_liquidation_data(),
        fetch_news(session),
        fetch_macro_event(session),
        return_exceptions=True
    )

    if isinstance(liq_data, Exception) or liq_data is None:
        liq_data = {}
    if isinstance(news, Exception):
        news = []
    if isinstance(macro, Exception):
        macro = None

    # Classify liquidations
    liq_total, liq_label, liq_emoji, liq_read = classify_liquidations(liq_data)
    longs  = liq_data.get("longs", 0)
    shorts = liq_data.get("shorts", 0)
    denom  = longs + shorts if (longs + shorts) > 0 else 1
    liq_long_pct  = int((longs / denom) * 100) if liq_label != "DATA UNAVAILABLE" else 50
    liq_short_pct = 100 - liq_long_pct

    # ── STEP 4: Single AI call per user ───────────────────────────────────────
    pulse_label = "NEUTRAL"  # shared pulse from free digest cycle if available
    try:
        from utils.calculations import calculate_market_pulse
        all_snaps = [snap for snap in snapshots.values() if snap is not None]
        if all_snaps:
            pulse = await asyncio.to_thread(calculate_market_pulse, all_snaps, config)
            pulse_label = pulse.get("label", "NEUTRAL")
            pulse_score = int(pulse.get("score", 50))
        else:
            pulse_score = 50
    except Exception:
        pulse_score = 50

    try:
        ai_output = await asyncio.wait_for(
            generate_pro_context(session, coin_data_list, pulse_label),
            timeout=35
        )
    except asyncio.TimeoutError:
        print(f"⚠️ Pro AI timeout for user {user_id}. Using fallback.")
        ai_output = build_pro_fallback_context(coin_data_list)
    except Exception as e:
        print(f"⚠️ Pro AI error for user {user_id}: {e}")
        ai_output = build_pro_fallback_context(coin_data_list)

    # Inject AI text into coin data
    for coin in coin_data_list:
        sym = coin["symbol"]
        coin["trader_read"]  = ai_output["reads"].get(sym, "")
        coin["what_matters"] = ai_output["matters"].get(sym, "")

    # ── STEP 5: Format final message ──────────────────────────────────────────
    return format_pro_digest(
        pulse_label=pulse_label,
        pulse_score=pulse_score,
        direction=ai_output.get("direction", ""),
        outlook=ai_output.get("outlook", ""),
        coins=coin_data_list,
        liq_total=liq_total,
        liq_label=liq_label,
        liq_emoji=liq_emoji,
        liq_long_pct=liq_long_pct,
        liq_short_pct=liq_short_pct,
        liq_read=liq_read,
        news=news,
        macro_event=macro if isinstance(macro, dict) else None,
    )
