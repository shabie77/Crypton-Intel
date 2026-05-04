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
