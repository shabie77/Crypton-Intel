import os
import json
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv

_app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_app_dir, ".env"))

# Base directories
DATA_DIR = os.getenv("DATA_DIR", os.path.join(_app_dir, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# File paths
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
RSI_STATE_FILE = os.path.join(DATA_DIR, "rsi_state.json")
LIFECYCLE_STATE_FILE = os.path.join(DATA_DIR, "lifecycle_state.json")
LAST_DIGEST_FILE = os.path.join(DATA_DIR, "last_digest.txt")

# Environment
_admin_id_str = os.getenv("ADMIN_USER_ID")
if not _admin_id_str:
    raise ValueError("⚠️ CRITICAL: ADMIN_USER_ID environment variable is missing!")
ADMIN_USER_ID = int(_admin_id_str)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
from utils.gemini_rotator import GroqRotator

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_ROTATOR = GroqRotator()

# Global instances and locks
STARTED_AT = datetime.now(timezone.utc)
CONFIG_LOCK = asyncio.Lock()
RSI_STATE_LOCK = asyncio.Lock()
LIFECYCLE_STATE_LOCK = asyncio.Lock()
BROADCAST_SEMAPHORE = asyncio.Semaphore(8)
DISPATCH_SEMAPHORE = asyncio.Semaphore(1)

# Default Config
DEFAULT_CONFIG = {
    "tracked_coins": ["BTC", "ETH", "SOL", "BNB", "ARB", "XRP", "ADA", "DOGE", "MATIC", "LINK", "AVAX", "DOT", "LTC", "SHIB", "TRX"],
    "cooldown_hours": 4,
    "rsi_thresholds": {"oversold": 30, "overbought": 70},
    "alert_zones": {"buffer_pct": 0.015},
    "digest": {"symbols": ["BTC", "ETH", "SOL"]}
}

async def load_config():
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG
    try:
        def _read():
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        data = await asyncio.to_thread(_read)
        # Deep merge simple dict
        merged = DEFAULT_CONFIG.copy()
        merged.update(data)
        return merged
    except Exception as e:
        print(f"⚠️ Error loading config, using default: {e}")
        return DEFAULT_CONFIG

async def update_config(mutator):
    async with CONFIG_LOCK:
        config = await load_config()
        if asyncio.iscoroutinefunction(mutator):
            await mutator(config)
        else:
            mutator(config)
        def _write():
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        await asyncio.to_thread(_write)
        return config
