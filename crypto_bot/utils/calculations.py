from indicators.rsi import calculate_rsi

def calculate_bias_rating(rsi, price, sma50, trend_1d):
    """
    Determines market bias based on technical indicators.
    """
    bias = "NEUTRAL"
    if rsi >= 70:
        bias = "EXTREME_BULL"
    elif rsi <= 30:
        bias = "EXTREME_BEAR"
    elif price > sma50 and trend_1d == "BULL":
        bias = "BULLISH"
    elif price < sma50 and trend_1d == "BEAR":
        bias = "BEARISH"
    return bias

def calculate_score(test_data, pulse_score, liq_data, force_direction=None):
    """
    Delegates to the canonical scoring engine.
    Real confluence scoring logic lives in utils/scoring_engine.py.
    This function maintained for backwards compatibility only.
    """
    from utils.scoring_engine import calculate_score as _real_score
    return _real_score(test_data, pulse_score, liq_data, force_direction)

def calculate_market_pulse(snapshots, config):
    """
    Calculates overall market pulse score (0-100) and label.
    """
    score = 50
    total_rsi = 0
    total_vol = 0
    valid_count = 0
    
    for s in snapshots:
        if not s: continue
        valid_count += 1
        total_rsi += s["rsi"]
        total_vol += s["vol_ratio"]
        # Trend contribution
        if s["trend_emoji"] == "🟢": score += 5
        else: score -= 7
        # RSI contribution
        if s["rsi"] >= 70: score += 5
        elif s["rsi"] <= 30: score -= 5
        # 24h Change contribution
        score += int(s.get("change_24h", 0) * 2)

    avg_rsi = total_rsi / valid_count if valid_count > 0 else 50
    avg_volume = total_vol / valid_count if valid_count > 0 else 1.0

    powell_score = int(config.get("powell", {}).get("score", 3))
    score += (3 - powell_score) * 5
    
    score = max(0, min(100, score))
    label = "EXTREME FEAR" if score <= 25 else "FEAR" if score <= 40 else "NEUTRAL" if score <= 60 else "GREED" if score <= 80 else "EXTREME GREED"
    
    context = "Market is in an accumulation phase with neutral momentum."
    if score > 60 and avg_volume > 1.2: context = "Bullish momentum is building across major assets with strong volume."
    elif score > 60: context = "Bullish momentum is building, but volume remains average."
    elif score < 40 and avg_volume > 1.2: context = "Heavy selling pressure is dominant. Proceed with caution."
    elif score < 40: context = "Selling pressure is dominant. Watch for key support levels."
    elif avg_rsi > 65: context = "Market is overheating globally. Pullback likely."
    elif avg_rsi < 35: context = "Market is broadly oversold. Relief bounce possible."
    
    return {"score": score, "label": label, "context": context}
