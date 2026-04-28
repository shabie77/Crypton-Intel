# Crypton Intel — Phase 2 Week 2: Signal Scoring Engine
**Status:** Approved — Execution Ready  
**Objective:** Transform Crypton Intel from a Data Broadcasting System into a Decision Intelligence Engine  
**Version:** Final Merged 10/10 (Base plan + 4 GPT upgrades integrated)

---

## Core Principle

Every alert that leaves this bot must pass through a scoring engine before it reaches any user. No exceptions. The engine evaluates six independent market factors, combines them into a Signal Confidence Score (0–100), and gates delivery based on score threshold. Free users and Pro users receive the same alerts — the difference is content depth, not access volume.

---

## Pre-Build Decisions (Locked)

**RSI Direction Trigger:** ≤ 30 = LONG signal, ≥ 70 = SHORT signal. Between 30–70 = no signal, return None immediately.

**RSI Scoring:** Two-tier. Full points for RSI ≤ 25 or ≥ 75. Half points for RSI ≤ 35 or ≥ 65. Zero otherwise.

**Volume Formula:** Log scale — `min(int(math.log(volume_ratio + 1) * 15), 25)`. Hard cap at 25 points regardless of weight adjustments. Log scale correctly rewards the first volume doubling more than subsequent ones.

**Liquidation Scope:** Per-coin only, last 30 minutes. Filter by symbol before passing to engine. Global 24h totals must never be used for per-coin scoring. Significance filter applied — delta of `abs(long_liq - short_liq)` must exceed $50,000 to count as aligned.

**Timeframe Alignment:** Sixth scoring factor worth 5 points. If 4H trend direction matches 1D trend direction, add 5 points. Passed in `symbol_data` as `trend_4h` and `trend_1d` strings ("BULL" or "BEAR").

**Threshold:** 55 minimum for any alert to broadcast. Both tiers receive alerts scoring ≥ 55. Content depth is the tier differentiator, not score threshold.

**Signal Meter:** 5 blocks, each block = 20 points. 🟩 filled, 🟨 partial (score mod 20 ≥ 10), ⬜ empty. Partial block correctly represents scores between block thresholds.

**Default Sensitivity:** 55. Admin-adjustable via `/setsensitivity` between 40 and 90 only. Values outside this range are rejected with an error message.

**Weight Distribution (6 factors, total = 100):**

| Factor | Base Weight | Notes |
|---|---|---|
| Trend Confluence | 25 | Dynamic ±5 based on market pulse |
| Volume Intensity | 20 | Log scale, hard cap 25pts |
| RSI Extremity | 20 | Dynamic ±5 based on market pulse, two-tier scoring |
| Liquidation Alignment | 15 | Per-coin 30min, $50k delta threshold |
| Market Pulse Bonus | 10 | Binary — matches direction or not |
| Timeframe Alignment | 5 | 4H vs 1D trend match |

**Per-factor scores stored in signal_history** for future Phase 3 backtesting and signal performance analysis.

---

## Part 1 — Database Schema (database.py)

### 1A — Add signal_history Table

Add inside `init_db()` after all existing table creations:

```sql
CREATE TABLE IF NOT EXISTS signal_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT,
    signal_type         TEXT,
    direction           TEXT,
    score               INTEGER,
    trend_score         INTEGER,
    volume_score        INTEGER,
    rsi_score           INTEGER,
    liq_score           INTEGER,
    pulse_score         INTEGER,
    alignment_score     INTEGER,
    strength            TEXT,
    rationale           TEXT,
    market_pulse        INTEGER,
    timeframe           TEXT,
    price_at_signal     REAL,
    price_after_1h      REAL,
    price_after_4h      REAL,
    outcome             TEXT,
    timestamp           TEXT
);
```

**Column explanations:**
- `signal_type` — "rsi", "liquidation", or "confluence"
- `direction` — "LONG" or "SHORT"
- `trend_score` through `alignment_score` — individual factor scores stored separately for backtesting
- `strength` — "HIGH CONVICTION", "STRONG", "MODERATE", or "WEAK"
- `rationale` — pipe-separated string of all rationale items
- `timeframe` — primary timeframe that triggered the signal (e.g. "4H")
- `price_after_1h` and `price_after_4h` — populated later by background updater for signal performance tracking
- `outcome` — "WIN", "LOSS", or "NEUTRAL" — populated after price data is filled

Add indexes immediately after table creation:

```sql
CREATE INDEX IF NOT EXISTS idx_signal_history_symbol ON signal_history(symbol);
CREATE INDEX IF NOT EXISTS idx_signal_history_timestamp ON signal_history(timestamp);
```

### 1B — Add bot_config Table

Add inside `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS bot_config (
    key     TEXT PRIMARY KEY,
    value   TEXT
);
```

Seed default sensitivity value immediately after:

```python
cursor.execute("""
    INSERT OR IGNORE INTO bot_config (key, value)
    VALUES ('signal_sensitivity', '55')
""")
```

### 1C — Add alert_cooldown Column Migration

The `alert_cooldown` table was created in Week 1 without a `cooldown_minutes` column. Add migration check inside `init_db()` after the table creation:

```python
cursor.execute("PRAGMA table_info(alert_cooldown)")
cooldown_cols = [info[1] for info in cursor.fetchall()]
if 'cooldown_minutes' not in cooldown_cols:
    cursor.execute("ALTER TABLE alert_cooldown ADD COLUMN cooldown_minutes INTEGER DEFAULT 15")
```

### 1D — Add log_signal() Function

Add new function in `database.py`. Accepts all per-factor scores for full analytics storage:

```python
def log_signal(symbol, signal_type, direction, score,
               trend_score, volume_score, rsi_score, liq_score,
               pulse_score, alignment_score, strength, rationale,
               market_pulse, timeframe, price_at_signal, timestamp):
    """
    Logs a generated signal to signal_history for performance tracking.
    Stores all per-factor scores individually for Phase 3 backtesting.
    """
    conn = _connect()
    try:
        conn.execute("""
            INSERT INTO signal_history (
                symbol, signal_type, direction, score,
                trend_score, volume_score, rsi_score, liq_score,
                pulse_score, alignment_score, strength, rationale,
                market_pulse, timeframe, price_at_signal, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, signal_type, direction, score,
              trend_score, volume_score, rsi_score, liq_score,
              pulse_score, alignment_score, strength, rationale,
              market_pulse, timeframe, price_at_signal, timestamp))
        conn.commit()
    except Exception as e:
        print(f"❌ log_signal error: {e}")
    finally:
        conn.close()
```

### 1E — Add get_sensitivity() and set_sensitivity() Functions

```python
def get_sensitivity():
    """Returns the current signal broadcast threshold. Default 55."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_config WHERE key = 'signal_sensitivity'")
        row = cursor.fetchone()
        return int(row[0]) if row else 55
    except Exception:
        return 55
    finally:
        conn.close()


def set_sensitivity(value):
    """Updates the signal broadcast threshold. Value must be 40–90."""
    conn = _connect()
    try:
        conn.execute("""
            INSERT INTO bot_config (key, value) VALUES ('signal_sensitivity', ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
        """, (str(value), str(value)))
        conn.commit()
    except Exception as e:
        print(f"❌ set_sensitivity error: {e}")
    finally:
        conn.close()
```

### 1F — Add get_coin_liquidations_30min() Function

Provides per-coin liquidation data filtered to last 30 minutes only:

```python
def get_coin_liquidations_30min(symbol):
    """
    Returns long and short liquidation totals for a specific coin
    in the last 30 minutes only. Used for per-coin scoring alignment.
    Symbol format: 'BTC', 'ETH' etc — matched with LIKE against full pair name.

    Side mapping (Binance forceOrder convention):
    - side 'SELL' = LONG position liquidated
    - side 'BUY'  = SHORT position liquidated
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                SUM(CASE WHEN side = 'SELL' THEN value ELSE 0 END) as long_liq,
                SUM(CASE WHEN side = 'BUY'  THEN value ELSE 0 END) as short_liq
            FROM liquidations
            WHERE symbol LIKE ? AND timestamp >= ?
        """, (f"%{symbol}%", cutoff))
        row = cursor.fetchone()
        if row and row[0]:
            return {"longs": float(row[0] or 0.0), "shorts": float(row[1] or 0.0)}
        return {"longs": 0.0, "shorts": 0.0}
    except Exception:
        return {"longs": 0.0, "shorts": 0.0}
    finally:
        conn.close()
```

### 1G — Add is_in_cooldown() and update_cooldown() Functions

```python
def is_in_cooldown(user_id, coin, alert_type):
    """
    Returns True if the user is within their cooldown window
    for this coin + alert_type combination. Returns False if no
    cooldown record exists (first alert is always allowed).
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT last_sent_at, cooldown_minutes
            FROM alert_cooldown
            WHERE user_id = ? AND coin = ? AND alert_type = ?
        """, (str(user_id), coin, alert_type))
        row = cursor.fetchone()
        if not row:
            return False

        last_sent_at = row[0]
        cooldown_minutes = row[1] if row[1] else 15

        cursor.execute("""
            SELECT 1 WHERE ? < datetime(?, '+' || ? || ' minutes')
        """, (now, last_sent_at, str(cooldown_minutes)))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def update_cooldown(user_id, coin, alert_type, cooldown_minutes=15):
    """
    Sets or updates the cooldown record for this user + coin + alert_type.
    Uses UPSERT to handle both first-time and repeat entries.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        conn.execute("""
            INSERT INTO alert_cooldown (user_id, coin, alert_type, last_sent_at, cooldown_minutes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, coin, alert_type)
            DO UPDATE SET last_sent_at = ?, cooldown_minutes = ?
        """, (str(user_id), coin, alert_type, now, cooldown_minutes, now, cooldown_minutes))
        conn.commit()
    except Exception as e:
        print(f"❌ update_cooldown error: {e}")
    finally:
        conn.close()
```

### 1H — Add get_user_pro_info() Function

Used by the Pro digest footer to fetch pro_since date:

```python
def get_user_pro_info(user_id):
    """Returns (tier, pro_since) tuple for a user. pro_since may be None."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tier, pro_since FROM users WHERE user_id = ?",
            (str(user_id),)
        )
        row = cursor.fetchone()
        if row:
            return row[0], row[1]
        return 'free', None
    except Exception:
        return 'free', None
    finally:
        conn.close()
```

---

## Part 2 — Scoring Engine (utils/scoring_engine.py) [NEW FILE]

Create new file at `utils/scoring_engine.py`. This is a completely standalone, pure-logic module. No database imports. No Telegram imports. No side effects. Takes data in, returns result dict or None.

```python
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

    Examples:
        score 100 → 🟩🟩🟩🟩🟩
        score 80  → 🟩🟩🟩🟩⬜
        score 65  → 🟩🟩🟩🟨⬜
        score 55  → 🟩🟩🟨⬜⬜
        score 20  → 🟩⬜⬜⬜⬜
        score 0   → ⬜⬜⬜⬜⬜
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

    Returns None if:
    - RSI is not in signal territory (between 30 and 70 exclusive)
    - Any required key is missing from symbol_data

    Returns dict with full scoring breakdown if a valid signal is detected.
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
    # Only proceed if RSI is in valid signal territory
    if rsi <= 30:
        direction = "LONG"
    elif rsi >= 70:
        direction = "SHORT"
    else:
        # RSI is neutral — no signal
        return None

    # ----- Initialise Score and Rationale -----
    score    = 0
    rationale = []

    # Per-factor score trackers (for signal_history storage)
    f_trend     = 0
    f_volume    = 0
    f_rsi       = 0
    f_liq       = 0
    f_pulse     = 0
    f_alignment = 0

    # ----- Dynamic Weight Adjustment Based on Market Pulse -----
    # Base weights — sum to 95 (timeframe alignment is always fixed at 5)
    trend_weight  = 25
    volume_weight = 20
    rsi_weight    = 20
    liq_weight    = 15
    pulse_weight  = 10
    align_weight  = 5   # Fixed — no dynamic adjustment

    # Adjust trend and RSI weights based on sentiment extremes
    if market_pulse > 70:
        # Extreme greed — trend signals more reliable, RSI less so
        trend_weight += 5
        rsi_weight   -= 5
    elif market_pulse < 30:
        # Extreme fear — RSI extremes more significant, trend less reliable
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
        # Counter-trend signal — apply penalty
        f_trend = -10
        score  += f_trend
        rationale.append(f"⚠️ Trend conflict — counter-trend signal (SMA50 opposes direction)")

    # ----- Factor 2: Volume Intensity (Log Scale) -----
    # Log scale: rewards first doubling more than subsequent doublings
    # Formula: min(int(log(volume_ratio + 1) * 15), 25)
    # Hard cap at 25 points regardless of dynamic weight
    raw_volume_pts = int(math.log(max(volume_ratio, 0.01) + 1) * 15)
    f_volume = min(raw_volume_pts, 25)
    score   += f_volume

    if volume_ratio >= 2.0:
        rationale.append(f"✅ Volume exceptional ({volume_ratio:.1f}x avg) — {f_volume}/25pts")
    elif volume_ratio >= 1.5:
        rationale.append(f"✅ Volume elevated ({volume_ratio:.1f}x avg) — {f_volume}/25pts")
    elif volume_ratio >= 1.0:
        rationale.append(f"🟡 Volume normal ({volume_ratio:.1f}x avg) — {f_volume}/25pts")
    else:
        rationale.append(f"⚠️ Volume weak ({volume_ratio:.1f}x avg) — {f_volume}/25pts")

    # ----- Factor 3: RSI Extremity (Two-Tier) -----
    if rsi <= 25 or rsi >= 75:
        # Extreme territory — full RSI weight
        f_rsi  = rsi_weight
        score += f_rsi
        rationale.append(f"✅ RSI extreme ({rsi:.1f}) — full {f_rsi}pts")
    elif rsi <= 35 or rsi >= 65:
        # Moderate extreme — half RSI weight
        f_rsi  = int(rsi_weight * 0.5)
        score += f_rsi
        rationale.append(f"🟡 RSI moderate extreme ({rsi:.1f}) — {f_rsi}/{rsi_weight}pts")
    else:
        # RSI mild zone — zero points
        f_rsi = 0
        rationale.append(f"⚠️ RSI mild zone ({rsi:.1f}) — 0pts")

    # ----- Factor 4: Liquidation Alignment (Per-Coin, Last 30 Min, $50k Delta) -----
    long_liq  = liquidations.get("longs", 0.0)
    short_liq = liquidations.get("shorts", 0.0)
    total_liq = long_liq + short_liq
    delta     = abs(long_liq - short_liq)

    if total_liq > 0 and delta >= LIQUIDATION_SIGNIFICANCE_THRESHOLD:
        # Determine which side is being liquidated more
        dominant_liq = "LONG" if long_liq > short_liq else "SHORT"

        # Alignment logic:
        # More LONG positions liquidated → bearish pressure → aligns with SHORT signal
        # More SHORT positions liquidated → bullish pressure → aligns with LONG signal
        liq_confirms_direction = (
            (direction == "LONG"  and dominant_liq == "SHORT") or
            (direction == "SHORT" and dominant_liq == "LONG")
        )

        if liq_confirms_direction:
            f_liq  = liq_weight
            score += f_liq
            dominant_label = "shorts" if dominant_liq == "SHORT" else "longs"
            rationale.append(
                f"✅ Liquidation aligned — {dominant_label} being cleared "
                f"(${delta:,.0f} delta, confirms {direction})"
            )
        else:
            f_liq = 0
            rationale.append(f"⚠️ Liquidation opposing — conflicting pressure (${delta:,.0f} delta)")
    elif total_liq > 0 and delta < LIQUIDATION_SIGNIFICANCE_THRESHOLD:
        f_liq = 0
        rationale.append(f"🟡 Liquidation insufficient delta (${delta:,.0f} < $50k threshold)")
    else:
        f_liq = 0
        rationale.append(f"🟡 No liquidation data in 30min window for {symbol}")

    # ----- Factor 5: Market Pulse Bonus -----
    if market_pulse > 70 and direction == "LONG":
        f_pulse = pulse_weight
        score  += f_pulse
        rationale.append(f"✅ Sentiment confirms LONG (Extreme Greed: {market_pulse}/100)")
    elif market_pulse < 30 and direction == "SHORT":
        f_pulse = pulse_weight
        score  += f_pulse
        rationale.append(f"✅ Sentiment confirms SHORT (Extreme Fear: {market_pulse}/100)")
    elif 40 <= market_pulse <= 60:
        f_pulse = 0
        rationale.append(f"🟡 Sentiment neutral ({market_pulse}/100) — no bonus")
    else:
        f_pulse = 0
        rationale.append(f"⚠️ Sentiment misaligned with direction ({market_pulse}/100) — no bonus")

    # ----- Factor 6: Timeframe Alignment (4H vs 1D) -----
    # Expected trend_4h and trend_1d values: "BULL" or "BEAR"
    if trend_4h == trend_1d:
        f_alignment = align_weight
        score      += f_alignment
        rationale.append(f"✅ Timeframe aligned — 4H and 1D both {trend_4h} (+{f_alignment}pts)")
    else:
        f_alignment = 0
        rationale.append(f"⚠️ Timeframe conflict — 4H {trend_4h} vs 1D {trend_1d} (0pts)")

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

        # Per-factor scores for signal_history storage
        "components": {
            "trend_score":     max(f_trend, 0),   # store 0 not negative for analytics
            "volume_score":    f_volume,
            "rsi_score":       f_rsi,
            "liq_score":       f_liq,
            "pulse_score":     f_pulse,
            "alignment_score": f_alignment,
        }
    }
```

---

## Part 3 — Test Suite (Run Before Any main.py Changes)

Create `test_scoring_engine.py` in the project root. Run all 5 tests. All must pass before proceeding to Part 4. Delete this file after passing.

```python
"""
Crypton Intel — Scoring Engine Test Suite
Phase 2 Week 2

Run: python test_scoring_engine.py
All 5 tests must pass before touching main.py.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.scoring_engine import calculate_score, build_signal_meter

print("=" * 60)
print("CRYPTON INTEL — SCORING ENGINE TEST SUITE")
print("=" * 60)

# ---------------------------------------------------------------------------
# TEST 1 — Perfect Storm (should score >= 85)
# ---------------------------------------------------------------------------
print("\n🧪 TEST 1: Perfect Storm — Expect score >= 85, Direction LONG, HIGH CONVICTION")

perfect_data = {
    "symbol":       "BTC",
    "price":        77000.0,
    "sma50":        72000.0,   # Price above SMA50 — trend aligned LONG
    "rsi":          22.0,       # RSI extreme (< 25) — full RSI points
    "volume_ratio": 2.5,        # 2.5x average — high volume (log scaled, capped at 25)
    "trend_4h":     "BULL",
    "trend_1d":     "BULL",    # Both timeframes aligned
}
# Fear sentiment (pulse=25) + SHORT liquidations dominant = confirms LONG
result1 = calculate_score(
    perfect_data,
    market_pulse=25,
    liquidations={"longs": 30_000, "shorts": 400_000}  # $370k delta > $50k threshold
)

assert result1 is not None, "❌ TEST 1 FAILED: Expected signal, got None"
assert result1["score"] >= 85, f"❌ TEST 1 FAILED: Score {result1['score']} < 85"
assert result1["direction"] == "LONG", f"❌ TEST 1 FAILED: Direction {result1['direction']} != LONG"
assert result1["strength"] == "HIGH CONVICTION", f"❌ TEST 1 FAILED: Strength {result1['strength']}"

print(f"✅ TEST 1 PASSED")
print(f"   Score: {result1['score']} | Strength: {result1['strength']}")
print(f"   Meter: {result1['meter']} {result1['score']}%")
print(f"   Components: {result1['components']}")
print(f"   Rationale:")
for r in result1["rationale"]:
    print(f"     {r}")


# ---------------------------------------------------------------------------
# TEST 2 — Weak Signal (score < 55 — should not broadcast)
# ---------------------------------------------------------------------------
print("\n🧪 TEST 2: Weak Signal — Expect score < 55")

weak_data = {
    "symbol":       "ETH",
    "price":        2300.0,
    "sma50":        2500.0,    # Price BELOW SMA50 — trend conflict for LONG
    "rsi":          28.0,       # LONG territory but only moderate extreme (35-30 tier = half points)
    "volume_ratio": 0.7,        # Below average — weak volume
    "trend_4h":     "BEAR",
    "trend_1d":     "BULL",    # Timeframe conflict
}
# Neutral sentiment + liquidation below threshold
result2 = calculate_score(
    weak_data,
    market_pulse=52,
    liquidations={"longs": 30_000, "shorts": 45_000}  # Only $15k delta — below threshold
)

if result2 is None:
    print("✅ TEST 2 PASSED: Returned None (RSI gate — should not happen at 28, checking...)")
    # RSI 28 is <= 30, so direction should be LONG — if None something else failed
    assert False, "❌ TEST 2 ERROR: RSI 28 should trigger LONG direction, not return None"
else:
    assert result2["score"] < 55, (
        f"❌ TEST 2 FAILED: Weak signal scored {result2['score']} — must be < 55 to be filtered"
    )
    print(f"✅ TEST 2 PASSED: Weak signal scored {result2['score']} (below 55 threshold — correctly filtered)")
    print(f"   Meter: {result2['meter']} {result2['score']}%")
    print(f"   Components: {result2['components']}")


# ---------------------------------------------------------------------------
# TEST 3 — Neutral RSI Gate (must return None)
# ---------------------------------------------------------------------------
print("\n🧪 TEST 3: Neutral RSI — Expect None returned")

neutral_data = {
    "symbol":       "SOL",
    "price":        88.0,
    "sma50":        80.0,
    "rsi":          55.0,       # RSI neutral — no signal territory
    "volume_ratio": 3.0,        # High volume — irrelevant, RSI gate fires first
    "trend_4h":     "BULL",
    "trend_1d":     "BULL",
}
result3 = calculate_score(
    neutral_data,
    market_pulse=65,
    liquidations={"longs": 0, "shorts": 200_000}
)

assert result3 is None, (
    f"❌ TEST 3 FAILED: Expected None for neutral RSI 55, got score {result3['score'] if result3 else 'N/A'}"
)
print("✅ TEST 3 PASSED: Neutral RSI 55 correctly returned None")


# ---------------------------------------------------------------------------
# TEST 4 — Signal Meter Format Verification
# ---------------------------------------------------------------------------
print("\n🧪 TEST 4: Signal Meter Accuracy")

meter_cases = [
    (100, "🟩🟩🟩🟩🟩"),
    (80,  "🟩🟩🟩🟩⬜"),
    (65,  "🟩🟩🟩🟨⬜"),
    (55,  "🟩🟩🟨⬜⬜"),
    (20,  "🟩⬜⬜⬜⬜"),
    (10,  "🟨⬜⬜⬜⬜"),
    (0,   "⬜⬜⬜⬜⬜"),
]

all_passed = True
for score_val, expected in meter_cases:
    meter = build_signal_meter(score_val)
    status = "✅" if meter == expected else "❌"
    if meter != expected:
        all_passed = False
    print(f"   {status} Score {score_val:3d} → {meter}  (expected {expected})")

assert all_passed, "❌ TEST 4 FAILED: One or more signal meter outputs incorrect"
print("✅ TEST 4 PASSED: All signal meters correct")


# ---------------------------------------------------------------------------
# TEST 5 — Liquidation Significance Threshold
# ---------------------------------------------------------------------------
print("\n🧪 TEST 5: Liquidation $50k Delta Threshold")

# Below threshold — should get 0 liq points
liq_test_data = {
    "symbol":       "XRP",
    "price":        1.20,
    "sma50":        1.00,
    "rsi":          25.0,       # Extreme RSI — LONG signal
    "volume_ratio": 1.8,
    "trend_4h":     "BULL",
    "trend_1d":     "BULL",
}

# Delta = $45k - $30k = $15k — below $50k threshold
result5_low = calculate_score(
    liq_test_data,
    market_pulse=50,
    liquidations={"longs": 30_000, "shorts": 45_000}
)
assert result5_low is not None, "❌ TEST 5a: Expected signal"
assert result5_low["components"]["liq_score"] == 0, (
    f"❌ TEST 5a FAILED: Liq score should be 0 for delta < $50k, got {result5_low['components']['liq_score']}"
)
print(f"   ✅ 5a: Below-threshold delta correctly scores 0 liq points")

# Delta = $400k — above $50k threshold, should score liq points
result5_high = calculate_score(
    liq_test_data,
    market_pulse=50,
    liquidations={"longs": 30_000, "shorts": 430_000}  # $400k delta, shorts dominant = confirms LONG
)
assert result5_high is not None, "❌ TEST 5b: Expected signal"
assert result5_high["components"]["liq_score"] > 0, (
    f"❌ TEST 5b FAILED: Liq score should be > 0 for delta > $50k, got {result5_high['components']['liq_score']}"
)
print(f"   ✅ 5b: Above-threshold delta correctly scores {result5_high['components']['liq_score']} liq points")
print("✅ TEST 5 PASSED: Liquidation significance threshold working correctly")


# ---------------------------------------------------------------------------
# FINAL SUMMARY
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("ALL 5 TESTS PASSED — SCORING ENGINE VERIFIED")
print("Proceed to Part 4: main.py alert pipeline integration")
print("=" * 60)
```

**Delete `test_scoring_engine.py` after all 5 tests pass.**

---

## Part 4 — Alert Pipeline Integration (main.py)

### 4A — Imports to Add at Top of main.py

```python
from utils.scoring_engine import calculate_score
import math  # if not already imported — needed for log scale reference awareness
```

### 4B — Modify RSI Scanner Loop

Locate the existing `rsi_scanner_loop` function. Find where RSI extreme conditions are detected and alerts are currently built and broadcast directly. Replace that section entirely with this scoring pipeline:

```python
# ============================================================
# PHASE 2 WEEK 2 — SCORING PIPELINE
# Replaces direct RSI alert broadcast
# ============================================================

# Determine 4H and 1D trend direction from existing SMA data
# "BULL" if price > sma50 on that timeframe, "BEAR" otherwise
trend_4h = "BULL" if current_price > sma50_4h else "BEAR"
trend_1d = "BULL" if current_price > sma50_1d else "BEAR"

# Build symbol_data dict for scoring engine
symbol_data = {
    "symbol":       coin,           # e.g. "BTC"
    "price":        current_price,  # float — current Binance price
    "sma50":        sma50_4h,       # float — 50-period SMA on 4H timeframe
    "rsi":          rsi_value,      # float — RSI value that triggered this check
    "volume_ratio": vol_ratio,      # float — current vol / 20-day avg
    "trend_4h":     trend_4h,       # "BULL" or "BEAR"
    "trend_1d":     trend_1d,       # "BULL" or "BEAR"
}

# Fetch per-coin liquidation data (last 30 minutes only — never global totals)
coin_liquidations = database.get_coin_liquidations_30min(coin)

# Fetch current market pulse (Fear & Greed index)
current_pulse = get_market_pulse()  # existing function — returns 0-100 int

# Run scoring engine — returns None if RSI not in signal territory
signal_result = calculate_score(symbol_data, current_pulse, coin_liquidations)

# RSI not in signal territory — skip silently
if signal_result is None:
    continue

score = signal_result["score"]
components = signal_result["components"]

# Fetch current broadcast threshold from DB (admin-adjustable)
sensitivity_threshold = database.get_sensitivity()

# Always log the signal regardless of threshold (for analytics)
database.log_signal(
    symbol=signal_result["symbol"],
    signal_type="rsi",
    direction=signal_result["direction"],
    score=score,
    trend_score=components["trend_score"],
    volume_score=components["volume_score"],
    rsi_score=components["rsi_score"],
    liq_score=components["liq_score"],
    pulse_score=components["pulse_score"],
    alignment_score=components["alignment_score"],
    strength=signal_result["strength"],
    rationale=" | ".join(signal_result["rationale"]),
    market_pulse=current_pulse,
    timeframe="4H",
    price_at_signal=current_price,
    timestamp=signal_result["timestamp"]
)

# Below threshold — logged but not broadcast
if score < sensitivity_threshold:
    print(f"🔇 Signal filtered: {coin} scored {score} (threshold {sensitivity_threshold}) — logged only")
    continue

# Above threshold — dispatch to users
await dispatch_signal_alert(session, signal_result, current_price)
```

### 4C — New Function: dispatch_signal_alert()

Add this new function in `main.py`. Handles tier-based formatting and delivery to all active users and channels:

```python
async def dispatch_signal_alert(session, signal_result, current_price):
    """
    Dispatches a scored and threshold-passed signal alert to all active users.
    Free users receive basic format — score meter visible, rationale hidden.
    Pro users receive full format — score meter + complete rationale + focus note.
    Cooldown is checked per-user before sending.
    """
    symbol    = signal_result["symbol"]
    score     = signal_result["score"]
    strength  = signal_result["strength"]
    direction = signal_result["direction"]
    meter     = signal_result["meter"]
    rationale = signal_result["rationale"]

    direction_emoji = "🟢" if direction == "LONG" else "🔴"
    direction_label = "LONG — Oversold Setup" if direction == "LONG" else "SHORT — Overbought Setup"

    # ---- FREE USER MESSAGE ----
    free_msg = (
        f"🚨 <b>{symbol} Signal Detected</b>\n\n"
        f"Direction: {direction_emoji} {direction_label}\n"
        f"Price: ${current_price:,.2f}\n\n"
        f"📊 Signal Confidence: {meter} {score}%\n"
        f"Strength: <b>{strength}</b>\n\n"
        f"<i>Upgrade to Pro for full signal breakdown,\n"
        f"rationale, and actionable focus note.</i>\n"
        f"/upgrade\n\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )

    # ---- PRO USER MESSAGE ----
    # Build rationale block
    rationale_lines = "\n".join([f"  {r}" for r in rationale])

    # Generate focus note based on direction and score
    if direction == "LONG" and score >= 80:
        focus = (
            "High conviction oversold setup. Watch for volume confirmation "
            "on the bounce. Key level: hold above recent low. "
            "Rejection from here invalidates the setup."
        )
    elif direction == "LONG":
        focus = (
            "Oversold condition detected. Wait for price to stabilize "
            "before acting. Confirm with next candle close. "
            "No position until structure holds."
        )
    elif direction == "SHORT" and score >= 80:
        focus = (
            "High conviction overbought setup. Watch for volume to dry up "
            "on the push higher. Key: rejection candle with high wick. "
            "Break above recent high invalidates the setup."
        )
    else:
        focus = (
            "Overbought condition detected. Look for momentum exhaustion "
            "before acting. Confirm with next candle close. "
            "Premature entries are the main risk here."
        )

    pro_msg = (
        f"🚨 <b>{symbol} Intelligence Signal</b>\n\n"
        f"Direction: {direction_emoji} {direction_label}\n"
        f"Price: ${current_price:,.2f}\n\n"
        f"📊 Signal Confidence: {meter} {score}%\n"
        f"Strength: <b>{strength}</b>\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Confluence Breakdown:</b>\n"
        f"{rationale_lines}\n\n"
        f"<b>Focus:</b>\n"
        f"{focus}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )

    # ---- DISPATCH TO USERS ----
    active_users = database.get_active_users()

    for user_id in active_users:
        # Check cooldown before sending to this user
        if database.is_in_cooldown(user_id, symbol, "signal"):
            continue

        tier = database.get_user_tier(user_id)

        try:
            if tier == "pro":
                await send_telegram_message(session, user_id, pro_msg)
                database.update_cooldown(user_id, symbol, "signal", cooldown_minutes=5)
            else:
                await send_telegram_message(session, user_id, free_msg)
                database.update_cooldown(user_id, symbol, "signal", cooldown_minutes=15)

        except Exception as e:
            print(f"❌ dispatch_signal_alert: Failed to send to user {user_id}: {e}")

    # ---- DISPATCH TO CHANNELS (free format) ----
    active_channels = database.get_active_channels()
    for channel_id in active_channels:
        try:
            await send_telegram_message(session, channel_id, free_msg)
        except Exception as e:
            print(f"❌ dispatch_signal_alert: Failed to send to channel {channel_id}: {e}")
```

---

## Part 5 — Admin Control Command (/setsensitivity)

Add inside `handle_admin_command` in `main.py`:

```python
if command == "/setsensitivity":
    if not args or not args.strip().isdigit():
        current = database.get_sensitivity()
        await send_telegram_message(
            session, chat_id,
            f"❌ Usage: /setsensitivity <number>\n"
            f"Valid range: 40–90\n\n"
            f"Current threshold: {current}/100\n\n"
            f"• 40 = Most alerts broadcast (low bar)\n"
            f"• 55 = Default balanced threshold\n"
            f"• 70 = Strict — strong signals only\n"
            f"• 90 = Very strict — near-perfect signals only"
        )
        return

    new_sensitivity = int(args.strip())

    if new_sensitivity < 40 or new_sensitivity > 90:
        await send_telegram_message(
            session, chat_id,
            f"❌ Invalid value: {new_sensitivity}\n"
            f"Must be between 40 and 90.\n"
            f"Tip: 55 is the recommended default."
        )
        return

    database.set_sensitivity(new_sensitivity)

    await send_telegram_message(
        session, chat_id,
        f"✅ Signal sensitivity updated.\n\n"
        f"New threshold: {new_sensitivity}/100\n"
        f"Signals scoring below {new_sensitivity} will be logged but not broadcast.\n\n"
        f"Use /setsensitivity without args to see current setting."
    )
```

---

## Part 6 — FOMO Footer + Pro Badge (Rank 9)

### Free Digest Footer

Add to the end of the free digest message generation function:

```python
# Fetch live Pro subscriber count
conn_tmp = database._connect()
pro_count_row = conn_tmp.execute(
    "SELECT COUNT(*) FROM users WHERE tier = 'pro' AND status = 'ACTIVE'"
).fetchone()
conn_tmp.close()
pro_count = pro_count_row[0] if pro_count_row else 0

fomo_footer = (
    f"\n━━━━━━━━━━━━━━━\n"
    f"💎 {pro_count} traders on Crypton Intel Pro are receiving\n"
    f"personalized analysis for their exact coins right now.\n"
    f"Join them → /upgrade"
)

# Append to digest message before sending
free_digest_message += fomo_footer
```

### Pro Digest Footer

Add to the end of the pro digest message generation:

```python
# Fetch user's pro_since date for personalized badge
tier, pro_since = database.get_user_pro_info(user_id)
pro_since_display = pro_since[:10] if pro_since else "N/A"  # Show date only, not time

pro_footer = (
    f"\n━━━━━━━━━━━━━━━\n"
    f"✅ Crypton Intel Pro — Active since {pro_since_display}"
)

pro_digest_message += pro_footer
```

---

## Part 7 — Verification Plan

### Step 1 — Run Test Suite
```bash
python test_scoring_engine.py
```
All 5 tests must pass. Fix any failures in `scoring_engine.py` before proceeding. Do not touch `main.py` until all 5 pass.

### Step 2 — Database Verification
```bash
python -c "
import database
database.init_db()
import sqlite3, os
db_path = os.getenv('DB_FILE', 'subscribers.db')
conn = sqlite3.connect(db_path)

# Verify all new tables exist
tables = [t[0] for t in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
print('Tables:', tables)
assert 'signal_history' in tables, 'MISSING: signal_history'
assert 'bot_config' in tables, 'MISSING: bot_config'
print('✅ All new tables present')

# Verify sensitivity functions
print('Default sensitivity:', database.get_sensitivity())
database.set_sensitivity(65)
print('Updated sensitivity:', database.get_sensitivity())
database.set_sensitivity(55)
print('Reset sensitivity:', database.get_sensitivity())
assert database.get_sensitivity() == 55
print('✅ Sensitivity functions working')

# Verify log_signal with all new columns
database.log_signal(
    symbol='BTC', signal_type='rsi', direction='LONG', score=87,
    trend_score=25, volume_score=20, rsi_score=20, liq_score=15,
    pulse_score=10, alignment_score=5, strength='HIGH CONVICTION',
    rationale='Trend aligned | RSI extreme | Volume exceptional',
    market_pulse=25, timeframe='4H', price_at_signal=77000.0,
    timestamp='2026-04-23 10:00:00'
)
row = conn.execute('SELECT * FROM signal_history ORDER BY id DESC LIMIT 1').fetchone()
print('Signal logged:', row)
assert row is not None
print('✅ log_signal working with all per-factor columns')

# Verify cooldown_minutes column exists
cols = [c[1] for c in conn.execute('PRAGMA table_info(alert_cooldown)').fetchall()]
assert 'cooldown_minutes' in cols, 'MISSING: cooldown_minutes column in alert_cooldown'
print('✅ alert_cooldown has cooldown_minutes column')

conn.close()
print('✅ ALL DATABASE CHECKS PASSED')
"
```

### Step 3 — Cooldown Function Test
```bash
python -c "
import database
database.init_db()

uid = 'test_verify_user_999'
print('In cooldown before set (expect False):', database.is_in_cooldown(uid, 'BTC', 'signal'))
database.update_cooldown(uid, 'BTC', 'signal', 15)
print('In cooldown after set (expect True):', database.is_in_cooldown(uid, 'BTC', 'signal'))
database.update_cooldown(uid, 'BTC', 'signal', 5)
print('In cooldown after 5min update (expect True):', database.is_in_cooldown(uid, 'BTC', 'signal'))
print('✅ Cooldown functions working correctly')
"
```

### Step 4 — Bot Startup Verification
```bash
python -c "import asyncio; from main import main; asyncio.run(asyncio.wait_for(main(), timeout=5))"
```

Expected clean startup — no new errors introduced:
```
🛠️ Initializing SQLite Database (Phase 2)...
✅ Database ready.
✅ Telegram Command Menu synchronized.
🤖 Telegram Polling Engine & Auto-Database is Live...
💧 Market-Wide Liquidation Engine is Live...
📡 Multi-Timeframe RSI Scanner is Live...
📈 Daily Digest Engine is Live...
📰 News Checker is Live...
🏛️ Macro Calendar Watcher is Live...
🧹 Database Maintenance Engine is Live...
🌐 Razorpay Webhook Server starting on port 8080...
✅ Connected to Market-Wide WebSocket successfully!
```

### Step 5 — Manual Tier Alert Verification

Temporarily set sensitivity to 0 via direct DB update to force next signal to broadcast regardless of score. Verify:
- Free user receives: meter shown, no rationale, upgrade prompt visible
- Pro user receives: meter shown, full rationale, focus note, no upgrade prompt
- Both messages include signal direction, price, and strength label
- Reset sensitivity to 55 immediately after verification

---

## Part 8 — Build Sequence (Strict — Do Not Deviate)

```
STEP 1 — database.py (all changes at once):
    → Add signal_history table with per-factor score columns
    → Add bot_config table with sensitivity seeded at 55
    → Add cooldown_minutes column migration to alert_cooldown
    → Add log_signal() with all per-factor parameters
    → Add get_sensitivity() and set_sensitivity()
    → Add get_coin_liquidations_30min()
    → Add is_in_cooldown() and update_cooldown()
    → Add get_user_pro_info()

STEP 2 — utils/scoring_engine.py (new file):
    → Create calculate_score() with all 6 factors
    → Create build_signal_meter() with partial block support

STEP 3 — Run test_scoring_engine.py:
    → All 5 tests must pass
    → Fix any failures before touching main.py
    → Do NOT proceed to Step 4 until all 5 pass

STEP 4 — main.py:
    → Add imports at top
    → Add dispatch_signal_alert() function
    → Modify rsi_scanner_loop to use scoring pipeline
    → Add /setsensitivity admin command
    → Add FOMO footer to free digest
    → Add Pro badge footer to pro digest

STEP 5 — Run all verification checks from Part 7 (Steps 1–5)

STEP 6 — Delete test_scoring_engine.py
```

---

## Files Modified in Week 2

| File | Change Type | Description |
|---|---|---|
| `database.py` | MODIFY | 2 new tables, 8 new functions, 1 column migration |
| `utils/scoring_engine.py` | CREATE | 6-factor scoring engine with log volume, timeframe alignment, $50k liq threshold |
| `main.py` | MODIFY | Scoring pipeline in RSI loop, dispatch function, admin command, digest footers |
| `test_scoring_engine.py` | CREATE → DELETE | 5-test verification suite — deleted after all pass |

---

## Completion Checklist

Before marking Week 2 complete and reporting back with output:

- [ ] All 5 scoring engine tests pass with correct scores
- [ ] signal_history table created with all per-factor columns
- [ ] log_signal() stores all 6 factor scores correctly
- [ ] bot_config table created with sensitivity = 55
- [ ] alert_cooldown table has cooldown_minutes column (migration applied)
- [ ] is_in_cooldown() returns False before set, True after
- [ ] get_coin_liquidations_30min() returns per-coin 30min data not global totals
- [ ] RSI scanner loop routes through scoring engine — no direct broadcast
- [ ] Signals below threshold are logged but not sent
- [ ] Free user alert: meter visible, rationale hidden, upgrade prompt shown
- [ ] Pro user alert: meter + full rationale + focus note, no upgrade prompt
- [ ] /setsensitivity rejects values outside 40–90 with error message
- [ ] /setsensitivity correctly persists new threshold to bot_config
- [ ] FOMO footer shows live Pro count in free digest
- [ ] Pro badge shows pro_since date in pro digest
- [ ] Bot starts clean with no errors after all changes
- [ ] test_scoring_engine.py deleted after all tests pass

**Report back with full terminal output of all 5 verification steps and complete test suite results before Week 3 begins.**

---

## Design Principle (Do Not Dilute)

This scoring system exists to make alerts rare and trustworthy. The temptation after launch will be to lower thresholds when alerts seem too infrequent. Resist this. A bot that fires 2 high-conviction signals per day is more valuable than one that fires 20 moderate signals. Selectivity is the product's edge.

---

*Crypton Intel Phase 2 Week 2 — Final Merged Execution Plan*  
*Base plan + GPT Upgrades: Log Volume Scale, Timeframe Alignment, Per-Factor Logging, Liquidation Delta Threshold*  
*April 2026*
