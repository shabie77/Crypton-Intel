import asyncio
import time

# Global state trackers
active_signal_states = {}
rsi_state_tracker = {}
_MARKET_SNAPSHOT_CACHE = {}
_MARKET_CACHE_LOCK = asyncio.Lock()
_IN_FLIGHT_FETCHES = {}
_IN_FLIGHT_FETCHES_LOCK = asyncio.Lock()

# Shared cache TTL constant used by both binance_api.py and pro_engine.py.
MARKET_SNAPSHOT_TTL_SECONDS = 300

# Telegram global 429 cooldown shared across all senders.
_TELEGRAM_GLOBAL_COOLDOWN_UNTIL: float = 0.0  # epoch seconds


def fire_and_forget(coro):
    """Utility to fire an async task and log its completion or failure."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
    except RuntimeError as e:
        print(f"fire_and_forget failed - no running event loop: {e}")
        return

    def _done(t):
        try:
            t.result()
        except Exception as e:
            print(f"Background task error: {e}")

    task.add_done_callback(_done)
