import os
import asyncio
from datetime import datetime, timezone
from utils.config import load_config, STARTED_AT
from utils.app_state import get_db, get_bot_app

# Global state trackers
active_signal_states = {}
rsi_state_tracker = {}
_MARKET_SNAPSHOT_CACHE = {}
_MARKET_CACHE_LOCK = asyncio.Lock()

def fire_and_forget(coro):
    """Utility to fire an async task and log its completion or failure."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
    except RuntimeError:
        # If no loop is running, just return
        return

    def _done(t):
        try:
            t.result()
        except Exception as e:
            print(f"🔥 Background task error: {e}")
    task.add_done_callback(_done)
