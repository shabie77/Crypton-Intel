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
# Core Scoring Function
# ---------------------------------------------------------------------------

def calculate_score(symbol_data: dict, market_pulse: int, liquidations: dict) -> dict | None:
    """
    Calculates signal confidence score using 6-factor confluence weighting.
    """

    # ----- Input Validation -----
    required_keys = ["symbol", "price", "sma50", "rsi", "volume_ratio", "trend_4h", "trend_1d"]
    for key in required_keys:
        if key not in symbol_data:
            print(f"⚠️ ScoringEngine: Missing required key '{key}' in symbol_data. Skipping.")
            return None

    symbol       = symbol_data["symbol"]
    price        = float(symbol_data["price"])
    sma50        = float(symbol_data["sma50"])
    rsi          = float(symbol_data["rsi"])
    volume_ratio = float(symbol_data["volume_ratio"])
    trend_4h     = str(symbol_data["trend_4h"]).upper()   # "BULL" or "BEAR"
    trend_1d     = str(symbol_data["trend_1d"]).upper()   # "BULL" or "BEAR"

    # ----- Signal Direction Gate -----
    if rsi <= 30:
        direction = "LONG"
    elif rsi >= 70:
        direction = "SHORT"
    else:
        return None

    # ----- Initialise Score and Rationale -----
    score    = 0
    rationale = []

    f_trend     = 0
    f_volume    = 0
    f_rsi       = 0
    f_liq       = 0
    f_pulse     = 0
    f_alignment = 0

    # ----- Dynamic Weight Adjustment Based on Market Pulse -----
    trend_weight  = 25
    volume_weight = 20
    rsi_weight    = 20
    liq_weight    = 15
    pulse_weight  = 10
    align_weight  = 5

    if market_pulse > 70:
        trend_weight += 5
        rsi_weight   -= 5
    elif market_pulse < 30:
        rsi_weight   += 5
        trend_weight -= 5

    # ----- Factor 1: Trend Confluence (SMA50 on 4H) -----
    if direction == "LONG" and price > sma50:
        f_trend = trend_weight
        score  += f_trend
        rationale.append(f"✅ Trend aligned — price above SMA50 (LONG confirmed)")
    elif direction == "SHORT" and price < sma50:
        f_trend = trend_weight
        score  += f_trend
        rationale.append(f"✅ Trend aligned — price below SMA50 (SHORT confirmed)")
    else:
        f_trend = -10
        score  += f_trend
        rationale.append(f"⚠️ Trend conflict — counter-trend signal (SMA50 opposes direction)")

    # ----- Factor 2: Volume Intensity (Log Scale) -----
    # Aim for max points (volume_weight) at ~2.0x volume ratio
    # log(2+1) = 1.09. 20 / 1.1 ~= 18.
    raw_volume_pts = int(math.log(max(volume_ratio, 0.01) + 1) * 18)
    f_volume = min(raw_volume_pts, volume_weight)
    score   += f_volume

    if volume_ratio >= 2.0:
        rationale.append(f"✅ Volume exceptional ({volume_ratio:.1f}x avg) — {f_volume}/{volume_weight}pts")
    elif volume_ratio >= 1.5:
        rationale.append(f"✅ Volume elevated ({volume_ratio:.1f}x avg) — {f_volume}/{volume_weight}pts")
    elif volume_ratio >= 1.0:
        rationale.append(f"🟡 Volume normal ({volume_ratio:.1f}x avg) — {f_volume}/{volume_weight}pts")
    else:
        rationale.append(f"⚠️ Volume weak ({volume_ratio:.1f}x avg) — {f_volume}/{volume_weight}pts")

    # ----- Factor 3: RSI Extremity (Two-Tier) -----
    if rsi <= 25 or rsi >= 75:
        f_rsi  = rsi_weight
        score += f_rsi
        rationale.append(f"✅ RSI extreme ({rsi:.1f}) — full {f_rsi}pts")
    elif rsi <= 35 or rsi >= 65:
        f_rsi  = int(rsi_weight * 0.5)
        score += f_rsi
        rationale.append(f"🟡 RSI moderate extreme ({rsi:.1f}) — {f_rsi}/{rsi_weight}pts")
    else:
        f_rsi = 0
        rationale.append(f"⚠️ RSI mild zone ({rsi:.1f}) — 0pts")

    # ----- Factor 4: Liquidation Alignment (Reversal Strategy) -----
    # For Reversal: LONG at oversold is confirmed by LONG liquidations (Capitulation)
    #               SHORT at overbought is confirmed by SHORT liquidations (Exhaustion)
    long_liq  = liquidations.get("longs", 0.0)
    short_liq = liquidations.get("shorts", 0.0)
    total_liq = long_liq + short_liq
    delta     = abs(long_liq - short_liq)

    if total_liq > 0 and delta >= LIQUIDATION_SIGNIFICANCE_THRESHOLD:
        dominant_liq = "LONG" if long_liq > short_liq else "SHORT"
        liq_confirms_direction = (
            (direction == "LONG"  and dominant_liq == "LONG") or
            (direction == "SHORT" and dominant_liq == "SHORT")
        )
        if liq_confirms_direction:
            f_liq  = liq_weight
            score += f_liq
            dominant_label = "longs" if dominant_liq == "LONG" else "shorts"
            rationale.append(f"✅ Liquidation aligned — {dominant_label} flush/exhaustion detected (${delta:,.0f} delta)")
        else:
            f_liq = 0
            rationale.append(f"⚠️ Liquidation opposing — counter-pressure dominant (${delta:,.0f} delta)")
    elif total_liq > 0 and delta < LIQUIDATION_SIGNIFICANCE_THRESHOLD:
        f_liq = 0
        rationale.append(f"🟡 Liquidation insufficient delta (${delta:,.0f} < $50k threshold)")
    else:
        f_liq = 0
        rationale.append(f"🟡 No liquidation data in 30min window for {symbol}")

    # ----- Factor 5: Market Pulse Bonus (Contrarian Sentiment) -----
    # "Buy the fear, Sell the greed"
    if market_pulse < 30 and direction == "LONG":
        f_pulse = pulse_weight
        score  += f_pulse
        rationale.append(f"✅ Sentiment confirms LONG (Extreme Fear: {market_pulse}/100)")
    elif market_pulse > 70 and direction == "SHORT":
        f_pulse = pulse_weight
        score  += f_pulse
        rationale.append(f"✅ Sentiment confirms SHORT (Extreme Greed: {market_pulse}/100)")
    elif 40 <= market_pulse <= 60:
        f_pulse = 0
        rationale.append(f"🟡 Sentiment neutral ({market_pulse}/100) — no bonus")
    else:
        f_pulse = 0
        rationale.append(f"⚠️ Sentiment misaligned with direction ({market_pulse}/100) — no bonus")

    # ----- Factor 6: Timeframe Alignment (4H vs 1D) -----
    # Confirm if signal direction matches the higher timeframe trends
    trend_matches = False
    if direction == "LONG" and trend_4h == "BULL" and trend_1d == "BULL":
        trend_matches = True
    elif direction == "SHORT" and trend_4h == "BEAR" and trend_1d == "BEAR":
        trend_matches = True

    if trend_matches:
        f_alignment = align_weight
        score      += f_alignment
        rationale.append(f"✅ Timeframe aligned — Signal {direction} matches 4H/1D trends (+{f_alignment}pts)")
    else:
        f_alignment = 0
        rationale.append(f"⚠️ Timeframe conflict — Signal {direction} lacks HTF alignment (4H:{trend_4h}, 1D:{trend_1d})")

    # ----- Final Score Normalization -----
    final_score = max(0, min(score, 100))
    strength    = get_strength_label(final_score)
    meter       = build_signal_meter(final_score)

    return {
        "symbol":           symbol,
        "direction":        direction,
        "score":            final_score,
        "strength":         strength,
        "meter":            meter,
        "rationale":        rationale,
        "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "components": {
            "trend_score":     max(f_trend, 0),
            "volume_score":    f_volume,
            "rsi_score":       f_rsi,
            "liq_score":       f_liq,
            "pulse_score":     f_pulse,
            "alignment_score": f_alignment,
        }
    }
