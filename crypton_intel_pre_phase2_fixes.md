# Crypton Intel — Pre-Phase 2 Fix Prompt
**Send this to Antigravity before starting any Phase 2 code.**

---

Run the following fixes on our crypto bot codebase in this exact order. Fix one at a time and confirm each is done before moving to the next. Do not start Phase 2 code until all 7 fixes are complete and the bot starts cleanly.

---

## Fix 1 — WAL Mode (database.py)

In the database initialization function, add WAL mode pragma immediately after every connection is opened. This must be the first thing that runs on every connection:

```python
conn.execute("PRAGMA journal_mode=WAL;")
```

**Why:** Phase 2 adds concurrent writes — price alert checker every 60 seconds, Telethon news watcher firing unpredictably, digest scheduler, and Razorpay webhook all hitting simultaneously. Without WAL mode, these concurrent writes will cause `database is locked` errors under load.

---

## Fix 2 — Database Cleanup Coroutine + Indexes (database.py + main.py)

**Step A — Add indexes** to both tables immediately if they don't already exist:

```sql
CREATE INDEX IF NOT EXISTS idx_liq_timestamp ON liquidations(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts_log(timestamp);
```

**Step B — Add a cleanup function** in database.py that deletes rows older than 7 days from both tables:

```python
async def cleanup_old_data():
    conn = get_connection()
    conn.execute("DELETE FROM liquidations WHERE timestamp < datetime('now', '-7 days')")
    conn.execute("DELETE FROM alerts_log WHERE timestamp < datetime('now', '-7 days')")
    conn.commit()
    conn.close()
```

**Step C — Schedule it** as a weekly async task in the main event loop alongside the existing digest scheduler. Run it once on startup and then every 7 days.

**Why:** The liquidations and alerts_log tables currently grow forever with no cleanup. Phase 2 adds more alert types and news events. Over weeks this becomes a performance problem that slows digest generation.

---

## Fix 3 — Remove Dead Dependencies (requirements.txt)

Remove these two lines entirely from requirements.txt. They are never imported anywhere in the codebase — confirmed by full audit:

```
python-telegram-bot==20.7
requests==2.31.0
```

The bot uses raw `aiohttp` for all Telegram and HTTP calls. These are dead weight adding unnecessary build time on Railway deploys.

**Why:** Removing dead dependencies prevents confusion when Phase 2 code is added — no developer or AI assistant should try to use these libraries thinking they are the intended HTTP layer.

---

## Fix 4 — Bare Except Clauses (main.py)

Find all 4 instances of bare `except:` in main.py (approximately lines 119, 713, 814, 1017) and change every one to `except Exception:`. Do not change any logic inside the blocks — only the except line itself.

```python
# Change this pattern:
except:
    continue

# To this:
except Exception:
    continue
```

**Why:** Bare `except:` silently catches `KeyboardInterrupt` and `SystemExit` — system-level signals that should never be swallowed. This can cause the bot to become unkillable or fail to restart cleanly on Railway.

---

## Fix 5 — Move Inline Imports to Top of File (main.py)

Move these two imports from inside function bodies to the top of main.py alongside all other module-level imports:

- `import xml.etree.ElementTree as ET`
  Currently inside: `fetch_top_news()` and `_fetch_all_high_impact_events()`

- `from bs4 import BeautifulSoup`
  Currently inside: `_fetch_investing_macro_events()`

After moving them to the top, delete the inline versions inside the functions.

**Why:** Imports inside function bodies add overhead on every call and are non-standard. With Phase 2 adding more frequent function calls, this adds up. Moving to top-level also makes the dependency surface immediately visible.

---

## Fix 6 — add_channel() Status Bug (database.py)

In the `add_channel()` function, the current logic treats ALL existing channels (including active ones) as banned. Fix it to check the actual status value before deciding:

```python
if existing_channel:
    status = existing_channel[0]
    if status == 'BANNED' or status == 'EXPIRED':
        print(f"🔒 Banned channel tried to re-add the bot. Access Denied.")
        return False
    else:
        # Channel already active — skip silently, no log needed
        return True
```

**Why:** The current bug logs a "banned" message for channels that are simply already registered and active. This produces misleading admin alerts and could cause confusion when diagnosing channel issues in Phase 2.

---

## Fix 7 — Environment Variable Cleanup (.env + .env.example)

**Step A — Remove stale variable from local `.env`:**
```
CRYPTOPANIC_API_KEY=...
```
This key is never referenced anywhere in the codebase. The bot uses CryptoCompare and CoinTelegraph RSS instead. Remove it to reduce confusion.

**Step B — Add missing variables to local `.env`:**
```
ADMIN_USER_ID=1120431469
RAILWAY_VOLUME_MOUNT_PATH=./data
```
Without these, the bot cannot run locally — main.py hard-crashes if `RAILWAY_VOLUME_MOUNT_PATH` is not set.

**Step C — Update `.env.example`** to include all required variables so the template is complete:
```
TELEGRAM_BOT_TOKEN=replace_with_your_bot_token
GEMINI_API_KEY=replace_with_your_gemini_key
GROQ_API_KEY=replace_with_your_groq_key
ADMIN_USER_ID=replace_with_your_telegram_user_id
RAILWAY_VOLUME_MOUNT_PATH=./data
```

---

## Completion Check

After all 7 fixes are applied:

1. Run the bot locally and confirm it starts with no import errors
2. Confirm no startup crash related to `RAILWAY_VOLUME_MOUNT_PATH`
3. Confirm Railway deploy completes successfully with the slimmed requirements.txt
4. Confirm the bot responds to `/status` normally in Telegram

Once all 4 confirmation checks pass — Phase 2 development begins.
