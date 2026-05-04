try:
    from supabase import create_client
except ImportError:
    create_client = None
    print("WARNING: supabase package not installed — Supabase features disabled")

import os
import json
import threading
import tempfile
import asyncio
from datetime import datetime, timezone

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.path.join(os.getcwd(), "data"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = None

if SUPABASE_URL and SUPABASE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("☁️ Supabase Cloud Persistence initialized.")
    except Exception as e:
        print(f"⚠️ Supabase initialization failed: {e}")

# Shared lock for local state persistence
_STATE_LOCK = threading.Lock()

def save_bot_state(key, data):
    """
    Persists bot state (JSON) to Supabase cloud if available, otherwise local file.
    """
    if supabase:
        try:
            supabase.table("bot_state").upsert({
                "key": key, 
                "value": data, 
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).execute()
            return
        except Exception as e:
            print(f"⚠️ Supabase save_bot_state error for {key}: {e}")

    # Local Fallback (Atomic with Lock)
    path = os.path.join(DATA_DIR, f"{key}.json")
    try:
        with _STATE_LOCK:
            # Atomic write using tempfile
            fd, temp_path = tempfile.mkstemp(dir=DATA_DIR, prefix=f"{key}_tmp")
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f)
                # Atomic swap (os.replace is atomic on POSIX, and handles overwriting on Windows in newer Python)
                os.replace(temp_path, path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
    except Exception as e:
        print(f"❌ Local save_bot_state error for {key}: {e}")

def load_bot_state(key, default=None):
    """
    Loads bot state from Supabase or local fallback.
    """
    if supabase:
        try:
            res = supabase.table("bot_state").select("value").eq("key", key).maybe_single().execute()
            if res and hasattr(res, "data") and res.data:
                return res.data.get("value") if isinstance(res.data, dict) else res.data["value"]
        except Exception as e:
            print(f"⚠️ Supabase load_bot_state error for {key}: {e}")

    # Local Fallback
    path = os.path.join(DATA_DIR, f"{key}.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return default or {}


# ============================================================
# STATE PERSISTENCE HELPERS
# ============================================================

async def load_bot_state_local():
    """Load persistent bot state from database safely updating in-place."""
    from utils.state import active_signal_states, rsi_state_tracker
    
    lifecycle_data = await asyncio.to_thread(load_bot_state, "lifecycle_state", {})
    rsi_data = await asyncio.to_thread(load_bot_state, "rsi_state", {})
    
    if isinstance(lifecycle_data, dict):
        active_signal_states.update(lifecycle_data)
    if isinstance(rsi_data, dict):
        rsi_state_tracker.update(rsi_data)

async def save_rsi_state(state):
    await asyncio.to_thread(save_bot_state, "rsi_state", state)

async def save_lifecycle_state(state):
    await asyncio.to_thread(save_bot_state, "lifecycle_state", state)
