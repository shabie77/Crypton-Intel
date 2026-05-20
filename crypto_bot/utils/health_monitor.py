"""
utils/health_monitor.py

Tracks the health state of all digest subsystems.
Updated by each engine after every cycle.
Read by /status command.
"""
import json
import asyncio
import os
from datetime import datetime, timezone, timedelta
from utils.config import DATA_DIR

CACHE_FILE = os.path.join(DATA_DIR, "health_state.json")

_state: dict = {}


async def update(system: str, status: str, note: str = ""):
    """
    Update health state for a system.
    status: 'ok' | 'offline' | 'error'
    """
    global _state
    _state[system] = {
        "status": status,
        "note": note,
        "last_updated": (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%I:%M %p IST")
    }
    await _flush()


async def _flush():
    """Persist state to disk for cross-session visibility."""
    try:
        def write_state():
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(_state, f, indent=2)
            os.replace(tmp, CACHE_FILE)
        await asyncio.to_thread(write_state)
    except Exception as e:
        print(f"⚠️ Health monitor flush error: {e}")


async def load() -> dict:
    """Load state from disk (used on bot restart)."""
    global _state
    try:
        def _read():
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        _state = await asyncio.to_thread(_read)
    except Exception:
        _state = {}
    return _state


async def get_status_message(started_at) -> str:
    """
    Returns a formatted status message for the /status command.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    uptime = now - started_at

    state = await load()

    def icon(system_key: str) -> str:
        s = state.get(system_key, {}).get("status", "unknown")
        return "🟢" if s == "ok" else "🟡" if s == "offline" else "🔴"

    def last(system_key: str) -> str:
        return state.get(system_key, {}).get("last_updated", "Never")

    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes = rem // 60

    msg = (
        f"🤖 <b>Bot Status: Online</b>\n"
        f"⏱ Uptime: {hours}h {minutes}m\n\n"
        f"<b>System Health:</b>\n"
        f"{icon('binance_api')} Binance API\n"
        f"{icon('groq_api')} Groq API\n"
        f"{icon('macro_engine')} Macro Feed\n"
        f"{icon('news_engine')} News Feed\n"
        f"{icon('digest_engine')} Digest Engine\n\n"
        f"<b>Last Digest:</b> {last('digest_engine')}"
    )
    return msg
