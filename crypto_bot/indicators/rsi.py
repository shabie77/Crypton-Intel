def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(prices)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def classify_rsi_pro(rsi: float) -> tuple[str, str]:
    """
    7-tier RSI label system for Pro Digest.
    Returns (label, emoji).
    """
    if rsi >= 75:
        return "Overbought — pullback risk increasing", "🔴"
    elif rsi >= 65:
        return "Bullish but approaching overbought", "🟡"
    elif rsi >= 55:
        return "Bullish momentum building", "🟢"
    elif rsi >= 45:
        return "Neutral momentum", "⚪"
    elif rsi >= 35:
        return "Bearish pressure increasing", "🟠"
    else:
        return "Oversold — bounce risk increasing", "🔴"


# ── RSI Series + Divergence Detection ────────────────────────────────────────

def calculate_rsi_series(prices: list, period: int = 14) -> list:
    """
    Returns one RSI value per input price, aligned to the same indices.

    rsi_series[i]  →  RSI at candle index i
    First `period` values are None (insufficient history).
    rsi_series[-1] is always the most recent candle's real RSI.

    Bug-fix note: initial RSI (at index `period`) is computed and appended
    BEFORE the smoothing loop so the last candle is never accidentally None.
    """
    if len(prices) < period + 1:
        return [None] * len(prices)

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Seed averages from first `period` delta values
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # First `period` candles have no RSI
    rsi_values: list = [None] * period

    # RSI at index `period` — from the initial seed window
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    # Wilder smoothing for remaining candles
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values  # len == len(prices), perfectly aligned


def detect_rsi_divergence(
    highs: list,
    lows: list,
    rsi_series: list,
    swing_low_indices: list,
    swing_high_indices: list,
    min_gap: int = 5,
    min_pct: float = 0.005,
) -> dict | None:
    """
    Detects classic RSI divergence against pre-computed swing index lists.

    Returns:
        {
            "type":       "BULLISH" | "BEARISH",
            "swing1_idx": int,
            "swing2_idx": int,
            "price1":     float,
            "price2":     float,
            "rsi1":       float,
            "rsi2":       float,
        }
        or None if no divergence detected.
    """

    def _filter_valid_swings(indices: list, prices: list) -> list:
        """Apply gap + minimum price-move filter to ALL swings, including first."""
        valid = []
        for idx in indices:
            if not valid:
                # First swing: compare vs reference point 5 candles back
                ref_idx = max(0, idx - 5)
                ref = prices[ref_idx]
                pct = abs(prices[idx] - ref) / ref if ref != 0 else 0.0
                if pct >= min_pct:
                    valid.append(idx)
            else:
                gap_ok  = (idx - valid[-1]) >= min_gap
                ref     = prices[valid[-1]]
                pct_ok  = (abs(prices[idx] - ref) / ref) >= min_pct if ref != 0 else False
                if gap_ok and pct_ok:
                    valid.append(idx)
        return valid

    # ── Bullish: Price lower-low, RSI higher-low ──────────────────────────────
    valid_lows = _filter_valid_swings(swing_low_indices, lows)
    if len(valid_lows) >= 2:
        i1, i2 = valid_lows[-2], valid_lows[-1]
        r1, r2 = rsi_series[i1], rsi_series[i2]
        if r1 is not None and r2 is not None:
            if lows[i2] < lows[i1] and r2 > r1:
                return {
                    "type":       "BULLISH",
                    "swing1_idx": i1,
                    "swing2_idx": i2,
                    "price1":     lows[i1],
                    "price2":     lows[i2],
                    "rsi1":       round(r1, 2),
                    "rsi2":       round(r2, 2),
                }

    # ── Bearish: Price higher-high, RSI lower-high ────────────────────────────
    valid_highs = _filter_valid_swings(swing_high_indices, highs)
    if len(valid_highs) >= 2:
        i1, i2 = valid_highs[-2], valid_highs[-1]
        r1, r2 = rsi_series[i1], rsi_series[i2]
        if r1 is not None and r2 is not None:
            if highs[i2] > highs[i1] and r2 < r1:
                return {
                    "type":       "BEARISH",
                    "swing1_idx": i1,
                    "swing2_idx": i2,
                    "price1":     highs[i1],
                    "price2":     highs[i2],
                    "rsi1":       round(r1, 2),
                    "rsi2":       round(r2, 2),
                }

    return None
