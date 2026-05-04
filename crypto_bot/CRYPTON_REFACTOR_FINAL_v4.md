# 🔧 CRYPTON INTEL — MODULAR REFACTOR PROMPT (FINAL v4.0)
### Antigravity Execution Document | Certified for Production
### Audit Score: 95/100 | All P1 and P2 risks resolved

---

## 🛑 SESSION DEFINITION — READ THIS FIRST, NON-NEGOTIABLE

This session is a **pure structural refactor only.**

### ✅ YOU ARE ALLOWED TO:
- Move existing code into new files
- Create folders and __init__.py files
- Fix import paths so moved code runs correctly
- Create utils/app_state.py for shared state (see Patch B — mandatory)
- Leave TODO and BUG comments where needed

### ❌ YOU ARE NOT ALLOWED TO:
- Change any logic
- Rewrite any function even if it looks bad
- Add new implementations
- Optimise anything
- Fix bugs
- Add missing features
- Convert sync functions to async or vice versa

**If something is missing → write: `# TODO: function missing — existed in original system?`**
**If something is broken → write: `# BUG: [describe what is broken]`**
**DO NOT FIX IT. Do not create new code to replace it.**

The only permitted exception to "no new code" is utils/app_state.py
which is required to make the modular structure function correctly.
This is defined in full in Patch B below.

---

## 🛑 STRICT RULES FOR THIS ENTIRE SESSION

| Rule | Definition |
|---|---|
| Zero Logic Change | Every function must behave exactly the same after refactor |
| Copy-Paste Only | Move code exactly as it exists — no rewriting even if it looks bad |
| No New Code | Do not create new indicator logic, API logic, or Gemini logic |
| Single Entry Point | main.py remains the only entry point Railway reads |
| Import Safety | Use absolute imports — fix import paths only, never fix logic |
| Async Consistency | Do not convert sync to async or async to sync under any circumstance |
| Railway Stability | Do not touch Procfile, environment variables, or Railway configuration |
| TODO Not Fix | If a function referenced does not exist — comment it, do not create it |
| Exception | utils/app_state.py is the only new file with new code — required for shared state |

---

## 🚨 MANDATORY STEP 0 — GIT BACKUP BEFORE ANYTHING

Before touching a single file, run this:

```bash
git add .
git commit -m "backup: pre-refactor snapshot"
```

**This is non-negotiable. If something goes wrong during refactor,
this commit is your recovery point.**
Do not proceed to Step 1 until this commit is confirmed.

---

## 🚨 PATCH A — ENVIRONMENT VARIABLE LOADING ORDER (P1 — CRITICAL)

This must be implemented in Step 10 when rebuilding main.py.
Read and understand this before starting.

**The Problem:**
If modules are imported before load_dotenv() runs, every os.getenv()
call inside those modules returns None. The bot crashes on Railway
with silent or cryptic errors because API keys and tokens are missing.

**The Rule:**
The first four lines of main.py must always be:

```python
import os
from dotenv import load_dotenv
load_dotenv()
# ← ALL project module imports come AFTER this line only
```

**Never move these four lines below any project import under any circumstance.**
This applies even if an import looks like it should come first.
load_dotenv() runs first. Always. No exceptions.

---

## 🚨 PATCH B — GLOBAL STATE AND SHARED INSTANCES (P1 — CRITICAL)

This must be implemented before any command or engine files are created.
Read and understand this before starting.

**The Problem:**
In the current monolithic main.py, shared objects like the database
connection and Telegram bot Application instance are global variables
accessible by all functions in the same file. After splitting into
modules, those functions can no longer see those globals because they
are in different files. Strict copy-paste breaks without this fix.

**The Solution:**
Create one shared state file that all modules import from.
This is the only new file with new code permitted in this session.

**Create this file exactly as written: utils/app_state.py**

```python
"""
utils/app_state.py
Shared state container for Crypton Intel bot.
Holds globally shared instances accessible across all modules.
This is the only permitted global state pattern in this project.
"""

_db_connection = None
_bot_app = None

def set_db(connection):
    """Store the database connection for global access."""
    global _db_connection
    _db_connection = connection

def get_db():
    """Retrieve the database connection."""
    return _db_connection

def set_bot_app(app):
    """Store the Telegram Application instance for global access."""
    global _bot_app
    _bot_app = app

def get_bot_app():
    """Retrieve the Telegram Application instance."""
    return _bot_app
```

**How main.py uses it (Step 10):**

```python
import os
from dotenv import load_dotenv
load_dotenv()   # FIRST — before all project imports

# Initialise shared state BEFORE importing commands
from utils.app_state import set_db, set_bot_app
from database.db_users import initialise_database

# Set up database connection
db_conn = initialise_database()
set_db(db_conn)

# Build Telegram app and store in shared state
from telegram.ext import Application
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Application.builder().token(TELEGRAM_TOKEN).build()
set_bot_app(app)

# Now safe to import commands — shared state is ready
from commands.cmd_digest import handle_digest
from commands.cmd_signal import handle_signal
# ... rest of imports
```

**How any module that needs the database uses it:**

```python
# Inside cmd_digest.py or any other module
from utils.app_state import get_db

async def handle_digest(update, context):
    conn = get_db()
    # use conn exactly as the original code used db_connection
```

**How any module that needs the bot app uses it:**

```python
from utils.app_state import get_bot_app

def send_alert(message):
    app = get_bot_app()
    # use app exactly as the original code used the bot instance
```

This pattern replaces all global variable access across the entire project.
The logic inside every function stays identical — only the access pattern changes.

---

## 🧭 IMPORT HIERARCHY — FOLLOW THIS EXACTLY

This prevents circular imports. Lower layers must never import from higher layers.

```
utils  (including app_state)
  ↓
indicators
  ↓
database
  ↓
digest / signals / lifecycle / alerts
  ↓
commands
  ↓
main
```

**STRICT RULE:**
- `utils` imports nothing from this project (only standard library and third-party packages)
- `indicators` may only import from `utils`
- `database` may only import from `utils`
- `digest`, `signals`, `lifecycle`, `alerts` may import from `utils`, `indicators`, `database`
- `commands` may import from everything above
- `main` imports from `commands` and schedulers only

If you are about to write an import that goes upward in this hierarchy — stop.
You have a structural problem. Flag it with a TODO comment instead.

---

## 📁 TARGET FOLDER STRUCTURE

Create exactly this structure:

```
crypton-bot/
│
├── main.py                        ← Entry point only
│
├── digest/
│   ├── __init__.py
│   ├── digest_builder.py          ← Free + Pro digest message construction
│   ├── digest_gemini.py           ← All Gemini API calls for digest
│   └── digest_scheduler.py        ← 9:30 AM IST schedule logic
│
├── signals/
│   ├── __init__.py
│   ├── signal_engine.py           ← Confluence scoring + signal fire logic
│   ├── signal_formatter.py        ← Free + Pro signal message formatting
│   └── signal_cooldown.py         ← Cooldown + expiry tracking
│
├── lifecycle/
│   ├── __init__.py
│   └── lifecycle_engine.py        ← 5-state machine + Supabase read/write
│
├── alerts/
│   ├── __init__.py
│   └── watch_zone.py              ← Level proximity + cooldown + alert output
│
├── indicators/
│   ├── __init__.py
│   ├── rsi.py                     ← RSI calculation
│   ├── ema.py                     ← EMA 21 and EMA 50
│   ├── macd.py                    ← MACD 12/26/9
│   ├── bos.py                     ← Break of Structure detection
│   └── levels.py                  ← Swing high/low key level detection
│
├── database/
│   ├── __init__.py
│   ├── db_users.py                ← All SQLite user and subscription logic
│   └── db_supabase.py             ← Supabase signal state, streaks, cooldowns
│
├── commands/
│   ├── __init__.py
│   ├── cmd_digest.py              ← /digest handler
│   ├── cmd_signal.py              ← /signal and /testsignal handlers
│   ├── cmd_lifecycle.py           ← /lifecycle handler
│   ├── cmd_explain.py             ← /explain handler (Pro)
│   ├── cmd_levels.py              ← /levels handler (Pro)
│   ├── cmd_bias.py                ← /bias handler (Pro)
│   ├── cmd_weekly.py              ← /weekly handler
│   └── cmd_status.py              ← /status handler
│
└── utils/
    ├── __init__.py
    ├── app_state.py               ← Shared state container (see Patch B)
    ├── binance_api.py             ← All Binance API calls
    ├── news_api.py                ← News fetching and severity parsing
    ├── coinglass_api.py           ← Liquidation data fetching
    ├── macro_api.py               ← TradingEconomics macro events
    └── time_utils.py              ← UTC to IST conversion, timestamps
```

---

## 📋 STEP-BY-STEP EXECUTION ORDER

**Do not skip steps. Do not combine steps. Complete each step fully before moving to next.**

---

### STEP 1 — Create All Folders and __init__.py Files

Create these folders:
- `digest/`
- `signals/`
- `lifecycle/`
- `alerts/`
- `indicators/`
- `database/`
- `commands/`
- `utils/`

Inside each folder, create one completely empty `__init__.py` file.

Also create `utils/app_state.py` now using the exact code from Patch B above.

**DO NOT MOVE ANY OTHER CODE IN THIS STEP.**
Only create folders, empty __init__.py files, and app_state.py.
Confirm all exist before moving to Step 2.

---

### STEP 2 — Move Utils (Foundation Layer — Do First)

Utils have zero dependencies on other project files.
Everything else will import from utils, so this must be correct first.

Move existing functions from main.py into these files:

#### utils/binance_api.py
Move into this file:
- All functions that call the Binance API
- All OHLCV data fetching functions
- All price and ticker fetching functions
- All volume fetching functions

If a Binance function does not exist in current code:
```python
# TODO: Binance function missing — implement in signal rebuild session
```

#### utils/news_api.py
Move into this file:
- All news fetching functions
- All news parsing functions
- Any severity label mapping that exists in current code

If not present:
```python
# TODO: news functions missing — implement in digest rebuild session
```

#### utils/coinglass_api.py
Move into this file:
- All CoinGlass API calls
- All liquidation data fetching functions
- Any liquidation label logic that exists in current code

If not present:
```python
# TODO: coinglass functions missing — implement in digest rebuild session
```

#### utils/macro_api.py
Move into this file:
- All TradingEconomics API calls
- All macro event fetching functions
- Any impact level filtering that exists in current code

If not present:
```python
# TODO: macro functions missing — implement in digest rebuild session
```

#### utils/time_utils.py
Move into this file:
- All UTC to IST conversion functions
- All timestamp formatting functions
- Any date/time utility functions

If not present:
```python
# TODO: time_utils functions missing — implement in digest rebuild session
```

---

### STEP 3 — Move Indicators (Pure Math — No Side Effects)

Indicator files contain only calculation functions.
They take numbers in and return numbers out.
They must not call APIs, send Telegram messages, or touch the database.
They import only from utils if needed.

#### indicators/rsi.py
Move existing RSI calculation function exactly as it is.
If it does not exist:
```python
# TODO: RSI function missing — implement in signal rebuild session
```

#### indicators/ema.py
Move existing EMA calculation function exactly as it is.
If it does not exist:
```python
# TODO: EMA function missing — implement in signal rebuild session
```

#### indicators/macd.py
Move existing MACD calculation function exactly as it is.
If it does not exist:
```python
# TODO: MACD function missing — implement in signal rebuild session
```

#### indicators/bos.py
Move existing BOS detection function exactly as it is.
If it does not exist:
```python
# TODO: BOS function missing — implement in signal rebuild session
```

#### indicators/levels.py
Move existing key level detection function exactly as it is.
If it does not exist:
```python
# TODO: levels function missing — implement in signal rebuild session
```

---

### STEP 4 — Move Database Layer

#### database/db_users.py
Move into this file:
- ALL SQLite database functions exactly as they are
- User table operations (create, read, update)
- Subscription table operations
- Pro/Free tier checking functions
- All asyncio.to_thread() SQLite calls

**Critical rules:**
- DO NOT change the database schema
- DO NOT change the asyncio.to_thread() pattern
- Copy the existing SQLite code exactly — character for character

If any function previously accessed a global db_connection variable,
replace that access with:
```python
from utils.app_state import get_db
conn = get_db()
```
This is the only permitted change — accessing shared state correctly.
The function logic itself must not change.

#### database/db_supabase.py
Move into this file:
- All existing Supabase connection setup
- All existing Supabase read/write functions

Add this safety import guard at the very top of the file:
```python
try:
    from supabase import create_client
except ImportError:
    create_client = None
    print("WARNING: supabase package not installed — Supabase features disabled")
```

If Supabase code does not exist yet in the current project:
```python
# TODO: Supabase not yet implemented — implement in lifecycle session
```
Do not create any Supabase functions. Leave the TODO and move on.

---

### STEP 5 — Move Digest Module

#### digest/digest_gemini.py
Move into this file:
- Gemini API initialisation code
- All Gemini API calls used in digest
- Gemini response parsing logic

Copy exactly as it exists.
If Gemini call is broken or returning "Analysis pending":
```python
# BUG: Gemini call not returning real analysis — fix in digest rebuild session
```
Do not fix it here.

#### digest/digest_builder.py
Move into this file:
- All digest message construction logic
- Free digest format builder function
- Pro digest format builder function
- Market context generation logic
- Status line generation logic
- All text formatting and emoji logic for digest

Copy exactly as it exists.

#### digest/digest_scheduler.py
Move into this file:
- The 9:30 AM IST schedule setup
- The scheduled function that triggers digest
- Any APScheduler or asyncio scheduling code

Copy exactly as it exists.

---

### STEP 6 — Move Signal Module

#### signals/signal_engine.py
Move into this file:
- All signal detection logic
- Confluence scoring system
- Signal fire decision logic
- The signal check loop

Copy exactly as it exists.

#### signals/signal_formatter.py
Move into this file:
- Free signal message text builder
- Pro signal message text builder
- Any confluence label functions

Copy exactly as it exists.

#### signals/signal_cooldown.py
Move into this file:
- Cooldown checking logic
- Cooldown setting logic
- Signal expiry checking logic
- Daily signal count tracking

Copy exactly as it exists.

---

### STEP 7 — Move Lifecycle Module

#### lifecycle/lifecycle_engine.py
Move into this file:
- All lifecycle state machine logic
- State transition functions
- /lifecycle command response builder

Copy exactly as it exists.

---

### STEP 8 — Move Alerts Module

#### alerts/watch_zone.py
Move into this file:
- Key level proximity detection
- Watch zone alert trigger logic
- Watch zone cooldown management
- Free + Pro watch zone message formatters

Copy exactly as it exists.
If watch zone is not yet implemented:
```python
# TODO: watch_zone not yet implemented — implement in alerts session
```

---

### STEP 9 — Move Commands (One File Per Command)

Each file contains only the handler for that one command.
Copy existing handler code exactly as it is.

If a handler previously accessed a global db_connection or bot instance,
replace that access using utils/app_state.py:
```python
from utils.app_state import get_db
from utils.app_state import get_bot_app
```
This is the only permitted change to handler code.

| File | Commands to move |
|---|---|
| cmd_digest.py | /digest handler |
| cmd_signal.py | /signal and /testsignal handlers |
| cmd_lifecycle.py | /lifecycle handler |
| cmd_explain.py | /explain handler |
| cmd_levels.py | /levels handler |
| cmd_bias.py | /bias handler |
| cmd_weekly.py | /weekly handler |
| cmd_status.py | /status handler |

If a command handler does not exist yet:
```python
# TODO: /[command] handler not yet implemented — implement in commands session
async def handle_[command](update, context):
    pass
```

---

### STEP 10 — Rebuild main.py as Clean Entry Point

Rewrite main.py following this exact structure and order:

```python
"""
CRYPTON INTEL — Main Entry Point
Initialises shared state, registers handlers, starts schedulers.
"""

# ============================================================
# SECTION 1 — ENVIRONMENT (MUST BE FIRST — NEVER MOVE THIS)
# ============================================================
import os
from dotenv import load_dotenv
load_dotenv()  # Loads .env file — must run before any project imports

# ============================================================
# SECTION 2 — SHARED STATE INITIALISATION
# ============================================================
from utils.app_state import set_db, set_bot_app
from database.db_users import initialise_database

db_conn = initialise_database()
set_db(db_conn)

# ============================================================
# SECTION 3 — TELEGRAM APPLICATION
# ============================================================
from telegram.ext import Application, CommandHandler

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Application.builder().token(TELEGRAM_TOKEN).build()
set_bot_app(app)

# ============================================================
# SECTION 4 — COMMAND HANDLER IMPORTS
# (Safe to import now — shared state is ready)
# ============================================================
from commands.cmd_digest import handle_digest
from commands.cmd_signal import handle_signal
from commands.cmd_lifecycle import handle_lifecycle
from commands.cmd_explain import handle_explain
from commands.cmd_levels import handle_levels
from commands.cmd_bias import handle_bias
from commands.cmd_weekly import handle_weekly
from commands.cmd_status import handle_status

# ============================================================
# SECTION 5 — SCHEDULER IMPORTS
# ============================================================
from digest.digest_scheduler import start_digest_scheduler
from signals.signal_engine import start_signal_loop

# ============================================================
# SECTION 6 — HANDLER REGISTRATION
# ============================================================
app.add_handler(CommandHandler("digest", handle_digest))
app.add_handler(CommandHandler("signal", handle_signal))
app.add_handler(CommandHandler("lifecycle", handle_lifecycle))
app.add_handler(CommandHandler("explain", handle_explain))
app.add_handler(CommandHandler("levels", handle_levels))
app.add_handler(CommandHandler("bias", handle_bias))
app.add_handler(CommandHandler("weekly", handle_weekly))
app.add_handler(CommandHandler("status", handle_status))

# ============================================================
# SECTION 7 — START SCHEDULERS AND BOT
# ============================================================
import asyncio

async def main():
    start_digest_scheduler()
    asyncio.create_task(start_signal_loop())
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
```

If any referenced function does not exist:
```python
# TODO: [function_name] missing — implement in correct session
```
Do not create it. Wire what exists. Comment what does not.

---

### STEP 11 — Fix All Import Paths

After all files are created, go through every file and verify:

**Rule 1 — Absolute imports only:**
```python
# CORRECT
from utils.binance_api import get_klines
from indicators.rsi import calculate_rsi
from database.db_users import is_pro_user
from utils.app_state import get_db

# WRONG — never use this style
from .binance_api import get_klines
from ..utils import get_klines
```

**Rule 2 — Hierarchy respected:**
No file imports from a layer above it:
```
utils → indicators → database → engines → commands → main
```

**Rule 3 — Import test:**
```bash
python -c "import main"
```
Must complete with zero errors before Railway deployment.

**Rule 4 — If ImportError appears:**
- Read the error — it tells you exactly which import failed
- Fix only that import path
- Do not change any logic
- Run the test again
- Repeat until clean

---

## ✅ VERIFICATION CHECKLIST — COMPLETE EVERY ITEM BEFORE MARKING DONE

### Structure
- [ ] All 8 folders created
- [ ] Every folder has an __init__.py file
- [ ] utils/app_state.py created with exact code from Patch B
- [ ] All files created in correct folder locations
- [ ] main.py follows exact 7-section structure from Step 10
- [ ] main.py contains no business logic — only wiring

### Patch A — Environment Loading
- [ ] load_dotenv() is the first meaningful line in main.py
- [ ] No project module is imported before load_dotenv() runs
- [ ] Confirmed by reading main.py top to bottom

### Patch B — Shared State
- [ ] utils/app_state.py exists with set_db, get_db, set_bot_app, get_bot_app
- [ ] main.py calls set_db() and set_bot_app() before importing commands
- [ ] Any module that previously used global db_connection now uses get_db()
- [ ] Any module that previously used global bot instance now uses get_bot_app()

### Zero Logic Change
- [ ] No function was renamed
- [ ] No variable was renamed
- [ ] No algorithm was changed
- [ ] No sync/async conversion performed
- [ ] No bug was fixed (bugs marked with BUG comments only)
- [ ] No new feature added (missing items marked with TODO comments only)

### Import Safety
- [ ] All imports use absolute paths — no dots
- [ ] No circular imports exist
- [ ] Supabase import guard added to db_supabase.py
- [ ] `python -c "import main"` runs with zero errors

### Bot Functionality — Test Every Single One
- [ ] Bot starts without errors
- [ ] /start command responds
- [ ] /digest command responds
- [ ] /signal command responds
- [ ] /lifecycle command responds
- [ ] /status command responds
- [ ] No error logs in Railway for first 5 minutes after deploy
- [ ] Scheduled digest triggers correctly at 9:30 AM IST

### Git
- [ ] Pre-refactor backup commit confirmed
- [ ] Refactor commit pushed: "refactor: modular structure v4.0"
- [ ] Railway auto-deployed successfully
- [ ] Railway logs show clean startup

---

## 🚀 RAILWAY DEPLOYMENT STEPS

After verification checklist is fully complete:

```bash
git add .
git commit -m "refactor: modular structure v4.0 — zero logic change"
git push
```

Then:
1. Open Railway dashboard
2. Watch deployment logs for 2 minutes
3. Look for bot startup confirmation message
4. If any error appears — read it, fix only the import path, push again
5. Never change logic to fix a Railway error — it is always an import or env issue

Railway reads only main.py as entry point.
No Procfile or Railway configuration changes needed.
All repo files are automatically included in deployment.

---

## 📋 POST-REFACTOR SESSION PLAN

Once refactor is complete and Railway confirmed running cleanly,
sessions proceed in this exact order. Each session touches only
the files listed — no other files are modified.

| Session | Focus | Files to Edit |
|---|---|---|
| Session 2 | Digest rebuild — Pro + Free format | `digest/` folder only |
| Session 3 | Signal engine rebuild | `signals/` + `indicators/` only |
| Session 4 | Lifecycle + Supabase persistence | `lifecycle/` + `database/db_supabase.py` only |
| Session 5 | Watch zone alerts | `alerts/` only |
| Session 6 | New commands | `commands/` folder only |

---

*CRYPTON INTEL — MODULAR REFACTOR PROMPT FINAL v4.0*
*Merged: Claude architecture + GPT safety rules + Antigravity audit fixes*
*Certified: All P1 and P2 risks resolved*
*Audit score: 95/100 — Approved for production execution*
*Zero logic change. Structure only. Git backup mandatory.*
