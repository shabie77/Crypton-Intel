# [Goal Description]

Update the Crypto Analyst Bot to become a comprehensive "Analysis Platform" rather than just a signal bot. This plan incorporates the initial HTML plan's vision while addressing critical technical flaws (Cloudflare blocking scrapers, JS rendering on CoinGlass, mismatched F&G timing, and incompatible tech stacks). It also integrates new operational commands for the admin to manage subscribers and broadcast news efficiently.

## User Review Required

> [!IMPORTANT]
> **Data Sources Shift:** 
> - **Macro Calendar:** Instead of scraping Forex Factory (which will fail due to Cloudflare), we will use an RSS feed (like Investing.com) or a free API.
> - **ETF Data:** Since CoinGlass uses heavy JS rendering, we will start with a manual `/update_etf` command for testing, similar to the Powell tone update.
> - **Fear & Greed:** The external API has bad timing. We will calculate our own internal "Market Pulse Score" based on RSI, Volume, and Trend.

> [!WARNING]
> **Codex Instructions:**
> When you give tasks to Codex, you must explicitly declare:
> `"Do NOT use requests or pandas or python-telegram-bot. Use ONLY aiohttp for async network calls and raw Python math. Everything MUST fit into the existing asyncio event loop structure."`

## Proposed Changes

The plan is divided into 5 distinct phases to keep Codex tasks manageable.

### [Phase 1: Database Expansion & Admin Commands]

Modify the core structure to handle new commands, better user tracking, and alert logging (for future Analysis Replay).

#### [MODIFY] `database.py`
- Add a new table `users` to track private direct messages (DMs).
- Add a new table `alerts_log` with schema: `(id, alert_type, symbol, content, timestamp)`.
- Update `init_db()` to ensure these tables are created.
- Add getter functions: `get_all_users()`, `get_all_channels()`, `log_alert()`.

#### [MODIFY] `main.py`
- Add handler for `/start` in DMs to register users in the `users` table.
- Implement new admin commands (restricted to Admin ID):
  - `/channels`: Returns list of active channels.
  - `/users`: Returns list of active users.
  - `/send <message>`: Broadcasts an alert to all channels and users.
  - `/status`: Returns basic bot uptime/health string.
  - `/help`: Returns available commands based on user role.

---

### [Phase 2: External Configuration Management]

Separate manual inputs (Powell tone, ETF flows) from code deployment.

#### [NEW] `config_manager.py` (or integrated into `main.py`)
- Create logic to read/write a `config.json` stored in the Railway volume (`RAILWAY_VOLUME_MOUNT_PATH`).
- Handle file creation if it doesn't exist.
- Add asyncio locks or safe read/write to prevent corruption.

#### [MODIFY] `main.py`
- Add command `/update_powell <stance> <score> <note>` to update config.
- Add command `/update_etf <inflow/outflow string>` to update config.
- Add `/update_coins <add/remove> <symbol>` to manage tracked coins.

---

### [Phase 3: Data Collection & Scoring (Internal Data)]

Enhance RSI checking and build the internal "Market Pulse Score".

#### [MODIFY] `main.py`
- Refine `rsi_scanner_loop`:
  - Fetch volume data alongside price.
  - Enforce volume confirmation: trigger alert ONLY if RSI is extreme (>75 or <25) AND volume is meaningfully above average.
  - Enforce a 6-hour cooldown per coin per timeframe (using state tracking on volume).
- Create `calculate_market_pulse()` function:
  - Consolidate RSI state, trend direction, and manual ETF/Powell data into a 0-100 score.
  - Remove external alternative.me F&G API call.

---

### [Phase 4: External Data APIs (News & Macro)]

Integrate third-party data using async-friendly methods.

#### [MODIFY] `main.py`
- Add `news_checker_loop`:
  - Fetch CryptoCompare News API (or CoinTelegraph RSS via `aiohttp`).
  - Simple keyword filter to identify "Breaking/Critical" news.
  - Broadcast high-priority news with deduplication (store last sent URL).
- Add `macro_calendar_loop`:
  - Fetch an RSS-based economic calendar (e.g., Investing.com RSS).
  - Parse events occurring within the next 2 hours.
  - Broadcast warnings for high-impact events.

---

### [Phase 5: Digest Assembly & Testing]

Refine the morning digest to include all the new data layers and deploy.

#### [MODIFY] `main.py`
- Transform `daily_digest_loop`:
  - Shift time to 03:30 UTC for optimal Indian morning delivery (9:00 AM IST).
  - Build a comprehensive formatted string incorporating:
    - Coin prices, 24h change, RSI, Volume status.
    - Market Pulse Score (0-100).
    - Powell Tone (from config).
    - ETF Flows (from config).
  - Add graceful degradation: if an API fails, insert "Data unavailable" rather than failing the whole digest.
- Ensure all loops wrap errors and optionally DM the Admin ID on failure (Error Alerting).

## Open Questions

- We will proceed with `/update_etf` manually for now to bypass scraper issues. Are you okay with spending ~30 seconds a day to update ETF data manually until we secure a reliable free API?
- Do you want to enforce the exact 5 coins (BTC, ETH, SOL, XRP, ADA) immediately, or keep the tracking dynamic via config?

## Verification Plan

### Automated Tests
- N/A - Testing happens in production/staging environment.

### Manual Verification
- Deploy to Railway.
- Admin tests `/start` in DM to verify user registration.
- Admin runs `/channels` and `/users` to verify DB reads.
- Admin tests `/update_powell` and `/update_etf` and verifies `config.json` updates safely on the volume.
- Admin runs `/send Test message` to ensure broadcasting functions correctly.
- Monitor execution logs on Railway for 24 hours to ensure asynchronous loops (RSI, Digest, News, etc.) run without blocking each other.
