def find_swing_lows(lows):
    if len(lows) < 6: return []
    return [lows[i] for i in range(2, len(lows)-2) if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]]

def find_swing_highs(highs):
    if len(highs) < 6: return []
    return [highs[i] for i in range(2, len(highs)-2) if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]]

def determine_structure(highs, lows):
    sh = find_swing_highs(highs)
    sl = find_swing_lows(lows)
    if not highs or not lows: return "🟡 Range"
    
    first_high = sh[0] if len(sh) > 1 else highs[0]
    last_high = sh[-1] if sh else highs[-1]
    first_low = sl[0] if len(sl) > 1 else lows[0]
    last_low = sl[-1] if sl else lows[-1]
    
    if last_high > first_high and last_low > first_low: return "🟢 Bullish"
    if last_high < first_high and last_low < first_low: return "🔴 Bearish"
    return "🟡 Range"


# ── Index-returning swing functions (divergence engine) ───────────────────────

def find_swing_low_indices(lows: list) -> list:
    """
    Same pivot logic as find_swing_lows but returns integer indices
    instead of price values. Used by the RSI divergence engine.
    """
    if len(lows) < 6:
        return []
    return [
        i for i in range(2, len(lows) - 2)
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2]
        and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]
    ]


def find_swing_high_indices(highs: list) -> list:
    """
    Same pivot logic as find_swing_highs but returns integer indices
    instead of price values. Used by the RSI divergence engine.
    """
    if len(highs) < 6:
        return []
    return [
        i for i in range(2, len(highs) - 2)
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2]
        and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]
    ]
