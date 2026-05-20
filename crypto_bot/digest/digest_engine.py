"""
digest/digest_engine.py

Free Digest Orchestrator.
Coordinates all modules. Produces one final message per cycle.
Does NOT touch Pro digest logic.
"""
import asyncio
import aiohttp

from utils.binance_api import fetch_symbol_snapshot
from utils.config import load_config
from digest.market_logic import (
    classify_rsi, classify_volume, classify_trend,
    get_bias_baseline, get_24h_range, update_streak,
    classify_liquidations
)
from digest.liquidation_engine import fetch_liquidation_data
from digest.news_engine import fetch_news
from digest.macro_engine import fetch_macro_event
from digest.digest_context import generate_digest_context
from digest.digest_formatter import format_free_digest
from utils.calculations import calculate_market_pulse
import utils.health_monitor as health

FREE_COINS = ["BTC", "ETH", "SOL"]


async def build_free_digest(session: aiohttp.ClientSession) -> str:
    """
    Full orchestration of the free digest.
    Returns formatted message string.
    """
    config = await load_config()

    # ── STEP 1: Fetch all market data ONCE ──────────────────────────────────
    snapshots = {}
    
    # Parallelize fetch to prevent sequential latency bottleneck
    snapshot_results = await asyncio.gather(
        *(fetch_symbol_snapshot(session, sym, config) for sym in FREE_COINS),
        return_exceptions=True
    )
    
    for sym, snap in zip(FREE_COINS, snapshot_results):
        if isinstance(snap, Exception):
            print(f"⚠️ fetch_symbol_snapshot error for {sym}: {snap}")
            continue
        if snap:
            snapshots[sym] = snap
            # Protect database write so failure doesn't crash entire digest
            try:
                await update_streak(sym, snap)
            except Exception as e:
                print(f"⚠️ update_streak error for {sym}: {e}")

    if not snapshots:
        await health.update("binance_api", "error", "No snapshots returned")
        await health.update("digest_engine", "error", "No market data fetched")
        return "⚠️ Market data temporarily unavailable. Please check back shortly."

    await health.update("binance_api", "ok")

    # ── STEP 2: Compute pulse ────────────────────────────────────────────────
    try:
        # Offload heavy calculation to thread to avoid blocking the event loop
        pulse = await asyncio.to_thread(calculate_market_pulse, list(snapshots.values()), config)
    except Exception as e:
        print(f"⚠️ calculate_market_pulse error: {e}")
        pulse = {"score": 50, "label": "NEUTRAL"}

    pulse_label = pulse.get("label", "NEUTRAL")
    pulse_score = int(pulse.get("score", 50))

    # ── STEP 3: Classify all coin data deterministically ─────────────────────
    coin_summaries = []   # for AI context prompt
    coin_display = []     # for formatter

    for sym in FREE_COINS:
        if sym not in snapshots:
            continue
        snap = snapshots[sym]

        rsi = snap.get("rsi", 50.0)
        vol_ratio = snap.get("vol_ratio", 1.0)
        rsi_label, rsi_emoji = classify_rsi(rsi)
        vol_label, vol_emoji, vol_delta = classify_volume(vol_ratio)
        trend = classify_trend(snap)
        bias = get_bias_baseline(snap)
        low_24h, high_24h = get_24h_range(snap)
        trend_emoji = snap.get("trend_emoji", "🟢")

        coin_summaries.append({
            "symbol": sym,
            "price": snap.get("price", 0.0),
            "change_24h": snap.get("change_24h", 0.0),
            "rsi": rsi,
            "rsi_label": rsi_label,
            "vol_label": vol_label,
            "trend": trend,
            "bias": bias,
        })

        coin_display.append({
            "symbol": sym,
            "price": snap.get("price", 0.0),
            "change_24h": snap.get("change_24h", 0.0),
            "rsi": rsi,
            "rsi_label": rsi_label,
            "vol_label": vol_label,
            "vol_emoji": vol_emoji,
            "vol_delta": vol_delta,
            "low_24h": low_24h,
            "high_24h": high_24h,
            "trend_emoji": trend_emoji,
            "read": "",  # filled after AI call
        })

    # ── STEP 4: Fetch supporting data in parallel ─────────────────────────────
    liq_data, news, macro = await asyncio.gather(
        fetch_liquidation_data(),
        fetch_news(session),
        fetch_macro_event(session),
        return_exceptions=True
    )

    if isinstance(liq_data, Exception):
        print(f"⚠️ Liquidation gather error: {liq_data}")
        liq_data = {}
    if isinstance(news, Exception):
        print(f"⚠️ News gather error: {news}")
        news = []
    if isinstance(macro, Exception):
        print(f"⚠️ Macro gather error: {macro}")
        macro = None

    await health.update("news_engine", "ok" if news else "offline")
    await health.update("macro_engine", "ok" if macro else "offline")

    # ── STEP 5: Classify liquidations ─────────────────────────────────────────
    liq_total, liq_label, liq_emoji, liq_read = classify_liquidations(liq_data)

    liq_longs = liq_data.get("longs", 0)
    liq_shorts = liq_data.get("shorts", 0)
    denom = liq_longs + liq_shorts if (liq_longs + liq_shorts) > 0 else 1
    liq_long_pct = int((liq_longs / denom) * 100) if liq_label != "DATA UNAVAILABLE" else 50
    liq_short_pct = 100 - liq_long_pct

    # ── STEP 6: Single AI call ────────────────────────────────────────────────
    try:
        # Protect AI call with timeout and fallback circuit breaker
        ai_output = await asyncio.wait_for(
            generate_digest_context(session, coin_summaries, pulse_label),
            timeout=30
        )
    except asyncio.TimeoutError:
        print("⚠️ AI generation timed out. Using fallback context.")
        ai_output = {
            "context": "The market is currently evaluating direction as key support and resistance zones are being tested.",
            "risk": "Moderate",
            "takeaway": "Wait for clear confirmation before making structural adjustments.",
            "reads": {},
            "provider": "fallback_timeout"
        }
    except Exception as e:
        print(f"⚠️ AI generation error: {e}")
        ai_output = {
            "context": "Market metrics are stabilizing. Data is currently being processed for directional confirmation.",
            "risk": "Moderate",
            "takeaway": "Maintain risk management until structural momentum resumes.",
            "reads": {},
            "provider": "fallback_error"
        }
    
    ai_provider = ai_output.get("provider", "fallback")
    if ai_provider == "groq":
        await health.update("groq_api", "ok")
    elif ai_provider == "gemini":
        await health.update("groq_api", "error", "Fallback to Gemini")
        await health.update("gemini_api", "ok")
    else:
        await health.update("groq_api", "error", "Failed")
        await health.update("gemini_api", "error", "Failed")
        
    await health.update("digest_engine", "ok")

    # Inject per-coin reads from AI output
    for coin in coin_display:
        coin["read"] = ai_output["reads"].get(coin["symbol"], "No read available.")

    # ── STEP 7: Format final message ──────────────────────────────────────────
    import database
    pro_count = await asyncio.to_thread(database.get_pro_user_count)

    msg = format_free_digest(
        pulse_label=pulse_label,
        pulse_score=pulse_score,
        context=ai_output["context"],
        risk=ai_output["risk"],
        coins=coin_display,
        liq_total=liq_total,
        liq_label=liq_label,
        liq_emoji=liq_emoji,
        liq_long_pct=liq_long_pct,
        liq_short_pct=liq_short_pct,
        liq_read=liq_read,
        news=news,
        takeaway=ai_output["takeaway"],
        pro_count=pro_count,
        macro_event=macro if isinstance(macro, dict) else None,
    )

    return msg
