# 🚀 CRYPTON INTEL — FINAL MASTER PLAN (EXECUTION VERSION)
### Antigravity Implementation Document | Pre-Week 3 Structured Perfection

---

## 🛑 STRICT AGENT DIRECTIVES — READ BEFORE TOUCHING ANY FILE

These rules are non-negotiable. Follow them exactly in every stage.

1. **DO NOT modify the existing SQLite user/subscription database schema or user table under any circumstance.**
2. **SQLite stays for user data only.** Supabase is used exclusively for signal state, lifecycle state, streak tracking, and watch zone cooldowns.
3. **DO NOT switch or remove the existing asyncio.to_thread() SQLite implementation.**
4. **DO NOT introduce any paid APIs.** All new data must come from Binance public API (no authentication required) or existing free sources already in the stack.
5. **Gemini Flash is called ONCE per digest cycle total — shared output for all users. Never call Gemini per user or per command individually.** Cache the output and reuse it.
6. **Implement stages in exact order as defined below. Do not jump ahead or combine stages.**
7. **Do not remove any existing working functionality while adding new features.**
8. **Test each stage independently before moving to the next.**
9. **All new indicator calculations (RSI, EMA, MACD, BOS) must be computed locally in Python using pandas/numpy from Binance OHLCV data. Do not use paid indicator APIs.**
10. **Cooldown logic always takes priority over signal score. If cooldown is active, signal does not fire regardless of score.**

---

## 🧠 SYSTEM OBJECTIVE

Transform Crypton Intel from a basic signal notifier into a **Decision Intelligence Engine.**

The bot must answer exactly three questions every single day:

| Question | Engine | Audience |
|---|---|---|
| **"What is happening in the market right now?"** | DIGEST ENGINE | Free + Pro |
| **"Is something building that I should watch?"** | LIFECYCLE ENGINE | Free + Pro |
| **"Should I act on anything right now?"** | SIGNAL ENGINE | Free + Pro (different depth) |

---

## 🔒 FREE vs PRO PRODUCT POSITIONING

| Tier | Purpose | What They Get |
|---|---|---|
| **FREE** | Awareness | Context, interpretation, curiosity, upgrade triggers |
| **PRO** | Decision Clarity | Confluence, structure, key levels, decision guidance, "what to do now" |

---

## 🪙 COINS COVERED

BTC, ETH, SOL, XRP, ADA, LTC

All OHLCV data sourced from **Binance public API** — zero cost, no API key required.

Endpoint used: `GET https://api.binance.com/api/v3/klines`

Intervals needed: `1h`, `4h`, `1d`

---

## ⚙️ FULL ARCHITECTURE

```
CRYPTON INTEL
│
├── 📊 DIGEST ENGINE
│   ├── Scheduled: 9:30 AM IST daily
│   ├── Free Digest Format
│   └── Pro Digest Format
│
├── 📡 SIGNAL ENGINE
│   ├── Event-driven (runs every 30 minutes)
│   ├── Weighted confluence scoring
│   ├── BOS detection (close-based)
│   ├── Cooldown + Expiry logic
│   ├── Free Signal Output
│   └── Pro Signal Output
│
├── 🔄 LIFECYCLE ENGINE
│   ├── Persistent state via Supabase
│   ├── 5-state machine
│   ├── Reasoning text per state
│   └── Timestamp persistence across Railway restarts
│
├── 👁️ WATCH ZONE ALERTS
│   ├── Key level proximity detection (1.5% threshold)
│   ├── 6-hour cooldown per level per coin
│   ├── Free Alert Output
│   └── Pro Alert Output
│
├── 📅 WEEKLY RECAP ENGINE
│   └── Scheduled: Every Sunday 9:00 AM IST
│
└── 💬 COMMAND SYSTEM
    ├── /digest — manual digest trigger (Free + Pro)
    ├── /signal — latest signal check (Free + Pro)
    ├── /lifecycle — full state overview with reasoning (Free + Pro)
    ├── /explain [coin] — plain English breakdown (Pro only)
    ├── /levels [coin] — support and resistance levels (Pro only)
    ├── /bias — full daily market bias card (Pro only)
    ├── /weekly — weekly recap (Free + Pro)
    └── /status — bot health check (Free + Pro)
```

---

---

# 📊 STAGE 1 — DIGEST ENGINE REBUILD

## Objective
Restore Phase 1 digest quality and add new intelligence layers. This is the highest priority fix because the digest is what users see every single day. Current Phase 2 digest is worse than Phase 1 in every measurable way.

---

## Problems to Fix in Current Digest

1. **50-day MA trend comparison is missing** — was present in Phase 1, must be restored
2. **Volume % vs 20-day average is missing** — was present in Phase 1, must be restored
3. **Liquidations section is missing entirely** — was present in Phase 1, must be restored
4. **News severity labels are missing** — was present in Phase 1, must be restored
5. **Emoji bug** — coins showing 🟢 even when trend is BEARISH. The green/red emoji must reflect the actual 50-day MA trend, not the 24h price direction
6. **AI summary is weak and sometimes contradictory** — Gemini prompt must be rewritten with strict constraints
7. **Macro events dumped raw with no filtering** — must filter to HIGH impact only and explain why each event matters
8. **No human-readable status interpretation per coin** — must add a plain-English status line per coin

---

## New Additions to Digest

1. **Human-readable status label per coin** — e.g. "Cooling after strong move", "Weak — below key average", "Building momentum"
2. **"What to do now" decision layer** — Pro only, end of digest, 2-3 lines of plain guidance
3. **Pro teaser block** — Free digest ends with a locked Pro insight block to drive upgrades
4. **Macro events filtered to HIGH impact only** with a one-line explanation of why each matters
5. **Trader Read per coin** — Pro only, generated by single shared Gemini call, 3-4 lines max per coin, must mention specific price level
6. **Today's Market Bias section** — Pro only, generated by Gemini, overall direction + per-coin bias
7. **Streak display** — Pro only, shows how many consecutive days each coin has maintained its current trend direction
8. **Data sources footer** — restore at bottom of every digest

---

## Data Calculations Required for Digest

### 50-day MA Trend
```python
# Pull 1D klines, minimum 60 candles
# Calculate simple moving average of close prices over last 50 candles
ma_50 = close_prices[-50:].mean()
current_price = close_prices[-1]

if current_price > ma_50:
    trend = "BULLISH"
    trend_emoji = "🟢"
else:
    trend = "BEARISH"
    trend_emoji = "🔴"
```

### Volume vs 20-day Average
```python
# Pull 1D klines, minimum 25 candles
# Calculate average volume over last 20 candles
avg_volume_20d = volumes[-20:].mean()
current_volume = volumes[-1]
volume_change_pct = ((current_volume - avg_volume_20d) / avg_volume_20d) * 100

if volume_change_pct > 30:
    volume_label = "HIGH 🔵"
elif volume_change_pct < -20:
    volume_label = "LOW 🟡"
else:
    volume_label = "NORMAL ⚪"
```

### Status Interpretation Logic
```python
def get_coin_status(rsi, trend, volume_label, price_change_24h):
    if rsi >= 70:
        return "🔴 Overbought — potential cooling ahead"
    elif rsi <= 30:
        return "🟢 Oversold — watch for reversal"
    elif trend == "BULLISH" and price_change_24h < -1.0:
        return "🟡 Cooling after recent move"
    elif trend == "BEARISH" and volume_label == "HIGH 🔵":
        return "🔴 Selling pressure — elevated volume"
    elif trend == "BULLISH" and volume_label == "HIGH 🔵":
        return "🟢 Strong buying — volume confirming"
    elif trend == "BEARISH":
        return "🔴 Weak — below key average"
    else:
        return "⚪ Neutral — no strong pressure"
```

### Correct Emoji Logic (Bug Fix)
```python
# The 24h direction emoji (up/down arrow) is separate from the trend emoji (green/red circle)
# 24h arrow: based on price_change_24h only
arrow_emoji = "🔼" if price_change_24h >= 0 else "🔽"

# Trend circle: based on 50-day MA comparison only — NOT price_change_24h
trend_circle = "🟢" if current_price > ma_50 else "🔴"

# These two are independent and must never be confused
```

---

## Gemini Prompt — Locked Format

**This exact prompt structure must be used. Do not allow Gemini to respond freely without constraints.**

### Trader Read Prompt (per coin, called once total in one batch call):
```
You are a professional crypto market analyst. For each coin below, write exactly 3 lines of analysis.

Rules you must follow without exception:
- Every response must mention at least one specific price level (support or resistance)
- Every response must give a clear directional bias (bullish, bearish, or neutral with reason)
- Never use vague phrases like "watch the market" or "things could go either way"
- Never exceed 3 lines per coin
- Do not add disclaimers or caveats inside the Trader Read — those are added separately

Coins to analyze:
{coin_data_json}

Respond in this exact format for each coin:
COIN: [SYMBOL]
READ: [Line 1. Line 2. Line 3.]
```

### Market Bias Prompt (once per digest):
```
You are a professional crypto market analyst. Based on the market data below, write the Today's Market Bias section.

Rules:
- Overall bias must be one of: Risk-On, Risk-Off, or Neutral
- Give a one-line bias for each coin (BTC, ETH, SOL, XRP, ADA, LTC)
- For any coin you recommend avoiding, say "Avoid" and give a specific reason
- Mention at least one specific price level in your overall assessment
- Total response must be under 8 lines

Market data:
{market_data_json}
```

### Summary Prompt (once per digest):
```
You are a professional crypto market analyst. Write a 4-line market summary based on the data below.

Rules:
- Be specific — mention exact coin names and exact price levels
- Be contrarian when data warrants it — do not just repeat that everything is bullish
- If a coin's emoji shows bearish but it has a positive 24h change, explain the contradiction
- Never use filler phrases like "overall market shows mixed signals"
- Maximum 4 lines, no bullet points

Market data:
{market_data_json}
```

---

## 🆓 FREE DIGEST — Full Format

```
📊 CRYPTON INTEL — DAILY DIGEST
📅 [Weekday, Month Day, Year] | 9:30 AM IST
━━━━━━━━━━━━━━━━━━━━━

🌡️ Market Pulse: [LABEL] ([SCORE]/100)
📌 Context: [2-line plain English market context]
━━━━━━━━━━━━━━━━━━━━━

📈 COIN SNAPSHOT

[TREND_CIRCLE] #BTC — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

[TREND_CIRCLE] #ETH — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

[TREND_CIRCLE] #SOL — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

[TREND_CIRCLE] #XRP — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

[TREND_CIRCLE] #ADA — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

[TREND_CIRCLE] #LTC — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

━━━━━━━━━━━━━━━━━━━━━
💥 24H LIQUIDATIONS
Total: $[TOTAL] [[LEVEL_LABEL] [LEVEL_EMOJI]]
Longs: $[LONGS] | Shorts: $[SHORTS]
Composition: [LONG_PCT]% Longs | [SHORT_PCT]% Shorts
Read: [One line plain English interpretation of liquidation data]

━━━━━━━━━━━━━━━━━━━━━
📅 MACRO EVENTS TODAY
[Only show HIGH impact events. If none today, write "No high-impact macro events today."]
[IMPACT_EMOJI] [TIME] IST — [EVENT_NAME] ([IMPACT_LEVEL])
   Why it matters: [One line explanation of crypto market relevance]

━━━━━━━━━━━━━━━━━━━━━
📰 TOP HEADLINES
🔴 CRITICAL: [Headline] — [Source]
🟠 MODERATE: [Headline] — [Source]
🟢 LOW: [Headline] — [Source]

━━━━━━━━━━━━━━━━━━━━━
📊 MARKET SUMMARY
[4 lines from Gemini — specific, contrarian when needed, mentions price levels]

━━━━━━━━━━━━━━━━━━━━━
💎 PRO INSIGHT TODAY (LOCKED 🔒)
• [Coin] key support/resistance level identified
• [Coin] structure shift forming on [timeframe]
• Today's highest probability setup: [Coin]
• Full multi-timeframe bias and "What to do now" guidance

→ Type /upgrade to unlock full Pro analysis
━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
📡 Data: Binance | CoinGlass | TradingEconomics | CryptoNews
```

---

## 💎 PRO DIGEST — Full Format

```
💎 CRYPTON INTEL PRO — DAILY DIGEST
📅 [Weekday, Month Day, Year] | 9:30 AM IST
━━━━━━━━━━━━━━━━━━━━━

🌡️ Market Pulse: [LABEL] ([SCORE]/100)
📌 Context: [2-line plain English market context]
━━━━━━━━━━━━━━━━━━━━━

📈 COIN DEEP ANALYSIS

[TREND_CIRCLE] #BTC — $[PRICE] ([ARROW][CHANGE_PCT]% 24h)
   RSI (1D): [RSI_VALUE] | Trend: [TREND_LABEL] [TREND_CIRCLE] vs 50-day MA
   Volume: [VOLUME_LABEL] ([VOLUME_PCT]% vs 20-day avg)
   Status: [STATUS_LINE]

   🏗️ Structure:
   4H: [TREND_CIRCLE] [BOS_LABEL_4H] — [structure description]
   1D: [TREND_CIRCLE] [BOS_LABEL_1D] — [structure description]

   🎯 Key Levels:
   Support: $[S1] | $[S2]
   Resistance: $[R1] | $[R2]

   🔥 Streak: [TREND_DIRECTION] — Day [STREAK_COUNT]

   📖 Trader Read:
   [Line 1 from Gemini]
   [Line 2 from Gemini]
   [Line 3 from Gemini]

[Repeat same full block for ETH, SOL, XRP, ADA, LTC]

━━━━━━━━━━━━━━━━━━━━━
💥 24H LIQUIDATIONS
Total: $[TOTAL] [[LEVEL_LABEL] [LEVEL_EMOJI]]
Longs: $[LONGS] | Shorts: $[SHORTS]
Composition: [LONG_PCT]% Longs | [SHORT_PCT]% Shorts
Read: [One line plain English interpretation]

━━━━━━━━━━━━━━━━━━━━━
📅 MACRO EVENTS TODAY (HIGH IMPACT ONLY)
[IMPACT_EMOJI] [TIME] IST — [EVENT_NAME] ([IMPACT_LEVEL])
   Why it matters: [One line crypto relevance explanation]

━━━━━━━━━━━━━━━━━━━━━
📰 TOP HEADLINES
🔴 CRITICAL: [Headline] — [Source]
🟠 MODERATE: [Headline] — [Source]
🟢 LOW: [Headline] — [Source]

━━━━━━━━━━━━━━━━━━━━━
🧭 TODAY'S MARKET BIAS
Overall: [Risk-On / Risk-Off / Neutral]
BTC: [One-line bias with price level]
ETH: [One-line bias with price level]
SOL: [One-line bias with price level]
XRP: [One-line bias with price level]
ADA: [One-line bias with price level]
LTC: [One-line bias with price level]

━━━━━━━━━━━━━━━━━━━━━
📊 MARKET SUMMARY
[4 lines from Gemini — specific, contrarian when needed, mentions price levels]

━━━━━━━━━━━━━━━━━━━━━
🎯 WHAT TO DO NOW
[2-3 lines from Gemini. Must be actionable and specific.]
[Example: "No confirmed setup currently. Market in consolidation."]
[Example: "Wait for BTC reaction at $91,400 before assessing next move."]
[Example: "ETH showing early structure shift — lifecycle moving to WATCH."]

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
💎 Intelligence Tier Active
📡 Data: Binance | CoinGlass | TradingEconomics | CryptoNews
```

---

---

# 📡 STAGE 2 — SIGNAL ENGINE REBUILD

## Objective
Replace the single-factor RSI signal with a weighted multi-confluence signal system. Signals must be rarer, stronger, and fully explainable. Free and Pro users receive different depth of information.

---

## Data Sources (All Free, All Binance Public API)

| Indicator | Calculation | Binance Interval |
|---|---|---|
| RSI 14 | Wilder's smoothed RSI, 14-period | 1h, 4h, 1d |
| EMA 21 | Exponential moving average, 21-period | 4h, 1d |
| EMA 50 | Exponential moving average, 50-period | 4h, 1d |
| MACD | 12 EMA - 26 EMA, signal line 9 EMA | 4h, 1d |
| Volume Spike | Current volume vs 20-candle average | 1d |
| BOS | Close-based break of 6-candle swing high/low | 4h, 1d |
| Key Levels | Swing highs/lows from last 20 candles | 1d |

---

## Weighted Confluence Scoring System

This replaces the old binary 2/4 logic. BOS gets double weight because it is the strongest structural confirmation.

```python
def calculate_confluence_score(rsi_1h, rsi_4h, rsi_1d, bos_4h, bos_1d, volume_spike, macd_cross_4h):
    score = 0
    factors_hit = []

    # RSI Extreme Check — at least 2 of 3 timeframes must agree
    rsi_extreme_count = 0
    if rsi_1h <= 30 or rsi_1h >= 70:
        rsi_extreme_count += 1
    if rsi_4h <= 30 or rsi_4h >= 70:
        rsi_extreme_count += 1
    if rsi_1d <= 30 or rsi_1d >= 70:
        rsi_extreme_count += 1

    if rsi_extreme_count >= 2:
        score += 1
        factors_hit.append("RSI")

    # BOS Check — 4H confirmation
    if bos_4h in ["BULLISH_BOS", "BEARISH_BOS"]:
        score += 2  # Double weight — strongest factor
        factors_hit.append("BOS_4H")

    # BOS Check — 1D confirmation (additional weight if 1D also confirms)
    if bos_1d in ["BULLISH_BOS", "BEARISH_BOS"]:
        score += 1
        factors_hit.append("BOS_1D")

    # Volume Spike Check
    if volume_spike:  # True if current volume > 130% of 20-day avg
        score += 1
        factors_hit.append("VOLUME")

    # MACD Cross Check on 4H
    if macd_cross_4h:  # True if MACD line crossed signal line in last 2 candles
        score += 1
        factors_hit.append("MACD")

    return score, factors_hit

# SIGNAL FIRES ONLY IF: score >= 3
SIGNAL_THRESHOLD = 3
```

---

## BOS Detection — Close-Based (Critical Implementation)

**Rule: BOS must be confirmed by CLOSE price, not wicks. This prevents fake breakouts.**

```python
def detect_bos(highs, lows, closes):
    """
    Detects Break of Structure using close prices only.
    Looks back 6 candles (excluding current) for swing high/low.
    
    Parameters:
    - highs: list/array of high prices
    - lows: list/array of low prices  
    - closes: list/array of close prices
    
    Returns: "BULLISH_BOS", "BEARISH_BOS", or "NO_BOS"
    """
    if len(closes) < 8:
        return "NO_BOS"
    
    # Look at the 6 candles BEFORE the current candle (not including current)
    lookback_highs = highs[-7:-1]   # 6 candles back
    lookback_lows = lows[-7:-1]     # 6 candles back — MUST use lows, not highs
    
    swing_high = max(lookback_highs)
    swing_low = min(lookback_lows)
    
    current_close = closes[-1]
    
    # BOS confirmed only if CLOSE is beyond the swing level
    # Wicks that pierce through but close back inside do NOT count
    if current_close > swing_high:
        return "BULLISH_BOS"
    elif current_close < swing_low:
        return "BEARISH_BOS"
    else:
        return "NO_BOS"
```

---

## Signal Directional Alignment Rule

Before firing a signal, confirm that ALL factors are pointing in the same direction. Mixed signals should not fire.

```python
def check_directional_alignment(rsi_1d, bos_4h, bos_1d, macd_cross_4h):
    """
    Returns True only if all active factors agree on direction.
    Prevents contradictory signals (e.g. bullish RSI + bearish BOS).
    """
    directions = []

    if rsi_1d <= 30:
        directions.append("BULLISH")
    elif rsi_1d >= 70:
        directions.append("BEARISH")

    if bos_4h == "BULLISH_BOS":
        directions.append("BULLISH")
    elif bos_4h == "BEARISH_BOS":
        directions.append("BEARISH")

    if bos_1d == "BULLISH_BOS":
        directions.append("BULLISH")
    elif bos_1d == "BEARISH_BOS":
        directions.append("BEARISH")

    if not directions:
        return False, "NEUTRAL"

    # All directions must agree — no mixed signals
    if len(set(directions)) == 1:
        return True, directions[0]
    else:
        return False, "MIXED"
```

---

## Signal Rules Summary

| Rule | Value |
|---|---|
| Minimum score to fire | 3 out of maximum 6 |
| Directional alignment required | Yes — all factors must agree |
| Cooldown per coin | 4 hours — takes priority over score |
| Signal expiry | 4 hours after firing |
| Maximum signals per day across all coins | 3 total |
| Signal check frequency | Every 30 minutes |

---

## Cooldown Implementation

```python
def is_cooldown_active(coin, signal_state_db):
    """
    Checks Supabase for last signal time for this coin.
    Returns True if less than 4 hours have passed since last signal.
    Cooldown takes absolute priority over score.
    """
    last_signal_time = signal_state_db.get_last_signal_time(coin)
    if last_signal_time is None:
        return False
    
    elapsed_hours = (datetime.utcnow() - last_signal_time).total_seconds() / 3600
    return elapsed_hours < 4.0
```

---

## 🆓 FREE SIGNAL OUTPUT FORMAT

```
📡 CRYPTON SIGNAL — #[COIN]
📅 [DATE] | [TIME] IST

⚡ Condition: [RSI description — e.g. "RSI oversold on Daily (28.4)"]
📌 Bias: [BULLISH / BEARISH] watch developing

━━━━━━━━━━━━━━━━━━━━━
💎 Pro users receive:
• Full confluence breakdown ([X]/6 score)
• Multi-timeframe structure analysis
• Key support and resistance levels
• Decision guidance and expiry timer

→ Type /upgrade to unlock
━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

## 💎 PRO SIGNAL OUTPUT FORMAT

```
💎 CRYPTON PRO SIGNAL — #[COIN]
🕐 [TIME] IST | [DATE]

📊 Confluence Score: [LABEL] ([SCORE]/6)
━━━━━━━━━━━━━━━━━━━━━
[For each factor, show ✅ if hit or ⚪ if not hit:]
✅ RSI: [TIMEFRAMES] — [values and description]
✅ BOS 4H: [BULLISH/BEARISH] — broke swing [high/low] at $[LEVEL]
✅ Volume: +[PCT]% spike vs 20-day avg
⚪ MACD 4H: No cross yet — neutral
⚪ BOS 1D: Not yet confirmed
━━━━━━━━━━━━━━━━━━━━━
🏗️ Structure Context:
4H: [TREND_CIRCLE] [BOS label and description]
1D: [TREND_CIRCLE] [BOS label and description]

🎯 Key Levels:
Support: $[S1] | $[S2]
Resistance: $[R1] | $[R2]

📌 Bias: [BULLISH / BEARISH] setup [forming / confirmed]
⏱️ Signal expires: [TIME] IST ([X] hours from now)

🎯 What to do:
[2 lines — specific, actionable, mentions exact price level]
[Example: "Setup forming but not confirmed. Watch for hold above $91,400."]
[Example: "If price closes above $91,400 on 4H, setup strengthens significantly."]

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

## Confluence Score Label System

| Score | Label |
|---|---|
| 3/6 | MODERATE |
| 4/6 | STRONG |
| 5/6 | HIGH |
| 6/6 | EXTREME |

---

---

# 🔄 STAGE 3 — LIFECYCLE ENGINE REBUILD

## Objective
Fix the lifecycle system completely. Current state: all coins show NEUTRAL with empty timestamps because state is lost on every Railway restart. The lifecycle must be persistent, meaningful, and show clear reasoning for every state.

---

## Supabase Table: signal_lifecycle

```sql
CREATE TABLE signal_lifecycle (
    id SERIAL PRIMARY KEY,
    coin VARCHAR(10) NOT NULL,
    state VARCHAR(20) NOT NULL,
    reason TEXT,
    what_to_expect TEXT,
    confluence_score INTEGER DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    signal_fired_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(coin)
);
```

---

## Supabase Table: signal_history

```sql
CREATE TABLE signal_history (
    id SERIAL PRIMARY KEY,
    coin VARCHAR(10) NOT NULL,
    state VARCHAR(20) NOT NULL,
    confluence_score INTEGER,
    fired_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expired_at TIMESTAMP WITH TIME ZONE,
    developed BOOLEAN DEFAULT FALSE
);
```

---

## Supabase Table: streak_tracker

```sql
CREATE TABLE streak_tracker (
    id SERIAL PRIMARY KEY,
    coin VARCHAR(10) NOT NULL UNIQUE,
    current_trend VARCHAR(10) NOT NULL,
    streak_days INTEGER DEFAULT 1,
    streak_started TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## Supabase Table: watch_zone_cooldowns

```sql
CREATE TABLE watch_zone_cooldowns (
    id SERIAL PRIMARY KEY,
    coin VARCHAR(10) NOT NULL,
    level_price DECIMAL NOT NULL,
    level_type VARCHAR(20) NOT NULL,
    alerted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
);
```

---

## 5-State Lifecycle Machine

| State | Emoji | Meaning | When Applied |
|---|---|---|---|
| NEUTRAL | ⚪ | Nothing forming, monitoring | Default state, no conditions approaching |
| WATCH | 👁️ | Price near key level | Price within 3% of support or resistance |
| EARLY | ⚡ | Conditions building but unconfirmed | Score 1-2, not yet at threshold |
| CONFIRMED | ✅ | Signal fired, active | Score >= 3 fired, within expiry window |
| EXPIRED | 💤 | Signal closed cleanly | 4 hours passed since signal fired |

---

## State Transition Logic

```python
def update_lifecycle_state(coin, score, bos_4h, price, key_levels, signal_active, signal_expired):
    """
    Determines the correct lifecycle state and reasoning for a coin.
    Writes result to Supabase signal_lifecycle table.
    """
    if signal_active and not signal_expired:
        state = "CONFIRMED"
        reason = f"Signal active — confluence score {score}/6"
        what_to_expect = "Monitor for setup development. Signal expires in [X] hours."
    
    elif signal_expired:
        state = "EXPIRED"
        reason = "Previous signal closed. Setup did not extend further."
        what_to_expect = "Returning to neutral monitoring. Next cycle begins fresh."
    
    elif score >= 1 and score < 3:
        state = "EARLY"
        reason = f"Conditions building — score {score}/6, not yet confirmed"
        what_to_expect = "Monitoring for additional confluence. Signal threshold is 3/6."
    
    elif is_near_key_level(price, key_levels, threshold_pct=3.0):
        state = "WATCH"
        nearest_level = get_nearest_level(price, key_levels)
        level_type = "support" if nearest_level < price else "resistance"
        reason = f"Price approaching {level_type} ${nearest_level:,.0f}"
        what_to_expect = f"Watch for bounce or breakdown at this level. React carefully."
    
    else:
        state = "NEUTRAL"
        reason = "RSI neutral, no structure break, no level proximity"
        what_to_expect = "No setup forming. Monitoring continues every 30 minutes."
    
    # Write to Supabase
    supabase.table("signal_lifecycle").upsert({
        "coin": coin,
        "state": state,
        "reason": reason,
        "what_to_expect": what_to_expect,
        "confluence_score": score,
        "last_updated": datetime.utcnow().isoformat()
    }, on_conflict="coin").execute()
```

---

## /lifecycle Command Output Format

```
🔄 SIGNAL LIFECYCLE STATUS
📅 [Weekday, Month Day, Year] | [TIME] IST
━━━━━━━━━━━━━━━━━━━━━

⚪ #BTC — NEUTRAL
   [Reason: e.g. "RSI neutral + no structure break"]
   [What to expect: e.g. "Nothing forming. Monitoring continues."]
   Last updated: [TIME] IST

👁️ #ETH — WATCH
   [Reason: e.g. "Price approaching support $3,050"]
   [What to expect: e.g. "Watch for bounce or breakdown at this level."]
   Last updated: [TIME] IST

⚡ #SOL — EARLY
   [Reason: e.g. "RSI dropping on 4H, volume rising — score 2/6"]
   [What to expect: e.g. "Conditions building. Signal threshold is 3/6."]
   Last updated: [TIME] IST

✅ #XRP — CONFIRMED
   [Reason: e.g. "RSI oversold + Bullish BOS on 1D — score 4/6"]
   [What to expect: e.g. "Signal active. Monitor for development."]
   Signal fired: [TIME] IST
   Expires: [TIME] IST

💤 #ADA — EXPIRED
   [Reason: e.g. "Previous signal closed — setup did not extend"]
   [What to expect: e.g. "Returning to neutral. Next cycle begins fresh."]
   Closed at: [TIME] IST

⚪ #LTC — NEUTRAL
   [Reason: e.g. "No conditions met. Quiet across all timeframes."]
   [What to expect: e.g. "Nothing forming. Monitoring continues."]
   Last updated: [TIME] IST
━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

---

# 👁️ STAGE 4 — WATCH ZONE ALERTS

## Objective
Alert users when price approaches a meaningful support or resistance level. This keeps users engaged between signals and demonstrates the bot's value daily even when no full signal fires.

---

## Key Level Calculation

```python
def calculate_key_levels(highs, lows, closes, lookback=20):
    """
    Identifies support and resistance levels from recent price action.
    Uses swing highs and lows from the lookback period.
    Returns top 2 support levels and top 2 resistance levels.
    """
    current_price = closes[-1]
    
    # Find significant swing points
    swing_highs = []
    swing_lows = []
    
    for i in range(2, lookback - 2):
        idx = -(lookback - i)
        # Swing high: higher than 2 candles on each side
        if (highs[idx] > highs[idx-1] and highs[idx] > highs[idx-2] and
            highs[idx] > highs[idx+1] and highs[idx] > highs[idx+2]):
            swing_highs.append(highs[idx])
        
        # Swing low: lower than 2 candles on each side
        if (lows[idx] < lows[idx-1] and lows[idx] < lows[idx-2] and
            lows[idx] < lows[idx+1] and lows[idx] < lows[idx+2]):
            swing_lows.append(lows[idx])
    
    # Resistance: swing highs above current price
    resistance_levels = sorted([h for h in swing_highs if h > current_price])[:2]
    
    # Support: swing lows below current price
    support_levels = sorted([l for l in swing_lows if l < current_price], reverse=True)[:2]
    
    return {
        "support": support_levels,
        "resistance": resistance_levels
    }
```

---

## Watch Zone Trigger Logic

```python
def check_watch_zone(coin, current_price, key_levels, cooldown_db):
    """
    Fires watch zone alert if price is within 1.5% of any key level
    and no cooldown is active for that level.
    """
    PROXIMITY_THRESHOLD = 0.015  # 1.5%
    
    all_levels = (
        [(l, "support") for l in key_levels["support"]] +
        [(l, "resistance") for l in key_levels["resistance"]]
    )
    
    for level_price, level_type in all_levels:
        proximity = abs(current_price - level_price) / level_price
        
        if proximity <= PROXIMITY_THRESHOLD:
            # Check if cooldown is active for this level
            if not cooldown_db.is_active(coin, level_price):
                # Fire alert and set 6-hour cooldown
                cooldown_db.set_cooldown(coin, level_price, hours=6)
                return True, level_price, level_type, proximity
    
    return False, None, None, None
```

---

## 🆓 FREE WATCH ZONE ALERT FORMAT

```
👁️ WATCH ZONE ALERT — #[COIN]
🕐 [TIME] IST

Price approaching [support / resistance]: $[LEVEL_PRICE]
Currently at $[CURRENT_PRICE] ([PROXIMITY_PCT]% away)

React carefully at this level.

━━━━━━━━━━━━━━━━━━━━━
💎 Pro users receive full level context,
structure analysis and scenario planning.
→ /upgrade to unlock
━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

## 💎 PRO WATCH ZONE ALERT FORMAT

```
💎 WATCH ZONE ALERT — #[COIN]
🕐 [TIME] IST

Price approaching [support / resistance]: $[LEVEL_PRICE]
Currently at $[CURRENT_PRICE] ([PROXIMITY_PCT]% away)

━━━━━━━━━━━━━━━━━━━━━
🏗️ Why this level matters:
• [Reason 1 — e.g. "Previous swing low formed here on [DATE]"]
• [Reason 2 — e.g. "EMA 50 (1D) sitting at $[VALUE]"]
• [Reason 3 — e.g. "1D BOS origin point"]

📌 Current Confluence Score: [SCORE]/6 — [building / no setup]

📋 Scenarios:
Bounce here → [what it means, e.g. "bullish continuation likely"]
Break below → [what it means, e.g. "next support at $[NEXT_LEVEL]"]

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

---

# 📅 STAGE 5 — COMMANDS, WEEKLY RECAP, AND STREAK SYSTEM

## /explain [coin] Command — Pro Only

**Trigger:** Gemini call cached per digest cycle. If called again within same digest cycle, return cached response. New Gemini call only at next digest cycle.

**Output Format:**
```
📖 #[COIN] — PLAIN ENGLISH
[DATE] | [TIME] IST

[3-4 lines from Gemini. Must be written for a non-technical user.]
[Must explain what price is doing and why it matters in plain language.]
[Must mention at least one specific price level.]
[Must end with a clear "bottom line" statement.]

🎯 Bottom line: [One sentence. Clear directional statement or "no urgency" if neutral.]

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

**Gemini Prompt for /explain:**
```
Explain what is happening with [COIN] right now in plain English for a non-technical user.

Rules:
- Write exactly 4 lines
- Do not use technical jargon without immediately explaining it
- Mention at least one specific price level ($XX,XXX format)
- End with exactly one "Bottom line:" sentence that is direct and clear
- Do not hedge excessively — give a clear assessment

Current data:
Price: $[PRICE]
RSI (1D): [VALUE]
Trend vs 50-day MA: [BULLISH/BEARISH]
BOS 4H: [VALUE]
BOS 1D: [VALUE]
Key Support: $[S1]
Key Resistance: $[R1]
```

---

## /levels [coin] Command — Pro Only

**Output Format:**
```
🎯 KEY LEVELS — #[COIN]
[DATE] | [TIME] IST

📉 Support Levels:
   $[S1] — [why this level matters]
   $[S2] — [why this level matters]

📈 Resistance Levels:
   $[R1] — [why this level matters]
   $[R2] — [why this level matters]

📌 Current Price: $[PRICE]
Distance to nearest support: [PCT]%
Distance to nearest resistance: [PCT]%

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

## /bias Command — Pro Only

Returns the Today's Market Bias section from the most recent digest. No new Gemini call needed — reuse cached digest output.

**Output Format:**
```
🧭 TODAY'S MARKET BIAS
📅 [DATE] | Generated at 9:30 AM IST

Overall: [Risk-On / Risk-Off / Neutral]

BTC: [One-line bias with price level]
ETH: [One-line bias with price level]
SOL: [One-line bias with price level]
XRP: [One-line bias with price level]
ADA: [One-line bias with price level]
LTC: [One-line bias with price level]

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

## /status Command — All Users

**Output Format:**
```
⚙️ CRYPTON INTEL — SYSTEM STATUS
[DATE] | [TIME] IST

Bot: ✅ Online
Last digest: [TIME] IST ([X] hours ago)
Next digest: 9:30 AM IST tomorrow
Signal checks: Every 30 minutes
Signals fired today: [X] / 3 maximum
Supabase: ✅ Connected
Binance API: ✅ Reachable
Gemini API: ✅ Reachable

Coins monitored: BTC, ETH, SOL, XRP, ADA, LTC
```

---

## Weekly Recap — All Users

**Schedule:** Every Sunday at 9:00 AM IST automatically.
**Also available manually via /weekly command.**

**Output Format:**
```
📅 CRYPTON INTEL — WEEKLY RECAP
Week of [START_DATE] — [END_DATE]

━━━━━━━━━━━━━━━━━━━━━
📊 MARKET THIS WEEK
BTC: [START_PRICE] → [END_PRICE] ([PCT_CHANGE]%)
ETH: [START_PRICE] → [END_PRICE] ([PCT_CHANGE]%)
SOL: [START_PRICE] → [END_PRICE] ([PCT_CHANGE]%)
XRP: [START_PRICE] → [END_PRICE] ([PCT_CHANGE]%)
ADA: [START_PRICE] → [END_PRICE] ([PCT_CHANGE]%)
LTC: [START_PRICE] → [END_PRICE] ([PCT_CHANGE]%)

━━━━━━━━━━━━━━━━━━━━━
📡 SIGNALS THIS WEEK: [TOTAL COUNT] fired

[For each signal that fired:]
✅ #[COIN] [BULLISH/BEARISH] — [DAY] [TIME]
   Score: [X]/6 | Status: [Developed / Expired without move]

━━━━━━━━━━━━━━━━━━━━━
📌 KEY OBSERVATION THIS WEEK
[2-3 lines identifying the most notable market development of the week]

━━━━━━━━━━━━━━━━━━━━━
🔍 NEXT WEEK WATCH
[COIN]: [What to watch and why — specific price level]
[COIN]: [What to watch and why — specific price level]

━━━━━━━━━━━━━━━━━━━━━
⚠️ Market analysis only. Not financial advice.
```

---

## Streak System

**Logic:**
```python
def update_streak(coin, current_trend, streak_db):
    """
    Updates streak counter for a coin based on daily 50-day MA trend.
    Called once per digest cycle.
    """
    existing = streak_db.get(coin)
    
    if existing is None:
        # First time — initialise
        streak_db.set(coin, {
            "current_trend": current_trend,
            "streak_days": 1,
            "streak_started": datetime.utcnow()
        })
        return 1, current_trend
    
    if existing["current_trend"] == current_trend:
        # Trend continues — increment streak
        new_count = existing["streak_days"] + 1
        streak_db.update(coin, {"streak_days": new_count})
        return new_count, current_trend
    else:
        # Trend flipped — reset to Day 1
        streak_db.set(coin, {
            "current_trend": current_trend,
            "streak_days": 1,
            "streak_started": datetime.utcnow()
        })
        return 1, current_trend
```

**Display in Pro Digest:**
```
🔥 Streak: [BULLISH / BEARISH] — Day [COUNT]
```

If streak is Day 1 (just flipped), display:
```
🔄 Streak: [BULLISH / BEARISH] — Day 1 (trend just flipped)
```

---

---

# 💰 COMPLETE COST BREAKDOWN

| Component | Tool | Monthly Cost |
|---|---|---|
| Price + OHLCV data | Binance public API | ₹0 |
| RSI, EMA, MACD, BOS | Python pandas/numpy (local calc) | ₹0 |
| Key level calculation | Python from OHLCV | ₹0 |
| Liquidations | CoinGlass API | ₹0 |
| Macro events | TradingEconomics | Already paid |
| News + severity | CryptoNews/RSS | ₹0 |
| Gemini Flash | 1 call/day (shared all users) | Near ₹0 |
| Signal state persistence | Supabase free tier | ₹0 |
| Streak + cooldown storage | Supabase free tier | ₹0 |
| Bot hosting | Railway | Already paid |
| Telegram delivery | python-telegram-bot | ₹0 |

**Total new monthly cost: ₹0**

---

# 🗓️ IMPLEMENTATION ORDER — DO NOT SKIP OR REORDER

## Stage 1 — Digest Engine Rebuild
- [ ] Fix emoji bug (trend circle must reflect 50-day MA, not 24h direction)
- [ ] Restore 50-day MA trend calculation and display per coin
- [ ] Restore volume % vs 20-day average with label (HIGH/NORMAL/LOW)
- [ ] Restore liquidations section with composition and plain English read
- [ ] Restore news severity labels (🔴 CRITICAL / 🟠 MODERATE / 🟢 LOW)
- [ ] Add human-readable status line per coin using status logic defined above
- [ ] Filter macro events to HIGH impact only with one-line relevance explanation
- [ ] Rewrite Gemini prompt using exact locked format defined above
- [ ] Add Trader Read per coin to Pro digest (from shared Gemini call)
- [ ] Add Today's Market Bias section to Pro digest
- [ ] Add "What to do now" decision layer to Pro digest
- [ ] Add Pro teaser block to end of Free digest
- [ ] Restore data sources footer to both Free and Pro digest
- [ ] Restore "Intelligence Tier Active" footer to Pro digest

## Stage 2 — Signal Engine Rebuild
- [ ] Implement RSI calculation for 1H, 4H, 1D timeframes from Binance klines
- [ ] Implement EMA 21 and EMA 50 calculation for 4H and 1D
- [ ] Implement MACD calculation (12/26/9) for 4H
- [ ] Implement volume spike detection (>130% of 20-day average)
- [ ] Implement BOS detection using close-based logic defined above (lows for swing_low, highs for swing_high — not highs for both)
- [ ] Implement weighted confluence scoring system defined above
- [ ] Implement directional alignment check
- [ ] Implement 4-hour cooldown per coin (Supabase stored, survives restarts)
- [ ] Implement 4-hour signal expiry
- [ ] Implement 3 signal per day maximum across all coins
- [ ] Build Free signal output format
- [ ] Build Pro signal output format with full confluence breakdown
- [ ] Signal check loop running every 30 minutes

## Stage 3 — Lifecycle Engine Rebuild
- [ ] Create all 4 Supabase tables (signal_lifecycle, signal_history, streak_tracker, watch_zone_cooldowns)
- [ ] Implement state transition logic defined above
- [ ] Connect lifecycle updates to signal engine (runs after every signal check)
- [ ] Implement /lifecycle command with full reasoning output
- [ ] Verify timestamps survive Railway redeploy (they will because stored in Supabase, not memory)

## Stage 4 — Watch Zone Alerts
- [ ] Implement key level calculation from OHLCV swing highs/lows
- [ ] Implement 1.5% proximity trigger
- [ ] Implement 6-hour cooldown per level per coin (Supabase stored)
- [ ] Build Free watch zone alert format
- [ ] Build Pro watch zone alert format with level context and scenarios
- [ ] Update lifecycle state to WATCH when watch zone triggers

## Stage 5 — Commands, Weekly Recap, Streak System
- [ ] Implement /explain [coin] command with Gemini cached output (Pro only)
- [ ] Implement /levels [coin] command from calculated key levels (Pro only)
- [ ] Implement /bias command returning cached digest bias section (Pro only)
- [ ] Implement /weekly command output format
- [ ] Schedule weekly recap every Sunday 9:00 AM IST
- [ ] Implement streak tracking logic and Supabase storage
- [ ] Add streak display to Pro digest
- [ ] Implement /status command showing bot health

---

# ✅ FINAL PRODUCT DEFINITION

**Before:** A signal bot that sends RSI alerts nobody fully understands or trusts.

**After:** A market intelligence engine that answers three questions every day — what is happening, what is building, and what to do — backed by multi-factor confluence, structural analysis, and plain-English decision guidance.

**Free users** receive enough to understand the market and want more.

**Pro users** receive enough to make informed, structured decisions with confidence.

**That is what people pay for.**

---

*CRYPTON INTEL — FINAL MASTER PLAN v1.0*
*Approved for Antigravity implementation. Execute stages in exact order.*
*Total additional infrastructure cost: ₹0*
