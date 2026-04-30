"""
Crypton Intel — Signal Scoring Engine
Phase 2 Week 2

Evaluates market confluence across 6 independent factors and produces
a Signal Confidence Score (0-100).

This module is pure logic — no DB calls, no Telegram calls, no side effects.
All inputs must be pre-fetched and passed in by the caller.

Input — symbol_data dict (required keys):
    symbol (str)        : e.g. "BTC"
    price (float)       : current market price
    sma50 (float)       : 50-period SMA on 4H timeframe
    rsi (float)         : RSI value on primary scanning timeframe
    volume_ratio (float): current volume / 20-day average volume
    trend_4h (str)      : "BULL" or "BEAR" — 4H trend direction
    trend_1d (str)      : "BULL" or "BEAR" — 1D trend direction

Input — market_pulse (int):
    Fear & Greed index 0-100

Input — liquidations (dict):
    {"longs": float, "shorts": float}
    Must be PER COIN, last 30 minutes only (never global totals)

Output:
    dict with all scoring details — OR None if no signal condition is met
"""

import math
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIQUIDATION_SIGNIFICANCE_THRESHOLD = 50_000  # $50k delta minimum to count as aligned


# ---------------------------------------------------------------------------
# Signal Meter Builder
# ---------------------------------------------------------------------------

def build_signal_meter(score: int) -> str:
    """
    Builds a 5-block visual confidence meter from a score 0-100.
    Each block represents 20 points.

    Block states:
        🟩 = fully filled  (block floor reached, e.g. score >= 20 for block 1)
        🟨 = partially filled (score mod 20 >= 10)
        ⬜ = empty
    """
    blocks = []
    for i in range(5):
        block_floor = i * 20
        if score >= block_floor + 20:
            blocks.append("🟩")
        elif score >= block_floor + 10:
            blocks.append("🟨")
        else:
            blocks.append("⬜")
    return "".join(blocks)


# ---------------------------------------------------------------------------
# Strength Label
# ---------------------------------------------------------------------------

def get_strength_label(score: int) -> str:
    """Maps a score to a human-readable strength label."""
    if score >= 80:
        return "HIGH CONVICTION"
    elif score >= 70:
        return "STRONG"
    elif score >= 60:
        return "MODERATE"
    else:
        return "WEAK"


# ---------------------------------------------------------------------------
# Market Structure Detection
# ---------------------------------------------------------------------------

def get_market_structure(highs: list, lows: list, closes: list) -> str:
    """
    Detects market structure using last 6 candles (Close-based BOS).
    HH_HL = Higher Highs + Higher Lows = bullish structure
    LH_LL = Lower Highs + Lower Lows = bearish structure
    WEAK  = No clear pattern confirmed
    """
    if not highs or not lows or not closes:
        return "WEAK"
        
    if len(closes) < 6 or len(highs) < 6 or len(lows) < 6:
        return "WEAK"

    # Using closes for Break of Structure (BOS) as per Master Plan Stage 2
    try:
        current_close = closes[-1]
        
        # Simple BOS: Close above recent high or below recent low
        recent_high = max(highs[-6:-1])
        recent_low = min(lows[-6:-1])
        
        if current_close > recent_high:
            return "HH_HL" # Bullish BOS
        elif current_close < recent_low:
            return "LH_LL" # Bearish BOS
    except (ValueError, IndexError):
        return "WEAK"
        
    return "WEAK"


def calculate_score(symbol_data: dict, market_pulse: int, liquidations: dict, force_direction: str = None) -> dict | None:
    """
    Calculates weighted signal confluence score.
    Stage 2 Requirements:
    - 3/6 Weighted Threshold
    - BOS gets 2x weight
    - Directional Alignment check
    """
    required_keys = ["symbol", "price", "sma50", "rsi", "volume_ratio", "trend_4h", "trend_1d", "closes", "highs", "lows"]
    for key in required_keys:
        if key not in symbol_data:
            return None

    symbol       = symbol_data["symbol"]
    price        = float(symbol_data["price"])
    sma50        = float(symbol_data["sma50"])
    rsi          = float(symbol_data["rsi"])
    volume_ratio = float(symbol_data["volume_ratio"])
    trend_4h     = str(symbol_data["trend_4h"]).upper()
    trend_1d     = str(symbol_data["trend_1d"]).upper()
    closes       = symbol_data["closes"]
    highs        = symbol_data["highs"]
    lows         = symbol_data["lows"]

    # 1. Signal Direction Gate
    if force_direction:
        direction = force_direction.upper()
    elif rsi <= 35:
        direction = "LONG"
    elif rsi >= 65:
        direction = "SHORT"
    else:
        return None

    # 2. Directional Alignment Rule (Master Plan Stage 2)
    # Long only if 1D trend is BULL, Short only if 1D trend is BEAR
    if (direction == "LONG" and trend_1d != "BULL") or (direction == "SHORT" and trend_1d != "BEAR"):
        return None

    factors = []
    rationale = []
    weighted_score = 0

    # Factor 1: Trend (Price vs 50-MA) - Weight 1
    trend_ok = (direction == "LONG" and price > sma50) or (direction == "SHORT" and price < sma50)
    if trend_ok:
        weighted_score += 1
        factors.append("TREND")
        rationale.append("✅ Trend aligned with 50-MA")

    # Factor 2: Volume (1.5x) - Weight 1
    if volume_ratio >= 1.5:
        weighted_score += 1
        factors.append("VOLUME")
        rationale.append(f"✅ Volume spike ({volume_ratio:.1f}x)")

    # Factor 3: RSI Extreme - Weight 1
    rsi_ok = (direction == "LONG" and rsi <= 30) or (direction == "SHORT" and rsi >= 70)
    if rsi_ok:
        weighted_score += 1
        factors.append("RSI")
        rationale.append(f"✅ RSI extreme ({rsi:.1f})")

    # Factor 4: Market Pulse - Weight 1
    pulse_ok = (direction == "LONG" and market_pulse < 40) or (direction == "SHORT" and market_pulse > 60)
    if pulse_ok:
        weighted_score += 1
        factors.append("PULSE")
        rationale.append(f"✅ Sentiment alignment ({market_pulse}/100)")

    # Factor 5: Liquidation Flush - Weight 1
    long_liq = liquidations.get("longs", 0.0)
    short_liq = liquidations.get("shorts", 0.0)
    liq_ok = (direction == "LONG" and long_liq > short_liq * 2) or (direction == "SHORT" and short_liq > long_liq * 2)
    if liq_ok:
        weighted_score += 1
        factors.append("LIQUIDATION")
        rationale.append("✅ Liquidation flush")

    # Factor 6: Market Structure (BOS) - Weight 2
    structure = get_market_structure(highs, lows, closes)
    structure_ok = (direction == "LONG" and structure == "HH_HL") or (direction == "SHORT" and structure == "LH_LL")
    if structure_ok:
        weighted_score += 2 # BOS is 2x weight
        factors.append("BOS")
        rationale.append("✅ Structure Break (BOS) detected")

    # Final Confidence Score (0-100) based on weighted total (Max possible = 7)
    confidence_pct = int((weighted_score / 7.0) * 100)
    
    # 3/7 weighted threshold for confirmation (approximating the 3/6 rule with BOS weight)
    is_confirmed = weighted_score >= 3

    return {
        "symbol": symbol,
        "direction": direction,
        "weighted_score": weighted_score,
        "is_confirmed": is_confirmed,
        "factors": factors,
        "rationale": rationale,
        "score": confidence_pct,
        "strength": get_strength_label(confidence_pct),
        "meter": build_signal_meter(confidence_pct),
        "price": price,
        "rsi": rsi,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }

