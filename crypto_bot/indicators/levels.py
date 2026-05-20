def calculate_key_levels(
    highs: list, lows: list, percentile: int = 10
) -> dict:
    if len(highs) < 20 or len(lows) < 20:
        return {
            "support": min(lows[-10:]) if lows else 0,
            "resistance": max(highs[-10:]) if highs else 0,
        }
    sorted_lows = sorted(lows[-20:])
    sorted_highs = sorted(highs[-20:])
    idx = max(1, len(sorted_lows) * percentile // 100)
    return {
        "support": sorted_lows[idx],
        "resistance": sorted_highs[-(idx + 1)],
    }


def calculate_swing_levels(highs: list, lows: list) -> dict:
    """
    Calculates key levels using true swing pivot detection.
    Used by the Pro Digest — never uses min/max to avoid fake levels.

    Logic:
    - Swing high: higher than 2 candles before and after it
    - Swing low:  lower than 2 candles before and after it
    - Returns the most recent swing high as resistance
    - Returns the most recent swing low as support
    - Falls back to 10-candle high/low only if no swing pivots detected
    """
    # Detect swing highs
    swing_highs = [
        highs[i]
        for i in range(2, len(highs) - 2)
        if highs[i] > highs[i - 1]
        and highs[i] > highs[i - 2]
        and highs[i] > highs[i + 1]
        and highs[i] > highs[i + 2]
    ]

    # Detect swing lows
    swing_lows = [
        lows[i]
        for i in range(2, len(lows) - 2)
        if lows[i] < lows[i - 1]
        and lows[i] < lows[i - 2]
        and lows[i] < lows[i + 1]
        and lows[i] < lows[i + 2]
    ]

    # Most recent swing high = resistance, most recent swing low = support
    resistance = swing_highs[-1] if swing_highs else (max(highs[-10:]) if highs else 0.0)
    support    = swing_lows[-1]  if swing_lows  else (min(lows[-10:])  if lows  else 0.0)

    return {"support": support, "resistance": resistance}


def calculate_zone_buffer(
    highs: list, lows: list, closes: list, period: int = 14
) -> float:
    trs = []
    for i in range(1, min(period + 1, len(closes))):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else closes[-1] * 0.015
