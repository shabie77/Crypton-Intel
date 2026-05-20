def calculate_ema(closes: list, period: int) -> float:
    """
    Calculates the Exponential Moving Average for the given period.
    Returns the final EMA value. Pure Python, no external libraries.
    Returns the last close price if not enough data.
    """
    if not closes or len(closes) < period:
        return closes[-1] if closes else 0.0

    multiplier = 2 / (period + 1)

    # Seed with SMA of first `period` values
    ema = sum(closes[:period]) / period

    # Apply EMA formula for remaining values
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return ema
