# Crypton Intel — Phase 2 Final Build Plan
**Status:** Approved — Ready for Development  
**Bot:** @CryptonIntel (Telegram)  
**Stack:** Python 3.10 (Async) · SQLite · Railway Cloud · Gemini Flash · Telethon  
**Version:** 2.0 Final

---

## Tier Structure

| Tier | Name | Price |
|---|---|---|
| 🆓 Free | Intel Feed | ₹0 |
| 💎 Pro | Decision Intelligence Layer | ₹249/month |

**Core Philosophy:**  
Free = Awareness. Useful enough to trust, limited enough to itch.  
Pro = Clarity. Every alert, digest, and flash filtered to what the user trades — with context that tells them what it means and what to watch next.  
The gap between tiers is not volume of information. It is **reduction of cognitive load.**

---

## Pre-Build Decisions (Locked)

### News Delivery
- Free users → headline sent to group broadcast channel
- Pro users → full Gemini-interpreted analysis sent via individual DM
- Single Telethon event triggers dual dispatch — one call, two outputs

### Digest Counter Reset
- Lazy evaluation — no cron job needed
- On each `/mydigest` call: compare stored date vs today's IST date
- If different → reset count to 0 and update date automatically

### Gemini Key Rotation
- Maintain `GEMINI_KEYS = [key1, key2, key3, key4, key5]` list in config
- Round-robin via index: `GEMINI_KEYS[current_index % len(GEMINI_KEYS)]`
- On 429 response → increment index → retry immediately with next key
- Never crashes the bot — fails silently to next key
- This utility is built ONCE and reused across every Gemini call in the codebase

---

## SQLite Schema (Full — Create All Tables at Project Start)

```sql
-- Core user registry
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    username    TEXT,
    tier        TEXT DEFAULT 'free',
    pro_since   DATE,
    pro_expiry  DATE
);

-- Coin watchlist per user
CREATE TABLE IF NOT EXISTS user_watchlist (
    user_id TEXT,
    coin    TEXT,
    PRIMARY KEY (user_id, coin)
);

-- Price alerts
CREATE TABLE IF NOT EXISTS price_alerts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT,
    coin                TEXT,
    target              REAL,
    direction           TEXT,       -- 'above' or 'below'
    is_active           INTEGER DEFAULT 1,
    is_paused           INTEGER DEFAULT 0,
    last_triggered_at   TIMESTAMP
);

-- On-demand digest daily usage counter
CREATE TABLE IF NOT EXISTS user_usage (
    user_id         TEXT PRIMARY KEY,
    date            TEXT,
    digest_count    INTEGER DEFAULT 0
);

-- Alert cooldown tracking (RSI + liquidation alerts)
CREATE TABLE IF NOT EXISTS alert_cooldown (
    user_id     TEXT,
    coin        TEXT,
    alert_type  TEXT,
    last_sent_at TIMESTAMP,
    PRIMARY KEY (user_id, coin, alert_type)
);

-- News deduplication store
CREATE TABLE IF NOT EXISTS news_seen (
    hash    TEXT PRIMARY KEY,
    seen_at TIMESTAMP
);

-- Weekly recap price snapshots
CREATE TABLE IF NOT EXISTS weekly_data (
    user_id             TEXT,
    week_start          DATE,
    coin                TEXT,
    open_price          REAL,
    close_price         REAL,
    alerts_triggered    INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, week_start, coin)
);
```

---

## Build Sequence (Week by Week)

```
Week 1 (Foundation):
    → Create full SQLite schema (all tables at once)
    → Build Gemini key rotation utility
    → Rank 1: Watchlist /mycoins
    → Rank 10: Razorpay payment gate (build in parallel)

Week 2 (Conversion Layer):
    → Rank 2: Price alerts + cooldown logic
    → Rank 5: Signal scoring system (alongside alerts)
    → Rank 9: FOMO footer + Pro badge (30 min job)

Week 3 (Intelligence Layer):
    → Rank 3: Dual digest 4H architecture
    → Rank 7: Macro deep explainer

Week 4 (Differentiation Layer):
    → Rank 4: Telethon news watcher
    → Rank 6: On-demand /mydigest
    → Rank 8: Weekly recap (Sunday)
```

---

## Rank 1 — Watchlist `/mycoins`

**Priority: Foundation. Build first. Everything else depends on this.**

### Commands
- `/mycoins BTC ETH SOL` — save watchlist (replaces existing)
- `/mycoins` — view current watchlist
- `/mycoins clear` — reset watchlist

### Limits
No limit on watchlist size for either tier. Selecting coins costs nothing and makes personalization feel generous.

### Logic
```
On any digest or alert render:
    fetch user watchlist from user_watchlist table

    if tier == 'pro':
        filter all outputs to watchlist coins only
        reorder: watchlist coins shown first with deeper context
        inject watchlist into Gemini prompt as priority coins

    if tier == 'free':
        ignore watchlist for content generation
        watchlist saved but has no effect on output
```

### Upsell Trigger
Fires immediately after a free user saves their watchlist:

> "✅ Watchlist saved — BTC, ETH, SOL.
> Upgrade to Pro and every digest, alert, and analysis
> will be built around only these coins. /upgrade"

### DB Operation
```sql
-- Save
DELETE FROM user_watchlist WHERE user_id = ?;
INSERT INTO user_watchlist (user_id, coin) VALUES (?, ?);

-- Fetch
SELECT coin FROM user_watchlist WHERE user_id = ?;
```

---

## Rank 2 — Price Alerts `/setalert` `/myalerts` `/deletealert`

**Priority: Strongest conversion lever. Hard Pro wall on set — show value, then block.**

### Commands
- `/setalert BTC 80000 above`
- `/setalert ETH 2200 below`
- `/myalerts` — list all active alerts with status
- `/deletealert BTC 80000` — remove specific alert

### Limits

| Tier | Active Alerts | Cooldown Per Coin |
|---|---|---|
| Free | 3 max | 15 minutes |
| Pro | 12 max | 5 minutes |

### Trigger Logic
```
Inside Binance WebSocket loop — check every 60 seconds:

    for each active, non-paused alert in price_alerts:
        fetch current price for alert.coin from Binance

        if direction == 'above' and current_price >= target:
            check alert_cooldown table for this user + coin + 'price_alert'
            if last_sent_at within cooldown window: skip silently
            else:
                fire alert message
                update last_triggered_at in price_alerts
                update last_sent_at in alert_cooldown

        if direction == 'below' and current_price <= target:
            same cooldown check and fire logic
```

### Alert Message Format

**Free:**
```
🔔 BTC crossed $80,000

Trend: Bullish 🟢
RSI (4H): 68
```

**Pro:**
```
🔔 BTC crossed $80,000

Context:
• RSI near overbought (68) — watch for exhaustion
• Trend bullish — price above 50MA
• Nearest resistance: $81,200

What to watch:
Does price hold above $80k on retest?
Breakout sustains → next target $83,500
Rejection here → pullback likely to $77,800
```

### Free User Hitting Limit (3/3)
Bot shows all 3 current alerts then:
> "Free limit reached (3/3). Delete one to replace it,
> or upgrade to Pro for up to 12 alerts with full context. /upgrade"

### Free User Attempting `/setalert`
Show a full sample Pro alert message first (so they see the experience), then:
> "Price alerts are a Pro feature. You're on Free tier.
> /upgrade to unlock 12 alerts with real-time context."

### Downgrade Behavior (Critical — Do Not Skip)
```
On pro_expiry date reached:
    fetch all alerts for user, ordered by id ASC
    alerts ranked 1–3:  keep is_active = 1, is_paused = 0
    alerts ranked 4–12: set is_paused = 1

    DO NOT DELETE any alerts

On re-upgrade (webhook fires):
    UPDATE price_alerts SET is_paused = 0 WHERE user_id = ?
    All 12 restore instantly — no re-entry by user needed
```

---

## Rank 3 — Dual Digest Architecture (4H Candle Schedule)

**Priority: Core monetization engine. One data fetch, two tier experiences.**

### Schedule (IST — Every 4H Candle Close)
`5:30 AM | 9:30 AM | 1:30 PM | 5:30 PM | 9:30 PM | 1:30 AM`

### Architecture
```
At each 4H candle close (cron job):

STEP 1 — Fetch market data ONCE (shared memory object):
    Binance REST: price, 24h change, volume per tracked coin
    RSI: 4H, 1D, 1W per coin (calculated from klines)
    CoinGlass: total liquidations, long/short split, tier breakdown
    Trading Economics: any macro event firing in next 4 hours
    Fear & Greed index (alternative.me — free API)

STEP 2 — Generate free digest:
    One Gemini call using standard broadcast prompt
    Same message sent to ALL free users (mass send — no loop)

STEP 3 — Generate Pro digests:
    Loop through Pro users:
        Fetch their watchlist
        Inject watchlist + shared data object into Pro Gemini prompt
        Generate personalized digest
        Send via individual DM
    (One Gemini call per Pro user per window)
```

**Key principle:** Data fetched once. Gemini called once for all free users combined, then once per Pro user individually. No redundant API calls.

### Free Digest Output Format
```
📊 CRYPTON INTEL — Market Update [TIME IST]

Fear & Greed: EXTREME GREED (79)

#BTC  $77,005 | 🔽 -0.09% | RSI 67.6 | Vol HIGH 🟢
#ETH  $2,405  | 🔽 -0.58% | RSI 64.1 | Vol HIGH 🟢
#SOL  $88.33  | 🔽 -0.48% | RSI 56.1 | Vol HIGH 🟢
#XRP  $1.49   | 🔼 +2.1%  | RSI 75.0 | Vol HIGH 🟢
#ADA  $0.61   | 🔽 -0.3%  | RSI 52.1 | Vol NORM ⚪

24H Liquidations: $15.9M (57% Longs | 43% Shorts)

Market View:
Bulls in control but RSI elevated across majors.
Watch for profit-taking near current highs.

━━━━━━━━━━━━━━━━━━━━━━━━
💎 17 traders on Pro are receiving personalized
analysis for their exact coins right now.
Join them → /upgrade
```

### Pro Digest Output Format
```
📊 YOUR INTEL — [TIME IST]
Your Coins: BTC • ETH

━━━━━━━━━━━━━━━
#BTC $77,005 | 🔽 -0.09%

Market State: Trending (Late Stage)
RSI: 67.6 (4H) | 71.2 (1D) — Elevated
Volume: +48% vs 20D avg — Confirming move

Key Levels:
  Resistance: $78,200 | $81,500
  Support:    $75,500 | $73,800

Risk:
RSI divergence forming on 4H.
Pullback probability increasing.

Focus:
Watch reaction at $78,200 resistance.
Breakout + hold → $81,500 next target.
Rejection → $75,500 retest likely.

━━━━━━━━━━━━━━━
#ETH $2,405 | 🔽 -0.58%

Market State: Bullish Recovery
RSI: 64.1 (4H) | 61.3 (1D) — Healthy range
Volume: +56% vs 20D avg — Strong

Key Levels:
  Resistance: $2,480 | $2,600
  Support:    $2,320 | $2,200

Risk: Low — RSI not extended
Focus: Clean structure. Watch $2,480
breakout for continuation signal.

━━━━━━━━━━━━━━━
⚠️ Macro: CPI release in 2h 30m
Impact: Expect volatility spike.
Secure open positions before release.

✅ Crypton Intel Pro — Active since Apr 1, 2026
```

---

## Rank 4 — News Intelligence Engine (Telethon Watcher)

**Priority: High differentiation feature. Build after Rank 1–3 are stable.**

### Architecture
```
Second Telegram account runs Telethon (user account — not bot)
Account joins all source channels manually via phone FIRST
Telethon session string stored as Railway environment variable
Runs silently 24/7 alongside main bot on same Railway service
```

### Source Channels (Join Manually First)
- WatcherGuru
- CoinGlass Alerts
- CoinMarketCap News
- Whale Alert
- CryptoQuant Alerts
- Wu Blockchain
- Lookonchain
- The Block
- Bitcoin Archive

New channels can be added at any time — just join via phone. No code changes needed.

### Filter Logic
```python
COIN_TICKERS = ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'BNB', 'USDT']

KEYWORDS = [
    'hack', 'exploit', 'ETF', 'SEC', 'Fed', 'FOMC',
    'liquidation', 'whale', 'breaking', 'critical',
    'crash', 'surge', 'ban', 'regulation', 'arrest',
    'partnership', 'launch', 'listing', 'delisting'
]

On each incoming channel message:

STEP 1 — Deduplication:
    news_hash = hash(message_text[:80])
    if hash exists in news_seen table:
        skip silently
    else:
        INSERT INTO news_seen (hash, seen_at) VALUES (?, NOW())
        continue to filter

STEP 2 — Relevance filter:
    message_upper = message_text.upper()
    if any ticker in message_upper OR any keyword in message_upper:
        pass to dispatch
    else:
        discard silently
```

### Dispatch Logic
```
STEP 3 — Free delivery:
    Send formatted headline to group broadcast channel
    No Gemini call — raw message only

STEP 4 — Pro delivery:
    Make one Gemini call with full message as context
    Prompt: extract impact, affected assets, what to watch
    Send interpreted analysis to each Pro user via individual DM
```

### Message Format

**Free (group broadcast channel):**
```
📡 Market Flash — WatcherGuru

MicroStrategy buys additional 5,000 BTC
at $76,800 average price.

#BTC
```

**Pro (individual DM):**
```
📡 Market Flash — WatcherGuru

MicroStrategy buys 5,000 BTC at $76,800 avg.

Impact:
Institutional accumulation signal.
Bullish for BTC medium-term sentiment.

Affected Assets:
BTC (primary) · ETH (secondary)

What to watch:
Price reaction in next 30–60 minutes.
BTC holds above $77k → further accumulation likely.
Watch for copycat buys from other institutions this week.
```

---

## Rank 5 — Smart Alert Signal Scoring System

**Priority: Quality control layer. Build alongside Rank 2 alert logic.**

Prevents alert fatigue. Ensures free users get moderate signals and Pro users get only high-confluence, high-quality signals.

### Score Formula
```python
score = 0

if RSI >= 70 or RSI <= 30:              score += 2   # Extreme territory
if volume > 30% above 20-day avg:       score += 2   # Abnormal volume
if liquidation_tier >= $50,000:         score += 2   # Significant liquidation
if trend_direction aligned with signal: score += 1   # Confirmation

# Max possible score: 7
```

### Thresholds

| Score | Action |
|---|---|
| 0–2 | Discard — signal too weak, send to nobody |
| 3–4 | Free tier alert — moderate signal |
| 5–7 | Pro priority alert — high-confluence signal only |

Pro users receive score 5–7 only. They never see weak 3–4 signals.  
Free users receive score 3–4. They never see what Pro quality looks like.

---

## Rank 6 — On-Demand Digest `/mydigest` *(Pro Only)*

**Priority: High perceived value. Low build effort. Reuses existing digest logic.**

### Limits

| Tier | Access | Daily Cap |
|---|---|---|
| Free | First watchlist coin only (teaser) | — |
| Pro | Full personalized digest | 5 per day |

### Reset Logic (No Cron Job Required)
```python
On each /mydigest call:

    row = SELECT * FROM user_usage WHERE user_id = ?

    if row.date != today_ist_date:
        UPDATE user_usage SET date = today, digest_count = 0
        row.digest_count = 0

    if row.digest_count >= 5:
        send: "Daily limit reached (5/5).
               Next scheduled digest at [next 4H close time].
               Cap resets at midnight IST."
        return

    generate_personalized_digest(user)
    UPDATE user_usage SET digest_count = digest_count + 1
```

### Free User Teaser
Generate analysis for their first watchlist coin only. Send it in full. Then:
> "Full on-demand digest covers all your watchlist coins.
> Upgrade to Pro for 5 pulls/day. /upgrade"

---

## Rank 7 — Macro Event Deep Explainer

**Priority: Zero new data cost. One extra Gemini call per macro event.**

Macro events are infrequent (1–3 per day max). API load is negligible.

### Free Format (Existing Behavior — No Change)
```
🏛️ MACRO ALERT

Event: CPI m/m
Country: USD
Time: 6:00 PM IST
Impact: HIGH 🔴

Watch: Expect volatility in BTC and ETH.
Secure positions before data drops.
```

### Pro Format (New — Additional Gemini Call)
```
🏛️ MACRO ALERT — CPI m/m (USD)

Time: 6:00 PM IST | Impact: HIGH 🔴
Impact Window: 1–2 hours post-release

Scenario Analysis:
CPI BEATS forecast (higher inflation):
  → DXY strengthens → BTC short-term pressure
  → Watch $75,500 support on BTC
  → ETH likely follows BTC lower

CPI MISSES forecast (lower inflation):
  → Risk-on sentiment → bullish for crypto
  → Watch for BTC push toward $78,200 resistance
  → ETH may outperform on miss

Your Coins (BTC · ETH):
Both sensitive to this release.
Recommend securing open positions before 6:00 PM IST.
```

### Implementation
```
In existing macro alert sender:

    send free version to all users (existing logic — no change)

    for each Pro user:
        fetch their watchlist
        call Gemini with:
            - event name, forecast, country
            - user watchlist coins
            - current price + RSI of their coins
        send Pro version via DM
```

---

## Rank 8 — Weekly Recap *(Pro Only — Sunday 9:00 AM IST)*

**Priority: Best retention feature. Users will not cancel once this is routine.**

One cron job added alongside the daily digest scheduler. One Gemini call per Pro user per week.

### Data Sources
```
- 7-day Binance klines (free endpoint, no cost)
- User's price_alerts table (alerts triggered this week)
- weekly_data table (prices stored each Sunday for comparison)
```

### Output Format
```
📊 YOUR WEEKLY RECAP
Week of Apr 14 – Apr 20, 2026
Your Coins: BTC • ETH

━━━━━━━━━━━━━━━
BTC Performance:
Open:  $74,200 → Close: $77,005 (+3.8%)
High:  $78,100 | Low: $73,800

Your Alerts This Week:
✅ BTC above $75,000 — triggered Apr 15 at 2:14 PM
✅ ETH below $2,300 — triggered Apr 14 at 9:31 AM
⏸ BTC above $80,000 — not yet triggered

━━━━━━━━━━━━━━━
ETH Performance:
Open:  $2,290 → Close: $2,405 (+5.0%)
High:  $2,480 | Low: $2,190

━━━━━━━━━━━━━━━
Weekly Insight:
BTC showed steady accumulation all week with two
resistance tests at $78,100. Buyers defended
$75,500 strongly — bullish structure intact.

ETH lagging BTC percentage-wise but structure
is clean. Possible catch-up move if BTC holds.

Next Week Focus:
$78,200 breakout confirmation is key for BTC.
Watch Monday open for direction. ETH $2,480
level is the decision point for the week.

✅ Crypton Intel Pro — Active since Apr 1, 2026
```

---

## Rank 9 — Pro Badge + FOMO Footer

**Priority: Trivial to build. High psychological impact. Build in Week 2.**

### Free Digest Footer
```
💎 [COUNT] traders on Crypton Intel Pro are receiving
personalized analysis for their exact coins right now.
Join them → /upgrade
```
Count: `SELECT COUNT(*) FROM users WHERE tier = 'pro'`  
Updates live on every digest send.

### Pro Digest Footer
```
✅ Crypton Intel Pro — Active since [pro_since]
```

---

## Rank 10 — Razorpay Payment Gate *(Build in Parallel — Week 1)*

**Priority: Non-negotiable. Nothing monetizes without this. Start Day 1.**

### Flow
```
1. User sends /upgrade
   Bot responds with:
   "💎 Upgrade to Crypton Intel Pro — ₹249/month
   Includes: Personalized digests, 12 price alerts,
   news analysis, weekly recap, and more.
   Pay here → [Razorpay link]
   (Include your Telegram ID: [user_id] in payment notes)"

2. User completes payment on Razorpay

3. Razorpay fires webhook to Railway endpoint:
   POST /razorpay-webhook on Flask/FastAPI app

4. Webhook handler:
   a. Validate Razorpay signature (mandatory — reject if invalid)
   b. Extract user_id from payment notes/reference field
   c. UPDATE users SET
          tier = 'pro',
          pro_since = TODAY,
          pro_expiry = TODAY + 30 days
      WHERE user_id = extracted_user_id
   d. Un-pause all alerts: UPDATE price_alerts SET is_paused = 0
   e. Send confirmation DM to user:
      "✅ Pro activated! Welcome to Crypton Intel Pro.
       Your personalized digest starts at the next 4H close.
       All features are now unlocked."
```

### Expiry Handling
```
Daily cron job at midnight IST:

    expired_users = SELECT user_id FROM users
                    WHERE tier = 'pro' AND pro_expiry < TODAY

    for each expired_user:
        UPDATE users SET tier = 'free'
        Pause alerts 4–12: UPDATE price_alerts SET is_paused = 1
                           WHERE user_id = ? AND id NOT IN (
                               SELECT id FROM price_alerts
                               WHERE user_id = ? ORDER BY id ASC LIMIT 3
                           )
        Send DM: "⚠️ Your Pro access has expired.
                  Your watchlist and alert history are saved.
                  Renew anytime to restore full access. /upgrade"
```

### Renewal Reminder
```
At midnight IST daily — also check:

    expiring_soon = SELECT user_id FROM users
                    WHERE tier = 'pro'
                    AND pro_expiry = TODAY + 3 days

    for each user:
        Send DM: "⚠️ Your Pro access expires in 3 days.
                  Renew to keep your watchlist, alerts,
                  and weekly recap active. /upgrade"
```

### Data Preservation on Downgrade
All watchlist data, alert history, and weekly recap history is **preserved on downgrade. Nothing is deleted.** Re-activation restores full state instantly.

---

## Deferred to Phase 3

### Key Levels Engine `/levels`
Requires full calculation specification before building:
- Which timeframe(s) to calculate on
- How many candles back to look
- Swing high/low detection logic (what separates a valid swing from noise)
- Volume cluster definition method

Risk of inaccurate output if built without complete spec. Inaccurate levels destroy user trust. Defer.

### `/current_market` Command
Redundant with digest in current form. Can be reconsidered in Phase 3 as a no-AI instant raw snapshot — price + RSI + liquidation pulled live with zero Gemini call, no daily limit, available to both tiers as a quick reference command.

---

## Feature Summary Table

| Rank | Feature | Free Tier | Pro Tier | Build Week |
|---|---|---|---|---|
| 1 | Watchlist `/mycoins` | Save only — no effect on output | Filters all output to watchlist | Week 1 |
| 2 | Price Alerts `/setalert` | 3 max · 15min cooldown · basic format | 12 max · 5min cooldown · full context | Week 2 |
| 3 | Dual Digest (4H schedule) | Broadcast — same for all | Personalized per watchlist | Week 3 |
| 4 | News Watcher (Telethon) | Headline in group channel | Gemini analysis via DM | Week 4 |
| 5 | Signal Scoring | Score 3–4 signals only | Score 5–7 signals only | Week 2 |
| 6 | On-Demand `/mydigest` | First coin teaser only | 5 pulls/day · full digest | Week 4 |
| 7 | Macro Deep Explainer | Basic alert (existing) | Scenario analysis + timing | Week 3 |
| 8 | Weekly Recap (Sunday) | ❌ Not available | Full recap + next week focus | Week 4 |
| 9 | FOMO Footer + Pro Badge | Live Pro count in footer | "Active since" badge | Week 2 |
| 10 | Razorpay Payment Gate | /upgrade command | Webhook → auto activation | Week 1 (parallel) |

---

## Strategic Position (Final)

**Free = Awareness**  
Useful enough to trust. Limits visible enough to motivate.

**Pro = Clarity**  
Personalized, structured intelligence — filtered to what the user trades, with context that reduces decision time from minutes to seconds.

**The goal:** A trader on Pro should be able to read their digest in under 10 seconds and know exactly what their coins are doing, where the risk is, and what to watch next. That clarity is worth ₹249/month.

---

*Crypton Intel Phase 2 — Approved Build Plan*  
*Prepared for Antigravity handover*  
*April 2026*
