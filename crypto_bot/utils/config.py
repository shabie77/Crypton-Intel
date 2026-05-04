import os
import json
import asyncio
from datetime import datetime, timezone
import aiohttp

# Base directories
DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.path.join(os.getcwd(), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

# File paths
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
RSI_STATE_FILE = os.path.join(DATA_DIR, "rsi_state.json")
LIFECYCLE_STATE_FILE = os.path.join(DATA_DIR, "lifecycle_state.json")
LAST_DIGEST_FILE = os.path.join(DATA_DIR, "last_digest.txt")

# Environment
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "1120431469"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Global instances and locks
STARTED_AT = datetime.now(timezone.utc)
CONFIG_LOCK = asyncio.Lock()
RSI_STATE_LOCK = asyncio.Lock()
LIFECYCLE_STATE_LOCK = asyncio.Lock()
BROADCAST_SEMAPHORE = asyncio.Semaphore(25)
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
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            # Deep merge simple dict
            merged = DEFAULT_CONFIG.copy()
            merged.update(data)
            return merged
    except Exception:
        return DEFAULT_CONFIG

async def update_config(mutator):
    async with CONFIG_LOCK:
        config = await load_config()
        if asyncio.iscoroutinefunction(mutator):
            await mutator(config)
        else:
            mutator(config)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return config
