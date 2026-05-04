import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.scoring_engine import calculate_score, build_signal_meter

print("=" * 60)
print("CRYPTON INTEL - SCORING ENGINE TEST SUITE")
print("=" * 60)

# ---------------------------------------------------------------------------
# TEST 1
# ---------------------------------------------------------------------------
print("\nTEST 1: Perfect Storm - Expect score >= 80")

perfect_data = {
    "symbol":       "BTC",
    "price":        77000.0,
    "sma50":        72000.0,
    "rsi":          22.0,
    "volume_ratio": 2.5,
    "trend_4h":     "BULL",
    "trend_1d":     "BULL",
}
result1 = calculate_score(
    perfect_data,
    market_pulse=25,
    liquidations={"longs": 30_000, "shorts": 400_000}
)

assert result1 is not None, "TEST 1 FAILED"
assert result1["score"] >= 80, f"TEST 1 FAILED: Score {result1['score']}"
print(f"PASS (Score: {result1['score']})")

# ---------------------------------------------------------------------------
# TEST 2
# ---------------------------------------------------------------------------
print("\nTEST 2: Weak Signal - Expect score < 55")
weak_data = {"symbol": "ETH", "price": 2300.0, "sma50": 2500.0, "rsi": 28.0, "volume_ratio": 0.7, "trend_4h": "BEAR", "trend_1d": "BULL"}
result2 = calculate_score(weak_data, market_pulse=52, liquidations={"longs": 30_000, "shorts": 45_000})
assert result2 is not None
assert result2["score"] < 55, f"TEST 2 FAILED: Score {result2['score']}"
print(f"PASS (Score: {result2['score']})")

# ---------------------------------------------------------------------------
# TEST 3
# ---------------------------------------------------------------------------
print("\nTEST 3: Neutral RSI - Expect None")
neutral_data = {"symbol": "SOL", "price": 88.0, "sma50": 80.0, "rsi": 55.0, "volume_ratio": 3.0, "trend_4h": "BULL", "trend_1d": "BULL"}
result3 = calculate_score(neutral_data, market_pulse=65, liquidations={"longs": 0, "shorts": 200_000})
assert result3 is None
print("PASS")

# ---------------------------------------------------------------------------
# TEST 4
# ---------------------------------------------------------------------------
print("\nTEST 4: Signal Meter Accuracy")
meter_cases = [(100, 5), (80, 4), (60, 3), (40, 2), (20, 1), (0, 0)]
all_passed = True
for score_val, expected_green in meter_cases:
    meter = build_signal_meter(score_val)
    green_count = meter.count("🟩")
    if green_count != expected_green: all_passed = False
    print(f"   Score {score_val:3d} -> Green blocks: {green_count} (Expected {expected_green})")
assert all_passed, "TEST 4 FAILED"
print("PASS")

# ---------------------------------------------------------------------------
# TEST 5
# ---------------------------------------------------------------------------
print("\nTEST 5: Liquidation Threshold")
liq_test_data = {"symbol": "XRP", "price": 1.20, "sma50": 1.00, "rsi": 25.0, "volume_ratio": 1.8, "trend_4h": "BULL", "trend_1d": "BULL"}
result5_low = calculate_score(liq_test_data, market_pulse=50, liquidations={"longs": 30_000, "shorts": 45_000})
assert result5_low["components"]["liq_score"] == 0
result5_high = calculate_score(liq_test_data, market_pulse=50, liquidations={"longs": 30_000, "shorts": 430_000})
assert result5_high["components"]["liq_score"] > 0
print("PASS")

print("\n" + "=" * 60)
print("ALL 5 TESTS PASSED - SCORING ENGINE VERIFIED")
print("=" * 60)
