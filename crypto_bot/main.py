from collections import OrderedDict
import os
import json
import asyncio
import re
import html
import traceback
from datetime import datetime, timezone, timedelta
import logging
import aiohttp
import websockets
from dotenv import load_dotenv

import database
from utils.gemini_rotator import GeminiRotator, GroqRotator
from utils.scoring_engine import calculate_score, get_market_structure
from utils.scrapers import (
    fetch_top_news,
    fetch_macro_events,
    classify_news_importance,
)
from webhook_server import run_webhook_server

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("CryptoBot")


load_dotenv()

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "1120431469")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError(
        "CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set. Bot shutting down."
    )

DATA_DIR = os.getenv(
    "RAILWAY_VOLUME_MOUNT_PATH", os.path.join(os.getcwd(), "data")
)
if not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
    print(
        f"⚠️ Warning: RAILWAY_VOLUME_MOUNT_PATH not set. Using local directory: {DATA_DIR}"
    )

os.makedirs(DATA_DIR, exist_ok=True)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Gemini/Groq AI status
gemini_rotator = GeminiRotator()
groq_rotator = GroqRotator()

if gemini_rotator.has_keys():
    print(
        f"🤖 Gemini AI Intelligence Layer initialized ({len(gemini_rotator.get_all_keys())} keys)."
    )
if groq_rotator.has_keys():
    print(
        f"🧠 Groq AI Failover Layer initialized ({len(groq_rotator.get_all_keys())} keys)."
    )
if not gemini_rotator.has_keys() and not groq_rotator.has_keys():
    print("⚠️ AI API Keys missing. Bot will use manual market summary logic.")

RSI_STATE_FILE = os.getenv(
    "RSI_STATE_FILE", os.path.join(DATA_DIR, "rsi_state.json")
)
LIFECYCLE_STATE_FILE = os.getenv(
    "LIFECYCLE_STATE_FILE", os.path.join(DATA_DIR, "lifecycle_state.json")
)
LAST_DIGEST_FILE = os.getenv(
    "LAST_DIGEST_FILE", os.path.join(DATA_DIR, "last_digest.txt")
)
CONFIG_FILE = os.getenv("CONFIG_FILE", os.path.join(DATA_DIR, "config.json"))
STARTED_AT = datetime.now(timezone.utc)
CONFIG_LOCK = asyncio.Lock()
RSI_STATE_LOCK = asyncio.Lock()
LIFECYCLE_STATE_LOCK = asyncio.Lock()
BROADCAST_SEMAPHORE = asyncio.Semaphore(
    25
)  # Global rate limit (Telegram threshold 30/s)
DISPATCH_SEMAPHORE = asyncio.Semaphore(
    1
)  # Serialize signal dispatches to prevent 429 storms

database.init_db()
_ACTIVE_TASKS = set()

# Phase 2: Lifecycle State
active_signal_states = database.load_bot_state("lifecycle_state", {})


def fire_and_forget(coro):
    """
    Creates a background task and tracks it to prevent garbage collection.
    Logs exceptions instead of silently swallowing them.
    """
    task = asyncio.create_task(coro)
    _ACTIVE_TASKS.add(task)

    def _done(t):
        _ACTIVE_TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            print(f"❌ Background task failed: {type(exc).__name__}: {exc}")

    task.add_done_callback(_done)
    return task


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def deep_merge(base, update):
    """Recursively merges update into base."""
    if not isinstance(base, dict) or not isinstance(update, dict):
        return update
    for k, v in update.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = deep_merge(base[k], v)
        else:
            base[k] = v
    return base


DEFAULT_CONFIG = {
    "tracked_coins": ["BTC", "ETH", "SOL", "XRP", "ADA"],
    "powell": {
        "stance": "neutral",
        "score": 3,
        "note": "No manual Powell update yet.",
        "updated_at": None,
    },
    "etf": {
        "summary": "No manual ETF update yet.",
        "bias": "neutral",
        "updated_at": None,
    },
    "rsi": {
        "overbought_threshold": 75,
        "oversold_threshold": 25,
        "volume_multiplier": 1.15,
        "cooldown_hours": 4,
        "intervals": {
            "4h": {"impact": "Moderate", "icon": "🟡"},
            "1d": {"impact": "High", "icon": "🟠"},
            "1w": {"impact": "Very High", "icon": "🔴"},
        },
    },
    "digest": {
        "hour_utc": 4,
        "minute_utc": 0,
        "symbols": ["BTC", "ETH", "SOL"],
    },
    "volume_settings": {
        "high": 120.0,
        "normal": 85.0,
        "low": 50.0,
    },
    "liquidation_settings": {
        "extreme": 500000000.0,
        "high": 250000000.0,
    },
}


def utc_now():
    return datetime.now(timezone.utc)


def ist_now():
    # UTC + 5:30
    ist = utc_now() + timedelta(hours=5, minutes=30)
    return ist.strftime("%Y-%m-%d %I:%M:%S %p IST")


def utc_to_ist(utc_time_str):
    """Converts 'HH:MM UTC' string to 'HH:MM IST' string."""
    try:
        # Example input: "09:15 UTC"
        time_part = utc_time_str.split(" ")[0]
        h, m = map(int, time_part.split(":"))
        m += 30
        h += 5 + (m // 60)
        m %= 60
        h %= 24
        period = "AM" if h < 12 else "PM"
        h_display = h if 0 < h <= 12 else (h - 12 if h > 12 else 12)
        if h_display == 0:
            h_display = 12
        return f"{h_display:02d}:{m:02d} {period} IST"
    except Exception:
        return utc_time_str


def read_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(default, dict) and isinstance(data, dict):
            return deep_merge(default, data)
        return data
    except Exception as exc:
        print(f"Error reading JSON file {path}: {exc}")
        return default


def write_json_file(path, payload):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    os.replace(tmp_path, path)


async def load_config():
    async with CONFIG_LOCK:
        config = await asyncio.to_thread(
            database.load_bot_state, "config", DEFAULT_CONFIG
        )
        # Ensure we always have default keys if missing
        if isinstance(config, dict) and isinstance(DEFAULT_CONFIG, dict):
            config = deep_merge(DEFAULT_CONFIG, config)
        return config


async def update_config(mutator):
    async with CONFIG_LOCK:
        config = await load_config()
        config = mutator(config)
        await asyncio.to_thread(database.save_bot_state, "config", config)
        return config


def load_rsi_state():
    raw_state = database.load_bot_state("rsi_state", {})
    normalized = {}
    for symbol, interval_states in raw_state.items():
        normalized[symbol] = {}
        if not isinstance(interval_states, dict):
            continue
        for interval, value in interval_states.items():
            if isinstance(value, str):
                normalized[symbol][interval] = {
                    "state": value,
                    "last_alert_at": None,
                }
            elif isinstance(value, dict):
                normalized[symbol][interval] = {
                    "state": value.get("state", "NEUTRAL"),
                    "last_alert_at": value.get("last_alert_at"),
                }
    return normalized


async def save_rsi_state(state):
    async with RSI_STATE_LOCK:
        try:
            await asyncio.to_thread(
                database.save_bot_state, "rsi_state", state
            )
        except Exception as exc:
            logger.error(f"Error saving RSI state: {exc}")


def load_lifecycle_state():
    """Loads lifecycle state from Supabase, converting ISO strings back to datetime."""
    raw = database.load_bot_state("lifecycle_state", {})
    for sym, data in raw.items():
        if "last_updated" in data and data["last_updated"]:
            try:
                data["last_updated"] = datetime.fromisoformat(
                    data["last_updated"]
                )
            except Exception:
                data["last_updated"] = None
    return raw


async def save_lifecycle_state(state):
    """Saves lifecycle state to Supabase, converting datetimes to ISO strings."""
    serializable = {}
    for sym, data in state.items():
        serializable[sym] = data.copy()
        if isinstance(data.get("last_updated"), datetime):
            serializable[sym]["last_updated"] = data[
                "last_updated"
            ].isoformat()

    async with LIFECYCLE_STATE_LOCK:
        try:
            await asyncio.to_thread(
                database.save_bot_state, "lifecycle_state", serializable
            )
        except Exception as exc:
            logger.error(f"Error saving lifecycle state: {exc}")


def load_news_state():
    """Load the list of already-sent news article IDs from disk."""
    path = os.path.join(DATA_DIR, "news_state.json")
    return read_json_file(path, [])


def save_news_state(sent_ids):
    """Persist sent article IDs, keeping only the last 200 to prevent unbounded growth."""
    path = os.path.join(DATA_DIR, "news_state.json")
    write_json_file(path, sent_ids[-200:])


def load_macro_state():
    """Load the list of already-alerted macro event IDs."""
    path = os.path.join(DATA_DIR, "macro_state.json")
    return read_json_file(path, [])


def save_macro_state(sent_events):
    """Save the list of already-alerted macro event IDs."""
    path = os.path.join(DATA_DIR, "macro_state.json")
    write_json_file(path, sent_events[-100:])


def has_sent_digest_today(target_date_str):
    if os.path.exists(LAST_DIGEST_FILE):
        try:
            with open(LAST_DIGEST_FILE, "r", encoding="utf-8") as file:
                return file.read().strip() == target_date_str
        except Exception as exc:
            print(f"Error reading last digest file {LAST_DIGEST_FILE}: {exc}")
    return False


def mark_digest_sent(target_date_str):
    try:
        with open(LAST_DIGEST_FILE, "w", encoding="utf-8") as file:
            file.write(target_date_str)
    except Exception as exc:
        print(f"Error writing to last digest file {LAST_DIGEST_FILE}: {exc}")


rsi_state_tracker = load_rsi_state()


async def fetch_with_backoff(session, url, params=None, retries=3):
    for attempt in range(retries):
        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return await response.json()
                if response.status == 429:
                    wait = int(response.headers.get("Retry-After", 2**attempt))
                    print(
                        f"⚠️ API Rate Limited (429) for {url}. Waiting {wait}s..."
                    )
                    await asyncio.sleep(wait)
                    continue
                print(f"API fetch failed ({response.status}) for {url}")
        except Exception as exc:
            print(f"API fetch error on attempt {attempt + 1}: {exc}")
        if attempt < retries - 1:
            await asyncio.sleep(2**attempt)
    return None


async def send_telegram_message(session, chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(3):
        try:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return True
                if response.status == 403:
                    print(
                        f"Telegram send failed (403) for {chat_id} - Revoking access."
                    )
                    await asyncio.to_thread(
                        database.revoke_access, str(chat_id)
                    )
                    return False
                if response.status == 429:
                    wait = (
                        (await response.json())
                        .get("parameters", {})
                        .get("retry_after", 5)
                        if response.content_type == "application/json"
                        else 5
                    )
                    print(
                        f"⚠️ Telegram Rate Limited (429). Waiting {wait}s..."
                    )
                    await asyncio.sleep(wait)
                    continue
                print(
                    f"Telegram send failed ({response.status}) for {chat_id}"
                )
        except Exception as exc:
            print(f"Telegram send error on attempt {attempt + 1}: {exc}")
        await asyncio.sleep(1)
    print(f"Failed to send message to {chat_id}")
    return False


async def send_telegram_message_semaphore(session, chat_id, text):
    """Wrapper for send_telegram_message with a global semaphore."""
    async with BROADCAST_SEMAPHORE:
        return await send_telegram_message(session, chat_id, text)


async def broadcast_message(
    session, text, include_channels=True, include_users=True
):
    sent = 0
    tasks = []

    # 1. Channels
    if include_channels:
        channels = await asyncio.to_thread(database.get_active_channels)
        for chat_id in set(channels):
            tasks.append(
                send_telegram_message_semaphore(session, chat_id, text)
            )

    # 2. Users
    if include_users:
        users = await asyncio.to_thread(database.get_active_users)
        for user_id in set(users):
            tier = await asyncio.to_thread(database.get_user_tier, user_id)
            if tier == "pro":
                watchlist = await asyncio.to_thread(
                    database.get_watchlist, user_id
                )
                if watchlist:
                    mention_found = any(
                        coin.upper() in text.upper() for coin in watchlist
                    )
                    if not mention_found:
                        continue
            tasks.append(
                send_telegram_message_semaphore(session, user_id, text)
            )

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        sent = sum(1 for r in results if r is True)

    return sent


async def dispatch_signal_alert(session, signal_result, current_price):
    """
    Dispatches a confluence-confirmed signal alert to all active users.
    """
    async with DISPATCH_SEMAPHORE:
        symbol = signal_result["symbol"]
        score = signal_result["score"]
        confluence = signal_result.get("confluence_count", score // 16)
        direction = signal_result["direction"]
        meter = signal_result["meter"]
        rationale = signal_result["rationale"]
        structure = signal_result.get("structure", "WEAK")
        lifecycle_state = signal_result.get("lifecycle_state", "NEUTRAL")
        lifecycle_zone = signal_result.get("lifecycle_zone")
        macro_trend = signal_result.get("macro_trend", "NEUTRAL")
        micro_trend = signal_result.get("structure", "NEUTRAL")
        rsi_raw = signal_result.get("rsi_raw", 50.0)

        direction_emoji = "🟢" if direction == "LONG" else "🔴"
        direction_label = (
            "LONG — Accumulation Setup"
            if direction == "LONG"
            else "SHORT — Distribution Setup"
        )

        # 1. QUICK DECISION (Aligned with 3/6 mandatory threshold)
        if confluence >= 5:
            decision = "✅ ACTIONABLE — high-conviction setup"
        elif confluence >= 4:
            decision = "⚠️ CONFIRMED — wait for entry level"
        else:
            decision = "🟡 EARLY — confluence confirmed (3/6)"
        quick_decision = f"📌 Quick Decision: {decision}"

        # 2. STRENGTH DISPLAY MAPPING
        strength_display = {
            "HIGH": "High Conviction — multiple factors aligned",
            "STRONG": "Strong Setup — trend and volume confirmed",
            "MODERATE": "Moderate — early confirmation",
            "EARLY": "Early Stage — watching for more confluence",
        }.get(signal_result["strength"], signal_result["strength"])

        rationale_lines = "\n".join([f"  • {html_safe(r)}" for r in rationale])

        # 4. MARKET STRUCTURE DISPLAY
        structure_display = {
            "HH_HL": "🟢 Bullish BOS confirmed (Higher High/Low)",
            "LH_LL": "🔴 Bearish BOS confirmed (Lower High/Low)",
            "WEAK": "🟡 Structure neutral — ranging",
        }.get(structure, "🟡 Structure unclear")

        # 4b. LIFECYCLE STAGE DISPLAY
        stage_display = {
            "WATCH": "👁️ WATCH — Near key level",
            "EARLY": "⚡ EARLY — Momentum building",
            "CONFIRMED": "✅ CONFIRMED — High-confidence setup",
            "INVALIDATED": "❌ INVALIDATED — Setup broken",
            "NEUTRAL": "⏸️ NEUTRAL — No active setup",
        }.get(lifecycle_state, f"⏸️ {lifecycle_state}")

        # 5. GEMINI "WHAT TO WATCH"
        prompt = (
            f"You are a crypto market analyst. Given this {direction} signal on {symbol} at ${current_price}:\n"
            f"Factors: {signal_result['factors']}\n"
            f"Confluence: {confluence}/6\n\n"
            f"Provide EXACTLY two lines in clear trader language:\n"
            f"Line 1: Hold above $[support] = strength\n"
            f"Line 2: Drop below $[invalidation] = failure\n"
            f"Rules: BTC: $50 precision, ETH/SOL: $5, others: $0.05. No fluff."
        )

        try:
            raw_res = await call_gemini_rest(
                session, prompt
            ) or await call_groq_rest(session, prompt)
            if raw_res and len(raw_res.strip().split("\n")) == 2:
                what_to_watch = html_safe(raw_res.strip())
            else:
                support = (
                    current_price * 0.98
                    if direction == "LONG"
                    else current_price * 1.02
                )
                invalid = (
                    current_price * 0.95
                    if direction == "LONG"
                    else current_price * 1.05
                )
                what_to_watch = f"Hold above ${support:,.2f} = strength\nDrop below ${invalid:,.2f} = failure"
        except Exception:
            what_to_watch = "Monitor local support/resistance for exit."

        # 6. INSIGHT LINE
        if confluence >= 5:
            insight = "📍 Insight: Prime setup — high probability reversal"
        elif confluence >= 4:
            insight = "📍 Insight: Strong momentum — watch for breakout"
        else:
            insight = (
                "📍 Insight: Confluence confirmed — wait for price action"
            )

        # ---- PRO MESSAGE STRUCTURE ----
        zone_line = (
            f"📍 Zone: Near {lifecycle_zone.lower()}\n"
            if lifecycle_zone
            else ""
        )
        pro_msg = (
            f"🚨 <b>{symbol} Intelligence Signal</b>\n\n"
            f"Direction: {direction_emoji} {direction_label}\n"
            f"Price: ${current_price:,.2f}\n"
            f"{quick_decision}\n\n"
            f"📊 Confidence: {meter} ({confluence}/6)\n"
            f"👉 {strength_display}\n"
            f"Structure: {structure_display}\n"
            f"📍 Stage: {stage_display}\n"
            f"{zone_line}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Intelligence Factors</b>\n"
            f"{rationale_lines}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>What to Watch</b>\n"
            f"{what_to_watch}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{insight}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<i>Market analysis only. Not financial advice.</i>"
        )

        # ---- FREE MESSAGE STRUCTURE (Zero Gemini + State Aware) ----
        free_summary = generate_free_summary(
            macro_trend,
            micro_trend,
            rsi_raw,
            direction,
            lifecycle_state,
            lifecycle_zone,
        )

        if score >= 70:
            action = "✅ Actionable — watch levels"
        elif score >= 55:
            action = "⚠️ Wait before entering"
        else:
            action = "❌ Skip — weak setup"

        free_msg = (
            f"🚨 <b>{symbol} Signal Detected</b>\n\n"
            f"Direction: {direction_emoji} {direction_label}\n"
            f"Price: ${current_price:,.2f}\n\n"
            f"📝 {html_safe(free_summary)}\n"
            f"🎯 Action: {action}\n\n"
            f"📊 Confidence: {meter} {score}%\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💎 Want to know WHY this signal fired\n"
            f"and the exact price levels? → /upgrade\n"
            f"━━━━━━━━━━━━━━━\n"
        )

        # ---- DISPATCH WITH CONCURRENCY CONTROL ----
        users_with_tiers = await asyncio.to_thread(
            database.get_all_users_with_tiers
        )
        active_channels = await asyncio.to_thread(database.get_active_channels)

        tasks = []

        # Prepare user tasks
        for row in users_with_tiers:
            uid, tier = row[0], row[2]
            if tier == "pro":
                watchlist = await asyncio.to_thread(
                    database.get_watchlist, uid
                )
                if watchlist and symbol.upper() not in [
                    c.upper() for c in watchlist
                ]:
                    continue
            msg = pro_msg if tier == "pro" else free_msg
            tasks.append(send_telegram_message_semaphore(session, uid, msg))

        # Prepare channel tasks
        for cid in active_channels:
            tasks.append(
                send_telegram_message_semaphore(session, cid, pro_msg)
            )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            print(
                f"✅ Dispatched {symbol} signal to {len(tasks)} recipients in parallel."
            )


async def dispatch_watch_alert(session, symbol, price, lifecycle):
    """
    Sends a Watch Zone alert if the coin enters proximity to key levels.
    Enforces a 6-hour cooldown per coin to prevent noise.
    """
    # Use a global-like identifier for the watch alert cooldown
    can_send = await asyncio.to_thread(
        database.claim_cooldown_for_user,
        "GLOBAL_WATCH",
        symbol,
        "WATCH_ZONE",
        360,
    )
    if not can_send:
        return

    zone = lifecycle.get("zone", "LEVEL")
    support = lifecycle.get("support", 0)
    resistance = lifecycle.get("resistance", 0)

    emoji = "👁️"
    msg = (
        f"{emoji} <b>WATCH ZONE: {symbol}</b>\n\n"
        f"Price is within 1.5% of key HTF {zone.lower()}.\n"
        f"Current Price: ${price:,.2f}\n\n"
        f"📊 <b>Levels to Watch</b>\n"
        f"  • Support: ${support:,.2f}\n"
        f"  • Resistance: ${resistance:,.2f}\n\n"
        f"<i>Potential setup developing. Wait for confluence confirmation.</i>"
    )

    await broadcast_message(session, msg)
    await asyncio.to_thread(
        database.log_alert, "watch_zone", symbol, msg, iso_now()
    )
    print(f"👁️ Watch alert sent for {symbol} at ${price:,.2f}")


# ---------------------------------------------------------------------------
# Free Tier Summary (Zero Gemini + State Aware)
# ---------------------------------------------------------------------------


def generate_free_summary(
    macro_trend: str,
    micro_trend: str,
    rsi: float,
    direction: str,
    state: str = "NEUTRAL",
    zone: str = None,
) -> str:
    """
    Generates free tier signal summary using pure logic — no Gemini call.
    Now includes lifecycle state + zone awareness for richer context.
    """
    # Priority 1: Lifecycle state-specific messages
    if state == "WATCH" and zone:
        direction_word = "bounce" if zone == "SUPPORT" else "rejection"
        return (
            f"Price is near key {zone.lower()}. Watch for a {direction_word}."
        )
    if state == "EARLY":
        return "Early signal forming. Not confirmed yet — wait for follow-through."
    if state == "CONFIRMED":
        return "High-confidence setup detected. Momentum is in play."
    if state == "INVALIDATED":
        return "Previous setup invalidated. Market is resetting."

    # Priority 2: Macro + Micro + RSI combinations
    if macro_trend == "BULL" and micro_trend == "BULLISH" and rsi <= 30:
        return "Oversold in an uptrend — early bounce conditions forming. Not confirmed yet."
    elif macro_trend == "BULL" and micro_trend == "BULLISH" and rsi >= 70:
        return "Overbought in a strong uptrend — momentum stretched. Watch for a pullback."
    elif macro_trend == "BULL" and micro_trend == "BEARISH":
        return "Daily trend is up but short-term momentum is turning down. Mixed — wait for clarity."
    elif macro_trend == "BULL" and micro_trend == "WEAK":
        return "Uptrend intact but short-term direction is unclear. No confirmed setup yet."
    elif macro_trend == "BEAR" and micro_trend == "BEARISH" and rsi >= 70:
        return "Overbought in a downtrend — high risk setup. Pullback likely."
    elif macro_trend == "BEAR" and micro_trend == "BEARISH":
        return "Both daily and short-term trends pointing down. No reversal signal yet."
    elif macro_trend == "BEAR" and micro_trend == "BULLISH":
        return "Short-term bounce inside a downtrend — counter-trend move. Higher risk."
    elif macro_trend == "BEAR" and micro_trend == "WEAK":
        return "Downtrend with unclear short-term direction. Wait for a clearer signal."
    elif rsi <= 30:
        return "Oversold reading detected. Bounce possible but needs trend confirmation."
    elif rsi >= 70:
        return "Overbought reading detected. Pullback possible — wait for structure to confirm."
    else:
        return "Mixed signals at this time. No high-confidence setup detected."


# ---------------------------------------------------------------------------
# Signal Lifecycle State Machine
# ---------------------------------------------------------------------------


def calculate_key_levels(
    highs: list, lows: list, percentile: int = 10
) -> dict:
    """
    Calculates support and resistance from recent 20 candles.
    Uses percentile-based outlier filtering instead of fixed indices.
    """
    if len(highs) < 20 or len(lows) < 20:
        return {
            "support": min(lows[-10:]) if lows else 0,
            "resistance": max(highs[-10:]) if highs else 0,
        }
    sorted_lows = sorted(lows[-20:])
    sorted_highs = sorted(highs[-20:])
    idx = max(1, len(sorted_lows) * percentile // 100)
    return {
        "support": sorted_lows[idx],
        "resistance": sorted_highs[-(idx + 1)],
    }


def calculate_zone_buffer(
    highs: list, lows: list, closes: list, period: int = 14
) -> float:
    """
    ATR-based dynamic zone buffer width.
    Adapts to each asset's volatility instead of fixed 1.5%.
    """
    trs = []
    for i in range(1, min(period + 1, len(closes))):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else closes[-1] * 0.015


def evaluate_signal_lifecycle(
    symbol: str,
    price: float,
    rsi: float,
    highs: list,
    lows: list,
    closes: list,
) -> dict:
    """
    Evaluates signal lifecycle state machine for a symbol.
    Transitions: NEUTRAL → WATCH → EARLY → CONFIRMED → INVALIDATED
    Fast-track: NEUTRAL → CONFIRMED (if RSI extreme AND near key level)
    """
    levels = calculate_key_levels(highs, lows)
    atr_buffer = calculate_zone_buffer(highs, lows, closes)

    prev = active_signal_states.get(symbol, {})
    prev_state = prev.get("state", "NEUTRAL")
    last_updated = prev.get("last_updated")

    new_state = prev_state
    zone = None

    # Determine zone using ATR buffer
    if price <= (levels["support"] + atr_buffer):
        zone = "SUPPORT"
    elif price >= (levels["resistance"] - atr_buffer):
        zone = "RESISTANCE"

    # State transitions
    if prev_state in ("NEUTRAL", "INVALIDATED"):
        if zone and (rsi <= 30 or rsi >= 70):
            # Fast-track: extreme RSI + near key level → skip to CONFIRMED
            new_state = "CONFIRMED"
        elif zone:
            new_state = "WATCH"

    elif prev_state == "WATCH":
        if rsi <= 35 or rsi >= 65:
            new_state = "EARLY"
        elif not zone:
            # Price drifted away from level — reset
            new_state = "NEUTRAL"

    elif prev_state == "EARLY":
        if rsi <= 30 or rsi >= 70:
            new_state = "CONFIRMED"
        elif not zone:
            new_state = "NEUTRAL"

    elif prev_state == "CONFIRMED":
        # Invalidation: price breaks through level by > 1 ATR
        if zone == "SUPPORT" and price < (levels["support"] - atr_buffer):
            new_state = "INVALIDATED"
            zone = None
        elif zone == "RESISTANCE" and price > (
            levels["resistance"] + atr_buffer
        ):
            new_state = "INVALIDATED"
            zone = None
        elif not zone:
            # DRIFT LOGIC: If price drifts > 5% away from level, reset to NEUTRAL
            dist_support = abs(price - levels["support"]) / levels["support"]
            dist_resist = (
                abs(price - levels["resistance"]) / levels["resistance"]
            )
            if dist_support > 0.05 and dist_resist > 0.05:
                new_state = "NEUTRAL"

    # Cooldown: don't re-enter WATCH within 6 hours
    if new_state == "WATCH" and prev_state == "WATCH":
        if (
            last_updated
            and (datetime.now(timezone.utc) - last_updated).total_seconds()
            < 21600
        ):
            return active_signal_states[symbol]

    # Update timestamp only on state change
    if new_state != prev_state or not last_updated:
        last_updated = datetime.now(timezone.utc)

    active_signal_states[symbol] = {
        "state": new_state,
        "zone": zone,
        "support": levels["support"],
        "resistance": levels["resistance"],
        "atr_buffer": atr_buffer,
        "last_updated": last_updated,
    }

    return active_signal_states[symbol]


async def send_invalidation_notice(
    session, symbol: str, support: float, resistance: float
):
    """
    Sends invalidation notification to Pro users when a signal breaks.
    Uses concurrent dispatch with rate-limit protection.
    """
    msg = (
        f"⚠️ <b>{symbol} Signal Invalidated</b>\n\n"
        f"Previous setup has been broken.\n"
        f"Key support was ${support:,.2f} — Key resistance was ${resistance:,.2f}\n\n"
        f"The price has moved beyond these levels, invalidating the active setup.\n"
        f"Wait for a new signal to form.\n\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )
    users_with_tiers = await asyncio.to_thread(
        database.get_all_users_with_tiers
    )

    tasks = []
    for row in users_with_tiers:
        uid, tier = row[0], row[2]
        if tier == "pro":
            tasks.append(send_telegram_message_semaphore(session, uid, msg))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        print(
            f"📡 Invalidation notice for {symbol} sent to {len(tasks)} Pro users."
        )


# ---------------------------------------------------------------------------
# Bounded Hash Cache (Anti-Spam Deduplication)
# ---------------------------------------------------------------------------


class BoundedHashCache(OrderedDict):
    """Fixed-size LRU cache for message dedup hashes. Prevents memory leak."""

    def __init__(self, max_size=5000):
        super().__init__()
        self.max_size = max_size

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.max_size:
            self.popitem(last=False)


last_sent_hashes = BoundedHashCache(5000)


# ---------------------------------------------------------------------------
# Signal Cache Validity
# ---------------------------------------------------------------------------

_signal_cache = {}


def is_signal_cache_valid(cache: dict, max_age_hours: int = 6) -> bool:
    """
    Returns True if the cache exists and was populated within max_age_hours.
    Used to prevent serving stale signal data if the scheduler misses a cycle.
    """
    if not cache or not cache.get("timestamp"):
        return False
    age = datetime.now(timezone.utc) - cache["timestamp"]
    return age < timedelta(hours=max_age_hours)


def html_safe(value):
    return html.escape(str(value), quote=False)


def symbol_to_pair(symbol):
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def pair_to_tag(symbol):
    return symbol.replace("USDT", "")


def pair_to_symbol(pair):
    return pair.replace("USDT", "")


def parse_timeframe_state(symbol, interval):
    symbol_state = rsi_state_tracker.setdefault(symbol, {})
    interval_state = symbol_state.get(interval)
    if isinstance(interval_state, dict):
        interval_state.setdefault("state", "NEUTRAL")
        interval_state.setdefault("last_alert_at", None)
        return interval_state
    if isinstance(interval_state, str):
        interval_state = {"state": interval_state, "last_alert_at": None}
    else:
        interval_state = {"state": "NEUTRAL", "last_alert_at": None}
    symbol_state[interval] = interval_state
    return interval_state


def cooldown_complete(last_alert_at, cooldown_hours):
    if not last_alert_at:
        return True
    try:
        alert_time = datetime.fromisoformat(last_alert_at)
    except ValueError:
        return True
    return utc_now() - alert_time >= timedelta(hours=cooldown_hours)


def classify_bias(text):
    lowered = text.lower()
    positive_terms = ("inflow", "bullish", "positive", "buying", "strong")
    negative_terms = ("outflow", "bearish", "negative", "selling", "weak")
    if any(term in lowered for term in positive_terms):
        return "bullish"
    if any(term in lowered for term in negative_terms):
        return "bearish"
    return "neutral"


async def fetch_sma_trend(session, symbol, interval, limit=50):
    pair = symbol_to_pair(symbol)
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": pair, "interval": interval, "limit": limit}
    candles = await fetch_with_backoff(session, url, params)
    if not candles:
        return 0.0
    close_prices = [float(candle[4]) for candle in candles]
    return sum(close_prices) / len(close_prices)


async def fetch_coin_liquidations(session, symbol):
    shorts = await asyncio.to_thread(
        database.get_coin_liquidations_30min, symbol, "BUY"
    )
    longs = await asyncio.to_thread(
        database.get_coin_liquidations_30min, symbol, "SELL"
    )
    return {"longs": longs, "shorts": shorts}


async def get_market_context(session, symbol):
    pair = symbol_to_pair(symbol)
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": pair, "interval": "4h", "limit": 50}
    candles = await fetch_with_backoff(session, url, params)
    if candles:
        close_prices = [float(candle[4]) for candle in candles]
        current_price = close_prices[-1]
        sma_50 = sum(close_prices) / len(close_prices)
        trend = "BULLISH 🟢" if current_price > sma_50 else "BEARISH 🔴"
        return trend, sma_50
    return "NEUTRAL ⚪", 0.0


async def calculate_confluence_score(total_value, side, trend):
    base_score = 0
    insight = ""
    if side == "BUY":
        base_score = 5 + (total_value / 500000)
    elif side == "SELL":
        base_score = -5 - (total_value / 500000)

    if trend == "BULLISH 🟢" and side == "BUY":
        final_score = base_score * 1.5
        insight = "Short liquidation cascade propelling the bull trend higher."
    elif trend == "BEARISH 🔴" and side == "BUY":
        final_score = base_score * 0.5
        insight = (
            "Weak short squeeze against a bearish trend. Watch for fade risk."
        )
    elif trend == "BEARISH 🔴" and side == "SELL":
        final_score = base_score * 1.5
        insight = "Longs capitulating. Bearish momentum is accelerating."
    elif trend == "BULLISH 🟢" and side == "SELL":
        final_score = base_score * 0.5
        insight = (
            "Long flush inside a bullish structure. Trend remains intact."
        )
    else:
        final_score = base_score
        insight = "Choppy market conditions. Signal strength remains neutral."

    abs_score = min(abs(final_score), 20)
    confidence = int((abs_score / 20) * 100)
    return final_score, confidence, insight


def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(prices)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


_AI_SUMMARY_CACHE = {}
_MARKET_SNAPSHOT_CACHE = {}
_AI_CACHE_LOCK = asyncio.Lock()
_MARKET_CACHE_LOCK = asyncio.Lock()


async def fetch_symbol_snapshot(session, symbol, config):
    now = datetime.now()
    async with _MARKET_CACHE_LOCK:
        if symbol in _MARKET_SNAPSHOT_CACHE:
            val, ts = _MARKET_SNAPSHOT_CACHE[symbol]
            if (now - ts).total_seconds() < 30:
                return val

        pair = symbol_to_pair(symbol)
        url = "https://api.binance.com/api/v3/klines"
        # Fetch 50 candles to calculate 50-day SMA
        params = {"symbol": pair, "interval": "1d", "limit": 50}
        candles = await fetch_with_backoff(session, url, params)
        if not candles:
            return None

        close_prices = [float(c[4]) for c in candles]
        volumes = [float(c[5]) for c in candles]
        current_price = close_prices[-1]
        prev_price = (
            close_prices[-2] if len(close_prices) > 1 else current_price
        )

        # Trend: Price relative to 50-day SMA
        sma_50 = sum(close_prices) / len(close_prices)

        # Volume: Current 24h vs 20-day Average
        current_vol = volumes[-1]  # Most recent 24h volume
        avg_vol_20 = (
            sum(volumes[-21:-1]) / 20
            if len(volumes) >= 21
            else (
                sum(volumes[:-1]) / len(volumes[:-1])
                if len(volumes) > 1
                else current_vol
            )
        )
        vol_ratio = current_vol / avg_vol_20 if avg_vol_20 else 1.0

        rsi = calculate_rsi(close_prices)
        change_24h = (
            ((current_price - prev_price) / prev_price * 100)
            if prev_price
            else 0.0
        )

        # Circle Emoji strictly for 50-day SMA alignment
        trend_emoji = "🟢" if current_price > sma_50 else "🔴"

        res = {
            "symbol": pair,
            "tag": pair_to_tag(pair),
            "price": current_price,
            "sma_50": sma_50,
            "change_24h": change_24h,
            "trend_emoji": trend_emoji,
            "rsi": rsi,
            "vol_ratio": vol_ratio,
            "volume_status": (
                "HIGH 🟢"
                if vol_ratio > 1.5
                else "NORMAL ⚪" if vol_ratio > 0.8 else "LOW 🔴"
            ),
        }
        _MARKET_SNAPSHOT_CACHE[symbol] = (res, now)
        return res


def calculate_market_pulse(snapshots, config):
    score = 50
    bulls, bears = 0, 0
    for s in snapshots:
        if not s:
            continue
        if s["trend_emoji"] == "🟢":
            score += 8
            bulls += 1
        else:
            score -= 12
            bears += 1

        if s["rsi"] >= 65:
            score += 5
        elif s["rsi"] <= 35:
            score -= 10
        if s["change_24h"] >= 2:
            score += 4
        elif s["change_24h"] <= -2:
            score -= 6

    powell_score = int(config["powell"]["score"])
    score += (3 - powell_score) * 4
    if config["etf"]["bias"] == "bullish":
        score += 10
    elif config["etf"]["bias"] == "bearish":
        score -= 10

    score = max(0, min(100, score))
    label = (
        "EXTREME FEAR"
        if score <= 25
        else (
            "FEAR"
            if score <= 40
            else (
                "NEUTRAL"
                if score <= 60
                else "GREED" if score <= 75 else "EXTREME GREED"
            )
        )
    )
    watch_next = (
        "Watch for potential overextension."
        if score > 75
        else (
            "Watch for capitulation bounce."
            if score < 25
            else "Watch for trend continuation."
        )
    )
    return {"score": score, "label": label, "watch_next": watch_next}


GEMINI_MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


async def call_gemini_rest(session, prompt):
    if not gemini_rotator.has_keys():
        return None
    for _ in range(len(gemini_rotator.get_all_keys())):
        idx, key = await gemini_rotator.reserve_key()
        if not key:
            return None
        for model in GEMINI_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={key}"
            try:
                async with session.post(
                    url,
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=15,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["candidates"][0]["content"]["parts"][0][
                            "text"
                        ].strip()
                    if resp.status == 429:
                        await gemini_rotator.advance_after_rate_limit(idx)
                        break
            except Exception:
                continue
    return None


async def call_groq_rest(session, prompt):
    if not groq_rotator.has_keys():
        return None
    for _ in range(len(groq_rotator.get_all_keys())):
        idx, key = await groq_rotator.reserve_key()
        if not key:
            continue
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            async with session.post(
                url, json=payload, headers=headers, timeout=15
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                if resp.status == 429:
                    await groq_rotator.advance_after_rate_limit(idx)
                    continue
        except Exception:
            continue
    return None


async def get_ai_digest_insights(session, snapshots, pulse):
    """
    Shared Gemini Cache for the daily digest.
    Fetches Trader Read, Master Bias, and Global Summary in ONE call.
    """
    cache_key = "|".join(
        [f"{s['tag']}:{round(s['price'], 1)}" for s in snapshots if s]
    )
    async with _AI_CACHE_LOCK:
        if cache_key in _AI_SUMMARY_CACHE:
            val, ts = _AI_SUMMARY_CACHE[cache_key]
            if (
                datetime.now() - ts
            ).total_seconds() < 3600:  # 1 hour cache for digest
                return val

        prompt = (
            f"Act as a Senior Crypto Macro Analyst. Analyze this data:\n"
            f"Pulse: {pulse['score']}/100 ({pulse['label']})\n"
            f"Assets: {[{'coin': s['tag'], 'price': s['price'], 'change': s['change_24h']} for s in snapshots if s]}\n\n"
            f"Provide exactly 3 sections in valid JSON format:\n"
            f"1. trader_read: A 1-sentence sharp market intuition (e.g., 'Bulls defending $60k but volume remains thin')\n"
            f"2. bias: One word (BULLISH, BEARISH, or NEUTRAL)\n"
            f"3. summary: A 2-sentence macro summary of the current situation.\n"
        )

        try:
            res_text = await call_gemini_rest(
                session, prompt
            ) or await call_groq_rest(session, prompt)
            if res_text:
                # Basic JSON extraction in case AI adds markdown
                match = re.search(r"\{.*\}", res_text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    result = {
                        "trader_read": data.get(
                            "trader_read",
                            "Market is consolidating at key levels.",
                        ),
                        "bias": data.get("bias", "NEUTRAL").upper(),
                        "summary": data.get(
                            "summary",
                            "Market pulse is neutral with balanced buy/sell pressure.",
                        ),
                    }
                    _AI_SUMMARY_CACHE[cache_key] = (result, datetime.now())
                    return result
        except Exception:
            pass

        # Fallback
        fallback = {
            "trader_read": "Market is in an accumulation phase.",
            "bias": "NEUTRAL",
            "summary": "The market is currently showing mixed signals with neutral momentum across major assets.",
        }
        return fallback


async def get_ai_market_summary(session, snapshots, pulse):
    # Keep legacy for single signal alerts if needed, or redirect to shared
    insights = await get_ai_digest_insights(session, snapshots, pulse)
    return insights["summary"]


async def build_digest_message(
    session,
    snapshots,
    pulse,
    config,
    top_news=None,
    upcoming_macro=None,
    liq_stats=None,
    macro_partial_failure=False,
    user_tier="free",
):
    """
    Builds a comprehensive Daily Market Digest with tiered output (Pro vs Free).
    """
    date_str = utc_now().strftime("%B %d, %Y")

    # Fetch Shared AI Insights
    ai = await get_ai_digest_insights(session, snapshots, pulse)

    if user_tier == "pro":
        # ---- PREMIUM INTELLIGENCE DIGEST ----
        lines = [
            "💎 <b>PREMIUM INTELLIGENCE DIGEST</b>",
            f"📅 {date_str}",
            "",
            f"🌡️ <b>Market Pulse:</b> {pulse['label']} ({pulse['score']}/100)",
            f"🎯 <b>Master Bias:</b> {ai['bias']}",
            f"💡 <b>Trader Read:</b> {ai['trader_read']}",
            "━━━━━━━━━━━━━━━",
            "",
            "<b>📈 Intelligence Layers</b>",
        ]

        for s in snapshots:
            if not s:
                continue
            # Circle = 50 MA trend, Price change = secondary
            lines.append(
                f"{s['trend_emoji']} <b>#{s['tag']}</b>: ${s['price']:,.2f} ({s['change_24h']:+.2f}%)\n"
                f"   RSI: {s['rsi']:.1f} | Vol: {s['volume_status']} ({s['vol_ratio']:.1f}x)"
            )

        if liq_stats:
            total_long = liq_stats.get("longs", 0) or 0
            total_short = liq_stats.get("shorts", 0) or 0
            lines.append("\n<b>💥 24H Liquidations</b>")
            lines.append(
                f"  🔴 Longs: ${total_long:,.0f} | 🟢 Shorts: ${total_short:,.0f}"
            )

        if upcoming_macro:
            lines.append("\n<b>📆 Macro Calendar (High Impact)</b>")
            for event in upcoming_macro[:3]:
                impact_icon = "🔴" if event.get("impact") == "HIGH" else "🟡"
                lines.append(
                    f"  {impact_icon} {html_safe(event.get('title'))}"
                )

        lines.append(f"\n🤖 <b>Global Summary:</b> {ai['summary']}")
        lines.append("\n━━━━━━━━━━━━━━━")
        lines.append("<i>Market analysis only. Not financial advice.</i>")

    else:
        # ---- STANDARD DAILY DIGEST (Free) ----
        lines = [
            "📊 <b>DAILY MARKET DIGEST</b>",
            f"📅 {date_str}",
            "",
            f"🌡️ <b>Market Pulse:</b> {pulse['label']} ({pulse['score']}/100)",
            f"👀 Watch: {pulse['watch_next']}",
            "━━━━━━━━━━━━━━━",
            "",
            "<b>📈 Market Overview</b>",
        ]

        for s in snapshots:
            if not s:
                continue
            lines.append(
                f"{s['trend_emoji']} <b>#{s['tag']}</b>: ${s['price']:,.2f} ({s['change_24h']:+.2f}%)"
            )

        lines.append("\n━━━━━━━━━━━━━━━")
        lines.append(f"🤖 <b>Summary:</b> {ai['summary']}")
        lines.append("\n━━━━━━━━━━━━━━━")
        lines.append(
            "💎 Upgrade to Pro for Trader Read, Bias, and Volume Analysis → /upgrade"
        )
        lines.append("")
        lines.append("<i>Market analysis only. Not financial advice.</i>")

    return "\n".join(lines)


def format_user_label(user_row):
    user_id, username, full_name, status = user_row
    handle = f"@{username}" if username else "no_username"
    return f"- {html_safe(full_name)} ({handle}) [{status}] — <code>{user_id}</code>"


def format_channel_label(channel_row):
    channel_id, channel_name, status = channel_row
    return (
        f"- {html_safe(channel_name)} [{status}] — <code>{channel_id}</code>"
    )


async def build_status_message():
    config = await load_config()
    uptime = utc_now() - STARTED_AT
    tracked = ", ".join(config["tracked_coins"])
    active_chans = await asyncio.to_thread(database.get_active_channels)
    active_users = await asyncio.to_thread(database.get_active_users)

    return (
        "<b>Bot Status</b>\n"
        f"- <b>Uptime:</b> {str(uptime).split('.')[0]}\n"
        f"- <b>Tracked Coins:</b> {html_safe(tracked)}\n"
        f"- <b>Channels:</b> {len(active_chans)}\n"
        f"- <b>Users:</b> {len(active_users)}\n"
        f"- <b>Data Dir:</b> <code>{html_safe(DATA_DIR)}</code>"
    )


def build_help_message(is_admin):
    common_lines = [
        "<b>Available Commands</b>",
        "/start — Register for DM updates",
        "/mycoins — Manage your coin watchlist",
        "/upgrade — Upgrade to Pro tier",
        "/feedback &lt;text&gt; — Send feedback",
        "/help — Show command list",
    ]
    if not is_admin:
        return "\n".join(common_lines)

    admin_lines = [
        "<b>Admin Commands</b>",
        "/status — Bot health summary",
        "/channels — List active channels",
        "/listusers — Detailed user directory",
        "/prolist — List Pro users only",
        "/setpro &lt;id&gt; — Manual Pro upgrade",
        "/setfree &lt;id&gt; — Manual Pro downgrade",
        "/send &lt;message&gt; - Broadcast message",
        "/setsensitivity &lt;number&gt; - Set signal strictness",
        "/testsignal &lt;symbol&gt; - Private intelligence test",
        "/signal &lt;symbol&gt; - Manual broadcast to all users",
        "/digest - Broadcast digest now",
        "/test_digest — Private test digest",
        "/update_powell &lt;stance&gt; &lt;score&gt; &lt;note&gt;",
        "/update_etf &lt;summary&gt;",
        "/renew &lt;name/id&gt;",
        "/lifecycle — Signal lifecycle status",
    ]
    return "\n".join(common_lines + [""] + admin_lines)


async def send_daily_digest(
    session, target_date_str, mark_as_sent=True, target_chat_id=None
):
    config = await load_config()

    # 1. Fetch shared context (Macro, Liq, News) to reuse across users
    top_news = await fetch_top_news(session)
    macro_data = await fetch_macro_events(
        session, horizon_days=2
    )  # 2 days horizon for better planning

    # Filter for High Impact events only as per Master Plan
    upcoming_macro = []
    if macro_data.get("events"):
        upcoming_macro = [
            e for e in macro_data["events"] if e.get("impact") == "HIGH"
        ]

    cutoff_iso = (utc_now() - timedelta(hours=24)).isoformat()
    liq_stats = await asyncio.to_thread(
        database.get_24h_liquidation_stats, cutoff_iso
    )

    async def build_content(symbols_list, user_tier="free"):
        snapshots = []
        for symbol in symbols_list:
            snapshots.append(
                await fetch_symbol_snapshot(session, symbol, config)
            )

        pulse = calculate_market_pulse(snapshots, config)
        return await build_digest_message(
            session,
            snapshots,
            pulse,
            config,
            top_news=top_news,
            upcoming_macro=upcoming_macro,
            liq_stats=liq_stats,
            macro_partial_failure=macro_data.get("partial_failure", False)
            and not upcoming_macro,
            user_tier=user_tier,
        )

    # 2. Prepare Global Content (for Free users and Channels)
    global_digest = await build_content(config["digest"]["symbols"], "free")

    if target_chat_id:
        # Handle specific target (e.g., test command)
        tier = await asyncio.to_thread(database.get_user_tier, target_chat_id)
        digest_to_send = global_digest
        if tier == "pro":
            watchlist = await asyncio.to_thread(
                database.get_watchlist, target_chat_id
            )
            if watchlist:
                digest_to_send = await build_content(watchlist, "pro")
            else:
                digest_to_send = await build_content(
                    config["digest"]["symbols"], "pro"
                )

        await send_telegram_message(session, target_chat_id, digest_to_send)
        await asyncio.to_thread(
            database.log_alert,
            "test_digest",
            "ADMIN",
            digest_to_send,
            iso_now(),
        )
        return digest_to_send

    # 3. Broadcast Mode
    sent = 0
    # Process Channels (Always Global)
    active_channels = await asyncio.to_thread(database.get_active_channels)
    for chat_id in set(active_channels):
        if await send_telegram_message(session, chat_id, global_digest):
            sent += 1

    # Process Users (Personalize for Pro)
    active_users = await asyncio.to_thread(database.get_active_users)
    for user_id in set(active_users):
        tier = await asyncio.to_thread(database.get_user_tier, user_id)
        if tier == "pro":
            watchlist = await asyncio.to_thread(
                database.get_watchlist, user_id
            )
            if watchlist:
                # Build custom content for this specific Pro user
                user_digest = await build_content(watchlist, "pro")
                if await send_telegram_message(session, user_id, user_digest):
                    sent += 1
                continue

        # Free users or Pro without watchlist get global content
        if await send_telegram_message(session, user_id, global_digest):
            sent += 1

    await asyncio.to_thread(
        database.log_alert, "daily_digest", "MULTI", global_digest, iso_now()
    )
    if mark_as_sent:
        await asyncio.to_thread(mark_digest_sent, target_date_str)
    print(f"Daily Digest sent to {sent} recipients.")
    return global_digest


async def scan_coin_logic(
    session, symbol, config, rsi_config, pulse_score, scan_sem
):
    async with scan_sem:
        try:
            pair = symbol_to_pair(symbol)
            for interval, meta in rsi_config["intervals"].items():
                state = parse_timeframe_state(pair, interval)

                # Fetch candles for RSI and local SMA
                candles = await fetch_with_backoff(
                    session,
                    "https://api.binance.com/api/v3/klines",
                    {"symbol": pair, "interval": interval, "limit": 100},
                )
                if not candles:
                    continue

                close_prices = [float(candle[4]) for candle in candles]
                candle_highs = [float(candle[2]) for candle in candles]
                candle_lows = [float(candle[3]) for candle in candles]
                volumes = [float(candle[5]) for candle in candles]
                current_price = close_prices[-1]
                rsi = calculate_rsi(close_prices)

                # Basic direction gate (35/65 for early detection)
                if rsi > 35 and rsi < 65:
                    # Reset state if neutral
                    if state["state"] != "NEUTRAL":
                        state["state"] = "NEUTRAL"
                        await save_rsi_state(rsi_state_tracker)
                    continue

                recent_volumes = (
                    volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
                )
                average_volume = sum(recent_volumes) / max(
                    len(recent_volumes), 1
                )
                volume_ratio = (
                    (volumes[-1] / average_volume) if average_volume else 1.0
                )

                current_state = "OVERBOUGHT" if rsi >= 65 else "OVERSOLD"

                # Check if state changed or we are re-triggering after cooldown
                is_new_state = current_state != state["state"]
                is_cooldown_done = cooldown_complete(
                    state["last_alert_at"], rsi_config["cooldown_hours"]
                )

                if is_new_state or is_cooldown_done:
                    # Fetch extra factors for scoring
                    sma50_4h = await fetch_sma_trend(session, symbol, "4h", 50)
                    sma50_1d = await fetch_sma_trend(session, symbol, "1d", 50)
                    liq_data = await fetch_coin_liquidations(session, symbol)

                    # Market Structure Detection (HH/HL vs LH/LL)
                    structure = get_market_structure(
                        candle_highs, candle_lows, close_prices
                    )

                    symbol_data = {
                        "symbol": symbol,
                        "price": current_price,
                        "sma50": sma50_4h,
                        "rsi": rsi,
                        "volume_ratio": volume_ratio,
                        "trend_4h": (
                            "BULL" if current_price > sma50_4h else "BEAR"
                        ),
                        "trend_1d": (
                            "BULL" if current_price > sma50_1d else "BEAR"
                        ),
                        "closes": close_prices,
                        "highs": candle_highs,
                        "lows": candle_lows,
                    }

                    signal_result = calculate_score(
                        symbol_data, pulse_score, liq_data
                    )

                    if signal_result:
                        # Attach context data for dispatch
                        signal_result["structure"] = structure
                        signal_result["macro_trend"] = symbol_data["trend_1d"]
                        signal_result["rsi_raw"] = rsi
                        signal_result["score"] = (
                            signal_result["confluence_count"] * 16
                        )  # Map 6-confluence to ~100 scale for legacy UI

                        # Signal Lifecycle State Machine (overlay)
                        prev_lifecycle = active_signal_states.get(
                            symbol, {}
                        ).get("state", "NEUTRAL")
                        lifecycle = evaluate_signal_lifecycle(
                            symbol,
                            current_price,
                            rsi,
                            candle_highs,
                            candle_lows,
                            close_prices,
                        )
                        signal_result["lifecycle_state"] = lifecycle["state"]
                        signal_result["lifecycle_zone"] = lifecycle.get("zone")

                        # Send invalidation notice if state just changed to INVALIDATED
                        if (
                            lifecycle["state"] == "INVALIDATED"
                            and prev_lifecycle != "INVALIDATED"
                        ):
                            fire_and_forget(
                                send_invalidation_notice(
                                    session,
                                    symbol,
                                    lifecycle.get("support", 0),
                                    lifecycle.get("resistance", 0),
                                )
                            )

                        # Log to signal_history
                        await asyncio.to_thread(
                            database.log_signal,
                            symbol,
                            "RSI",
                            signal_result["direction"],
                            signal_result["score"],
                            1 if "TREND" in signal_result["factors"] else 0,
                            1 if "VOLUME" in signal_result["factors"] else 0,
                            1 if "RSI" in signal_result["factors"] else 0,
                            (
                                1
                                if "LIQUIDATION" in signal_result["factors"]
                                else 0
                            ),
                            1 if "PULSE" in signal_result["factors"] else 0,
                            1 if "BOS" in signal_result["factors"] else 0,
                            signal_result["strength"],
                            "|".join(signal_result["rationale"]),
                            pulse_score,
                            interval,
                            current_price,
                            rsi,
                            volume_ratio,
                            liq_data["longs"],
                            liq_data["shorts"],
                        )

                        # 3/6 mandatory threshold for confirmation
                        if signal_result["is_confirmed"]:
                            fire_and_forget(
                                dispatch_signal_alert(
                                    session, signal_result, current_price
                                )
                            )
                            state["last_alert_at"] = utc_now().isoformat()
                            print(
                                f"🚀 Signal confirmed: {symbol} {interval} Confluence:{signal_result['confluence_count']}/6"
                            )
                        elif lifecycle["state"] == "WATCH":
                            # Trigger Watch Zone Alert with 6h cooldown
                            fire_and_forget(
                                dispatch_watch_alert(
                                    session, symbol, current_price, lifecycle
                                )
                            )
                        else:
                            print(
                                f"🟡 Signal suppressed (Low Confluence {signal_result['confluence_count']}/6): {symbol}"
                            )

                    state["state"] = current_state
                    await save_rsi_state(rsi_state_tracker)
                    await save_lifecycle_state(active_signal_states)
        except Exception as coin_exc:
            print(f"⚠️ Error processing coin {symbol}: {coin_exc}")


async def rsi_scanner_loop(session):
    print("📡 Intelligence Signal Scanner is Live (Confluence Mode)...")
    scan_sem = asyncio.Semaphore(5)
    while True:
        try:
            config = await load_config()
            tracked_coins = config["tracked_coins"]
            rsi_config = config["rsi"]

            # Fetch Market Pulse once per scan cycle
            pulse_symbols = ["BTC", "ETH", "BNB", "SOL"]
            pulse_snapshots = []
            for ps in pulse_symbols:
                snap = await fetch_symbol_snapshot(session, ps, config)
                if snap:
                    pulse_snapshots.append(snap)

            pulse_result = calculate_market_pulse(pulse_snapshots, config)
            pulse_score = pulse_result["score"]

            tasks = [
                scan_coin_logic(
                    session, symbol, config, rsi_config, pulse_score, scan_sem
                )
                for symbol in tracked_coins
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            await asyncio.sleep(600)
        except Exception as exc:
            print(f"Signal Scanner Error: {exc}")
            traceback.print_exc()
            await asyncio.sleep(60)


async def daily_digest_loop(session):
    print("📈 Daily Digest Engine is Live...")
    while True:
        try:
            config = await load_config()
            now = utc_now()
            target = now.replace(
                hour=config["digest"]["hour_utc"],
                minute=config["digest"]["minute_utc"],
                second=0,
                microsecond=0,
            )
            target_date_str = target.strftime("%Y-%m-%d")

            if now >= target and await asyncio.to_thread(
                has_sent_digest_today, target_date_str
            ):
                target += timedelta(days=1)
                target_date_str = target.strftime("%Y-%m-%d")
            if now < target:
                await asyncio.sleep((target - now).total_seconds())

            if await asyncio.to_thread(has_sent_digest_today, target_date_str):
                continue

            await send_daily_digest(
                session, target_date_str, mark_as_sent=True
            )
            await asyncio.sleep(60)
        except Exception as exc:
            print(f"Daily Digest Error: {exc}")
            await asyncio.sleep(3600)


async def binance_liquidation_listener(session):
    """
    Connects to the Binance Aggregated Force Order stream (!forceOrder@arr).
    Captures ALL symbols market-wide for the $200M+ accurate totals.
    Real-time alerts remain scoped to your tracked coins.

    Reliability: Retries exponentially and alerts Admin if failure persists.
    """
    print("💧 Market-Wide Liquidation Engine is Live...")
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"

    retry_count = 0
    while True:
        try:
            config = await load_config()
            tracked_coins = config.get(
                "tracked_coins", DEFAULT_CONFIG["tracked_coins"]
            )

            async with websockets.connect(
                url, ping_interval=20, ping_timeout=20, close_timeout=10
            ) as websocket:
                print("✅ Connected to Market-Wide WebSocket successfully!")
                retry_count = 0  # Reset on success
                while True:
                    response = await websocket.recv()
                    payload = json.loads(response)

                    # Binance !forceOrder@arr returns an array of events
                    data_list = (
                        payload if isinstance(payload, list) else [payload]
                    )

                    for event in data_list:
                        order_data = event.get("o", {})
                        if not order_data:
                            continue

                        symbol = order_data["s"].replace("USDT", "")
                        side = order_data["S"]
                        price = float(order_data["p"])
                        qty = float(order_data["q"])
                        total_value = price * qty

                        # Store EVERYTHING for global market accuracy
                        await asyncio.to_thread(
                            database.log_liquidation,
                            symbol,
                            side,
                            total_value,
                            iso_now(),
                        )

                        # Alert logic for specific coins only
                        if symbol in tracked_coins:
                            # User requested less strict: $200k floor for "Small Scale"
                            if total_value >= 200000:
                                position_type = (
                                    "SHORT 🔴" if side == "BUY" else "LONG 🟢"
                                )
                                trend, _ = await get_market_context(
                                    session, symbol
                                )
                                _, confidence, insight = (
                                    await calculate_confluence_score(
                                        total_value, side, trend
                                    )
                                )

                                # Floor lowered to 25% (Less strict)
                                if confidence >= 25:
                                    if confidence >= 75:
                                        tag = "🔥 HIGH CONFIDENCE"
                                    elif confidence >= 45:
                                        tag = "🟠 MODERATE CONFIDENCE"
                                    else:
                                        tag = "⚪ SMALL SCALE"

                                    alert_message = (
                                        f"🚀 <b>PREMIUM LIQUIDATION SIGNAL</b>\n\n"
                                        f"<b>Tier:</b> {tag}\n"
                                        f"<b>Asset:</b> #{pair_to_tag(symbol)}\n"
                                        f"<b>Liquidation:</b> {position_type}\n"
                                        f"<b>Value:</b> ${total_value:,.2f}\n"
                                        f"<b>Trend:</b> {trend}\n"
                                        f"<b>Signal Confidence:</b> {confidence}%\n\n"
                                        f"<b>Intelligence Layer:</b> {insight}\n\n"
                                        f"<i>Disclaimer: Market analysis only, not financial advice.</i>"
                                    )
                                    # DECOUPLED: Queue for background broadcast
                                    fire_and_forget(
                                        broadcast_message(
                                            session, alert_message
                                        )
                                    )
                                    await asyncio.to_thread(
                                        database.log_alert,
                                        "liquidation",
                                        symbol,
                                        alert_message,
                                        iso_now(),
                                    )

        except Exception as exc:
            retry_count += 1
            print(
                f"Market-Wide WebSocket Error (Attempt {retry_count}): {exc}"
            )

            if retry_count == 3:
                admin_alert = (
                    f"⚠️ <b>CRITICAL ALERT</b>\n\n"
                    f"Binance Market-Wide Liquidation WebSocket failed to reconnect after 3 attempts.\n"
                    f"<b>Error:</b> {html_safe(str(exc))}"
                )
                await send_telegram_message(
                    session, ADMIN_USER_ID, admin_alert
                )
                print("Admin alerted about WebSocket failure.")

            # Backoff sleep before retrying
            sleep_time = min(60, 5 * retry_count)
            await asyncio.sleep(sleep_time)


async def register_admin_commands(session):
    """Registers commands with Telegram so they appear in the menu button."""
    url = f"{TELEGRAM_API_URL}/setMyCommands"

    # Common commands for everyone
    common_commands = [
        {"command": "start", "description": "Register for alerts"},
        {"command": "help", "description": "Show command list"},
        {"command": "mycoins", "description": "Manage your coin watchlist"},
        {"command": "upgrade", "description": "Upgrade to Pro tier"},
        {"command": "feedback", "description": "Send suggestion/feedback"},
    ]

    # Admin-only commands (Scoped to ADMIN_USER_ID)
    admin_only = common_commands + [
        {"command": "digest", "description": "Get latest market digest"},
        {"command": "status", "description": "Show bot health"},
        {"command": "macro", "description": "Audit upcoming macro events"},
        {"command": "channels", "description": "List active channels"},
        {"command": "users", "description": "List active DM users"},
        {"command": "test_digest", "description": "Test digest privately"},
        {"command": "setpro", "description": "Manual Pro upgrade"},
        {"command": "setfree", "description": "Manual Pro downgrade"},
        {"command": "prolist", "description": "List Pro users only"},
        {"command": "listusers", "description": "Detailed user directory"},
        {"command": "send", "description": "Broadcast message to all users"},
        {"command": "setsensitivity", "description": "Set signal strictness"},
        {"command": "testsignal", "description": "Simulate signal privately"},
        {"command": "signal", "description": "Manual broadcast signal"},
        {"command": "lifecycle", "description": "Signal lifecycle status"},
    ]

    try:
        # 1. Set global commands
        await session.post(url, json={"commands": common_commands})

        # 2. Set admin-specific commands
        admin_scope = {"type": "chat", "chat_id": str(ADMIN_USER_ID)}
        await session.post(
            url, json={"commands": admin_only, "scope": admin_scope}
        )

        print("✅ Telegram Command Menu synchronized (Admin Scope included).")
    except Exception as exc:
        print(f"Error registering commands: {exc}")


async def handle_admin_command(session, command, args, chat_id):
    if command == "/status":
        await send_telegram_message(
            session, chat_id, await build_status_message()
        )
        return

    if command == "/channels":
        all_chans = await asyncio.to_thread(database.get_all_channels)
        channels = [row for row in all_chans if row[2] == "ACTIVE"]
        lines = (
            ["<b>Active Channels</b>"]
            + [format_channel_label(row) for row in channels]
            if channels
            else ["No channels found."]
        )
        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/users":
        all_users = await asyncio.to_thread(database.get_all_users)
        users = [row for row in all_users if row[3] == "ACTIVE"]
        lines = (
            ["<b>Registered Users</b>"]
            + [format_user_label(row) for row in users]
            if users
            else ["No users found."]
        )
        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/setpro":
        target_user = args.strip() if args else ""
        if not target_user or not re.match(r"^-?\d+$", target_user):
            await send_telegram_message(
                session, chat_id, "ℹ️ Usage: /setpro &lt;id&gt;"
            )
            return
        await asyncio.to_thread(
            database.set_user_tier, chat_id, target_user, "pro"
        )
        await send_telegram_message(
            session, chat_id, f"✅ User {target_user} upgraded to Pro."
        )
        # Notify user
        await send_telegram_message(
            session,
            target_user,
            "🌟 <b>ADMIN UPGRADE</b>\n\n"
            "Your account has been upgraded to <b>Pro Tier</b>.\n"
            "You now have access to personalized analysis and advanced alerts.\n\n"
            "Use /mycoins to set your watchlist.",
        )
        return

    if command == "/setfree":
        target_user = args.strip() if args else ""
        if not target_user or not re.match(r"^-?\d+$", target_user):
            await send_telegram_message(
                session, chat_id, "ℹ️ Usage: /setfree &lt;id&gt;"
            )
            return
        await asyncio.to_thread(
            database.set_user_tier, chat_id, target_user, "free"
        )
        await send_telegram_message(
            session, chat_id, f"⚠️ User {target_user} moved to Free tier."
        )
        # Notify user
        await send_telegram_message(
            session,
            target_user,
            "⚠️ <b>ACCOUNT UPDATE</b>\n\n"
            "Your account has been moved to the Free tier.\n"
            "Some premium features are now limited. Upgrade anytime with /upgrade.",
        )
        return

    if command == "/listusers":
        users = await asyncio.to_thread(database.get_all_users_with_tiers)
        total = len(users)
        lines = []
        for u in users:
            uid, uname, tier, expiry = u
            label = f"@{uname}" if uname else uid
            if tier == "pro":
                exp_date = expiry[:10] if expiry else "N/A"
                lines.append(f"💎 {label} — Pro (Exp: {exp_date})")
            else:
                lines.append(f"🆓 {label} — Free")

        for i in range(0, len(lines), 20):
            chunk = lines[i: i + 20]
            header = (
                f"👥 <b>User Directory</b> (Total: {total})\n\n"
                if i == 0
                else ""
            )
            await send_telegram_message(
                session, chat_id, header + "\n".join(chunk)
            )
        return

    if command == "/prolist":
        users = await asyncio.to_thread(database.get_all_users_with_tiers)
        pro_users = [u for u in users if u[2] == "pro"]
        if not pro_users:
            await send_telegram_message(
                session, chat_id, "ℹ️ No Pro users found."
            )
            return

        lines = []
        for uid, uname, tier, expiry in pro_users:
            label = f"@{uname}" if uname else uid
            exp_date = expiry[:10] if expiry else "N/A"
            lines.append(
                f"💎 {label} — ID: <code>{uid}</code> (Exp: {exp_date})"
            )

        header = f"💎 <b>Pro User Directory</b> (Total: {len(pro_users)})\n\n"
        await send_telegram_message(
            session, chat_id, header + "\n".join(lines)
        )
        return

    if command == "/send":
        if not args:
            await send_telegram_message(
                session, chat_id, "Usage: /send &lt;message&gt;"
            )
            return
        message = f"<b>📢 ADMIN BROADCAST</b>\n\n{html_safe(args)}"
        sent = await broadcast_message(session, message)
        await asyncio.to_thread(
            database.log_alert, "admin_broadcast", "MULTI", message, iso_now()
        )
        await send_telegram_message(
            session, chat_id, f"Broadcast delivered to {sent} recipients."
        )
        return

    if command == "/setsensitivity":
        if not args or not args.strip().isdigit():
            current = await asyncio.to_thread(database.get_sensitivity)
            await send_telegram_message(
                session,
                chat_id,
                f"❌ Usage: /setsensitivity &lt;number&gt;\n"
                f"Valid range: 40–90\n\n"
                f"Current threshold: {current}/100\n\n"
                f"• 40 = Most alerts broadcast\n"
                f"• 55 = Default balanced\n"
                f"• 70 = Strict\n"
                f"• 90 = Very strict",
            )
            return
        new_val = int(args.strip())
        if new_val < 40 or new_val > 90:
            await send_telegram_message(
                session,
                chat_id,
                "❌ Invalid value. Must be between 40 and 90.",
            )
            return
        await asyncio.to_thread(database.set_sensitivity, new_val)
        await send_telegram_message(
            session,
            chat_id,
            f"✅ Signal sensitivity updated to {new_val}/100.",
        )
        return

    if command == "/testsignal":
        symbol = args.strip().upper() if args else "BTC"
        await send_telegram_message(
            session,
            chat_id,
            f"🔍 Simulating Private Intelligence Signal for {symbol}...",
        )

        # Fetch real price
        price_url = (
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
        )
        price_data = await fetch_with_backoff(session, price_url)
        current_price = float(price_data["price"]) if price_data else 0.0

        # Fetch real 4H candles for structure
        pair = symbol_to_pair(symbol)
        candles = await fetch_with_backoff(
            session,
            "https://api.binance.com/api/v3/klines",
            {"symbol": pair, "interval": "4h", "limit": 80},
        )
        if not candles:
            await send_telegram_message(
                session, chat_id, "⚠️ Could not fetch candle data. Try again."
            )
            return

        close_prices = [float(c[4]) for c in candles]
        candle_highs = [float(c[2]) for c in candles]
        candle_lows = [float(c[3]) for c in candles]
        rsi = calculate_rsi(close_prices)

        # EMA21
        ema21 = close_prices[0]
        multiplier = 2.0 / (21 + 1)
        for cp in close_prices[1:]:
            ema21 = (cp - ema21) * multiplier + ema21

        structure = get_market_structure(candle_highs, candle_lows)
        if current_price > ema21 and structure == "HH_HL":
            micro_trend = "BULLISH"
            trend_4h = "BULL"
        elif current_price < ema21 and structure == "LH_LL":
            micro_trend = "BEARISH"
            trend_4h = "BEAR"
        else:
            micro_trend = "WEAK"
            trend_4h = "BEAR"

        structure_display = {
            "HH_HL": "🟢 Bullish structure — higher highs and lows forming",
            "LH_LL": "🔴 Bearish structure — lower highs and lows forming",
            "WEAK": "🟡 Structure unclear — no confirmed direction yet",
        }.get(structure, "🟡 Structure unclear")

        sma50_4h = await fetch_sma_trend(session, symbol, "4h", 50)
        sma50_1d = await fetch_sma_trend(session, symbol, "1d", 50)
        liq_data = await fetch_coin_liquidations(session, symbol)

        config = await load_config()
        pulse_snapshots = []
        for ps in ["BTC", "ETH", "BNB", "SOL"]:
            snap = await fetch_symbol_snapshot(session, ps, config)
            if snap:
                pulse_snapshots.append(snap)
        pulse_result = calculate_market_pulse(pulse_snapshots, config)
        pulse_score = pulse_result["score"]

        test_data = {
            "symbol": symbol,
            "price": current_price,
            "sma50": sma50_4h,
            "rsi": rsi,
            "volume_ratio": 1.0,
            "trend_4h": trend_4h,
            "trend_1d": "BULL" if current_price > sma50_1d else "BEAR",
        }

        signal_result = calculate_score(test_data, pulse_score, liq_data)
        if signal_result:
            direction = signal_result["direction"]
            direction_emoji = "🟢" if direction == "LONG" else "🔴"
            direction_label = (
                "LONG — Oversold Setup"
                if direction == "LONG"
                else "SHORT — Overbought Setup"
            )

            def clean_rationale(text):
                for k, v in {
                    "SMA50": "50-day average",
                    "HTF": "short-term and daily trends",
                    "delta": "pressure",
                }.items():
                    text = text.replace(k, v)
                return text

            rationale_lines = "\n".join(
                [
                    f"  • {html_safe(clean_rationale(r))}"
                    for r in signal_result["rationale"]
                ]
            )

            strength_display = {
                "HIGH CONVICTION": "High Confidence — strong setup",
                "STRONG": "Good Confidence — setup developing well",
                "MODERATE": "Medium — needs confirmation",
                "WEAK": "Low — early stage only",
            }.get(signal_result["strength"], signal_result["strength"])

            score = signal_result["score"]
            if score >= 70:
                decision = "✅ ACTIONABLE — levels are your guide"
            elif score >= 55:
                decision = "⚠️ WAIT — not fully confirmed yet"
            else:
                decision = "❌ WEAK — low confidence, skip this one"

            focus = html_safe(
                "High conviction setup. Watch for volume confirmation on the bounce."
                if score >= 80
                else "Monitor for confirmation before acting."
            )

            pro_msg = (
                f"🚨 <b>{html_safe(symbol)} Intelligence Signal</b> (Private Test)\n\n"
                f"Direction: {direction_emoji} {direction_label}\n"
                f"Price: ${current_price:,.2f}\n"
                f"📌 Quick Decision: {decision}\n\n"
                f"📊 Confidence: {signal_result['meter']} {score}%\n"
                f"👉 {strength_display}\n"
                f"Micro (4H): {structure_display}\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<b>Why This Signal Fired</b>\n"
                f"{rationale_lines}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<b>Focus:</b>\n"
                f"{focus}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<i>Market analysis only. Not financial advice.</i>"
            )
            await send_telegram_message(session, chat_id, pro_msg)
        else:
            await send_telegram_message(
                session,
                chat_id,
                f"⚠️ No signal for {symbol} — RSI is in neutral zone ({rsi:.1f}).",
            )
        return

    if command == "/signal":
        symbol = args.strip().upper() if args else "BTC"

        # Check if we have a fresh cached signal
        if is_signal_cache_valid(_signal_cache.get(symbol)):
            cached = _signal_cache[symbol]
            await send_telegram_message(
                session,
                chat_id,
                f"🚨 Broadcasting cached signal for {symbol}...",
            )
            await dispatch_signal_alert(
                session, cached["result"], cached["price"]
            )
            return

        await send_telegram_message(
            session, chat_id, f"🚨 Triggering Manual BROADCAST for {symbol}..."
        )

        # Fetch price
        price_url = (
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
        )
        price_data = await fetch_with_backoff(session, price_url)
        current_price = float(price_data["price"]) if price_data else 0.0

        # Fetch real market context
        liq_data = await fetch_coin_liquidations(session, symbol)
        pulse_snapshots = []
        config = await load_config()
        for ps in ["BTC", "ETH", "BNB", "SOL"]:
            snap = await fetch_symbol_snapshot(session, ps, config)
            if snap:
                pulse_snapshots.append(snap)
        pulse_result = calculate_market_pulse(pulse_snapshots, config)
        pulse_score = pulse_result["score"]

        sma50_4h = await fetch_sma_trend(session, symbol, "4h", 50)
        sma50_1d = await fetch_sma_trend(session, symbol, "1d", 50)

        # Fetch 4H candles for structure detection
        pair = symbol_to_pair(symbol)
        candles = await fetch_with_backoff(
            session,
            "https://api.binance.com/api/v3/klines",
            {"symbol": pair, "interval": "4h", "limit": 80},
        )
        if not candles:
            await send_telegram_message(
                session,
                chat_id,
                "⚠️ Market data is refreshing. Please try again in a few minutes.",
            )
            return

        close_prices = [float(c[4]) for c in candles]
        candle_highs = [float(c[2]) for c in candles]
        candle_lows = [float(c[3]) for c in candles]

        # EMA21
        ema21 = close_prices[0]
        multiplier = 2.0 / (21 + 1)
        for cp in close_prices[1:]:
            ema21 = (cp - ema21) * multiplier + ema21

        structure = get_market_structure(candle_highs, candle_lows)
        if current_price > ema21 and structure == "HH_HL":
            micro_trend = "BULLISH"
            trend_4h = "BULL"
        elif current_price < ema21 and structure == "LH_LL":
            micro_trend = "BEARISH"
            trend_4h = "BEAR"
        else:
            micro_trend = "WEAK"
            trend_4h = "BEAR"

        rsi = calculate_rsi(close_prices)

        test_data = {
            "symbol": symbol,
            "price": current_price,
            "sma50": sma50_4h,
            "rsi": rsi,
            "volume_ratio": 1.0,
            "trend_4h": trend_4h,
            "trend_1d": "BULL" if current_price > sma50_1d else "BEAR",
        }

        signal_result = calculate_score(test_data, pulse_score, liq_data)
        if signal_result:
            signal_result["structure"] = structure
            signal_result["micro_trend"] = micro_trend
            signal_result["macro_trend"] = test_data["trend_1d"]
            signal_result["rsi_raw"] = rsi

            # Cache the result
            _signal_cache[symbol] = {
                "result": signal_result,
                "price": current_price,
                "timestamp": datetime.now(timezone.utc),
            }

            await dispatch_signal_alert(session, signal_result, current_price)
        else:
            await send_telegram_message(
                session,
                chat_id,
                f"⚠️ No actionable signal for {symbol} right now (RSI in neutral zone).",
            )
        return
    if command == "/lifecycle":
        config = await load_config()
        tracked = config["tracked_coins"]
        lines = ["<b>📊 Signal Lifecycle Status</b>\n"]
        for sym in tracked:
            ls = active_signal_states.get(sym, {})
            state = ls.get("state", "NEUTRAL")
            zone = ls.get("zone")
            support = ls.get("support", 0)
            resistance = ls.get("resistance", 0)
            last_upd = ls.get("last_updated")

            state_icons = {
                "NEUTRAL": "⏸️",
                "WATCH": "👁️",
                "EARLY": "⚡",
                "CONFIRMED": "✅",
                "INVALIDATED": "❌",
            }
            icon = state_icons.get(state, "⏸️")

            age_str = "—"
            if last_upd:
                age = datetime.now(timezone.utc) - last_upd
                hours = int(age.total_seconds() // 3600)
                mins = int((age.total_seconds() % 3600) // 60)
                age_str = f"{hours}h {mins}m ago"

            line = f"{icon} <b>#{sym}</b>: {state}"
            if zone:
                line += f" (near {zone.lower()})"
            if support and resistance:
                line += f"\n   S: ${support:,.2f} | R: ${resistance:,.2f}"
            line += f"\n   Updated: {age_str}"
            lines.append(line)

        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/macro":
        result = await fetch_macro_events(session, horizon_days=7)
        events = result.get("events", [])

        # Case 1: Fetch failed AND no events at all
        if not result["success"] and not events:
            await send_telegram_message(
                session,
                chat_id,
                "⚠️ Macro calendar feed unavailable right now. Please try again later.",
            )
            return

        # Case 2: Fetch succeeded but truly no events
        if result["success"] and not events:
            await send_telegram_message(
                session,
                chat_id,
                "📅 No High Impact macro events found for the next 7 days.",
            )
            return

        # Case 4: Events exist
        lines = ["<b>📅 WEEKLY MACRO AUDIT (High Impact)</b>", ""]
        for e in events:
            lines.append(f"• <code>{e['date']} {e['time']}</code>")
            lines.append(f"  <b>{html_safe(e['title'])}</b> ({e['country']})")
            lines.append("")

        await send_telegram_message(
            session, chat_id, "Audit report:\n" + "\n".join(lines)
        )
        return

    if command == "/update_powell":
        parts = args.split(" ", 2)
        if len(parts) < 3:
            await send_telegram_message(
                session,
                chat_id,
                "Usage: /update_powell &lt;stance&gt; &lt;score&gt; &lt;note&gt;",
            )
            return
        stance, score_text, note = parts
        try:
            score = max(1, min(5, int(score_text)))
        except ValueError:
            await send_telegram_message(
                session,
                chat_id,
                "Powell score must be a number between 1 and 5.",
            )
            return

        await update_config(
            lambda config: deep_merge(
                config,
                {
                    "powell": {
                        "stance": stance.lower(),
                        "score": score,
                        "note": note,
                        "updated_at": iso_now(),
                    }
                },
            )
        )
        await send_telegram_message(
            session, chat_id, "Powell context updated successfully."
        )
        return

    if command == "/update_etf":
        if not args:
            await send_telegram_message(
                session, chat_id, "Usage: /update_etf &lt;summary&gt;"
            )
            return
        bias = classify_bias(args)
        await update_config(
            lambda config: deep_merge(
                config,
                {
                    "etf": {
                        "summary": args,
                        "bias": bias,
                        "updated_at": iso_now(),
                    }
                },
            )
        )
        await send_telegram_message(
            session, chat_id, "ETF note updated successfully."
        )
        return

    if command == "/update_coins":
        parts = args.split()
        if len(parts) != 2:
            await send_telegram_message(
                session,
                chat_id,
                "Usage: /update_coins &lt;add/remove&gt; &lt;symbol&gt;",
            )
            return
        action, symbol = parts[0].lower(), parts[1].upper()
        if action not in {"add", "remove"}:
            await send_telegram_message(
                session, chat_id, "Action must be add or remove."
            )
            return
        if action == "remove" and symbol in {"BTC", "ETH", "SOL"}:
            await send_telegram_message(
                session,
                chat_id,
                "BTC, ETH, and SOL stay pinned for the core digest.",
            )
            return

        def mutate(config):
            coins = list(dict.fromkeys(config["tracked_coins"]))
            if action == "add" and symbol not in coins:
                coins.append(symbol)
            if action == "remove" and symbol in coins and len(coins) > 1:
                coins.remove(symbol)

            digest_symbols = [
                coin for coin in config["digest"]["symbols"] if coin in coins
            ]
            for required in ["BTC", "ETH", "SOL"]:
                if required in coins and required not in digest_symbols:
                    digest_symbols.append(required)

            config["tracked_coins"] = coins
            config["digest"]["symbols"] = [
                coin for coin in digest_symbols if coin in coins
            ][:3]
            return config

        updated = await update_config(mutate)
        await send_telegram_message(
            session,
            chat_id,
            f"Tracked coins updated: {', '.join(updated['tracked_coins'])}",
        )
        return

    if command == "/digest":
        target_date_str = utc_now().strftime("%Y-%m-%d")
        await send_daily_digest(session, target_date_str, mark_as_sent=False)
        await send_telegram_message(
            session, chat_id, "Digest broadcasted manually."
        )
        return

    if command == "/test_digest":
        target_date_str = utc_now().strftime("%Y-%m-%d")
        await send_daily_digest(
            session,
            target_date_str,
            mark_as_sent=False,
            target_chat_id=chat_id,
        )
        # Test digest sends a confirmation inside the digest engine output or can be silent.
        return

    if command == "/help":
        await send_telegram_message(session, chat_id, build_help_message(True))
        return

    if command == "/revoke":
        target_customer = args.strip()
        if not target_customer:
            await send_telegram_message(
                session, chat_id, "Usage: /revoke &lt;channel name or id&gt;"
            )
            return
        actual_id = await asyncio.to_thread(
            database.get_channel_id_by_name, target_customer
        )
        success = await asyncio.to_thread(
            database.ban_channel, target_customer
        )
        if success:
            await send_telegram_message(
                session,
                chat_id,
                f"🚫 Access revoked for {html_safe(target_customer)}",
            )
            if actual_id and str(actual_id) != ADMIN_USER_ID:
                leave_url = f"{TELEGRAM_API_URL}/leaveChat"
                await session.post(leave_url, json={"chat_id": str(actual_id)})
        else:
            await send_telegram_message(
                session,
                chat_id,
                f"⚠️ Could not find {html_safe(target_customer)}.",
            )
        return

    if command == "/renew":
        target_customer = args.strip()
        if not target_customer:
            await send_telegram_message(
                session, chat_id, "Usage: /renew &lt;channel name or id&gt;"
            )
            return
        success = await asyncio.to_thread(
            database.renew_channel, target_customer
        )
        if success:
            await send_telegram_message(
                session,
                chat_id,
                f"✅ Access renewed for {html_safe(target_customer)}",
            )
        else:
            await send_telegram_message(
                session,
                chat_id,
                f"⚠️ Could not find {html_safe(target_customer)}.",
            )
        return

    await send_telegram_message(
        session, chat_id, "Unknown admin command. Use /help."
    )


async def news_checker_loop(session):
    """
    Checks CryptoCompare for new crypto headlines every 5 minutes.
    Sends a breaking news alert when a critical or high-importance article
    is found that has not been sent before.
    """
    print("📰 News Checker is Live...")
    sent_ids = await asyncio.to_thread(load_news_state)

    while True:
        try:
            url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"
            data = await fetch_with_backoff(session, url, retries=2)

            if data and data.get("Type") == 100:
                articles = data.get("Data", [])
                new_alerts_sent = False

                for article in articles[:20]:
                    article_id = str(article.get("id", ""))
                    title = article.get("title", "")
                    body_snippet = article.get("body", "")[:200]
                    source = article.get("source_info", {}).get(
                        "name", "Unknown Source"
                    )
                    published_on = article.get("published_on", 0)

                    if article_id in sent_ids:
                        continue

                    # Mark as seen immediately to prevent duplicates
                    sent_ids.append(article_id)

                    # Skip articles older than 30 minutes — prevents
                    # flooding subscribers with stale news after a restart
                    age_seconds = utc_now().timestamp() - published_on
                    if age_seconds > 1800:
                        continue

                    importance_data = classify_news_importance(title)
                    if importance_data["level"] not in (
                        "CRITICAL",
                        "MODERATE",
                    ):
                        continue

                    icon = importance_data["emoji"]
                    alert = (
                        f"<b>{icon} BREAKING NEWS</b>\n\n"
                        f"<b>{html_safe(title)}</b>\n\n"
                        f"<b>Source:</b> {html_safe(source)}\n"
                        f"<b>Impact:</b> {importance_data['level']}\n\n"
                        f"<i>{html_safe(body_snippet)}...</i>\n\n"
                        f"<b>Why it matters:</b> {importance_data['impact']}\n\n"
                        f"<i>Disclaimer: Market analysis only, not financial advice.</i>"
                    )

                    await broadcast_message(session, alert)
                    await asyncio.to_thread(
                        database.log_alert,
                        "breaking_news",
                        "MULTI",
                        alert,
                        iso_now(),
                    )
                    new_alerts_sent = True
                    print(f"Breaking news alert sent: {title[:60]}")

                if new_alerts_sent or len(sent_ids) % 50 == 0:
                    await asyncio.to_thread(save_news_state, sent_ids)

        except Exception as exc:
            print(f"News Checker Error: {exc}")

        await asyncio.sleep(300)  # Check every 5 minutes


async def macro_calendar_loop(session):
    """
    Checks economic calendar for high-impact events and sends an early warning alert
    approx 2 hours before they occur.
    """
    print("🏛️ Macro Calendar Watcher is Live...")
    sent_events = await asyncio.to_thread(load_macro_state)

    while True:
        try:
            result = await fetch_macro_events(session, horizon_days=1)
            if not result["success"]:
                # Silent skip to avoid log spam, fetcher already prints errors
                await asyncio.sleep(3600)  # Wait longer if feed is down
                continue

            events = result["events"]
            now_ts = utc_now().timestamp()

            # 1. Filter pending events (approx 2 hours away and not sent)
            pending_events = []
            for event in events:
                event_id = f"{event['timestamp']}_{event['title']}"
                if event_id in sent_events:
                    continue

                time_until = event["timestamp"] - now_ts
                if 0 < time_until <= 7200:
                    pending_events.append(event)

            if not pending_events:
                await asyncio.sleep(300)
                continue

            # 2. Group pending events by (timestamp, country)
            groups = {}
            for ev in pending_events:
                key = (ev["timestamp"], ev["country"])
                if key not in groups:
                    groups[key] = []
                groups[key].append(ev)

            # 3. Process each group as a single broadcast
            for (ts, country), grouped_evs in groups.items():
                ist_time = utc_to_ist(grouped_evs[0]["time"])

                # Format titles list
                if len(grouped_evs) > 1:
                    titles_block = "\n".join(
                        [f"• {html_safe(e['title'])}" for e in grouped_evs]
                    )
                    event_header = "<b>Events:</b>"
                else:
                    titles_block = html_safe(grouped_evs[0]["title"])
                    event_header = "<b>Event:</b>"

                alert = (
                    f"🏛️ <b>MACRO VOLATILITY WARNING</b>\n\n"
                    f"{event_header} {titles_block}\n"
                    f"<b>Country:</b> {html_safe(country)}\n"
                    f"<b>Time:</b> {ist_time}\n\n"
                    f"<b>Why it matters:</b> These are High Impact events. Expect significant volatility "
                    f"in BTC and ETH pairs around this time.\n"
                    f"<b>Watch next:</b> Ensure your trade positions are secured before the data drops."
                )

                await broadcast_message(session, alert)
                await asyncio.to_thread(
                    database.log_alert,
                    "macro_warning",
                    "GLOBAL",
                    alert,
                    iso_now(),
                )

                # Mark all as sent
                for ev in grouped_evs:
                    eid = f"{ev['timestamp']}_{ev['title']}"
                    sent_events.append(eid)

                await asyncio.to_thread(save_macro_state, sent_events)
                print(
                    f"Grouped Macro warning sent: {len(grouped_evs)} events for {country}"
                )

        except Exception as exc:
            print(f"Macro Calendar Error: {exc}")
            print(traceback.format_exc())

        await asyncio.sleep(
            300
        )  # Check every 5 minutes for precise 2-hour alerts


async def get_symbol_explanation(session, symbol):
    """Provides a detailed AI-driven explanation of the current confluence for a symbol."""
    state = active_signal_states.get(symbol.upper())
    if not state:
        return f"❌ No active data for {symbol}. Wait for the next scan."

    # We don't have the full signal_result here, so we synthesize from current state
    prompt = (
        f"Explain the trading context for {symbol} at ${state['price']:,.2f}.\n"
        f"State: {state['state']}, Zone: {state['zone']}, Support: ${state['support']:,.2f}, Resistance: ${state['resistance']:,.2f}.\n"
        "Provide a professional 3-sentence risk assessment."
    )
    explanation = await call_gemini_rest(
        session, prompt
    ) or await call_groq_rest(session, prompt)
    return (
        explanation
        if explanation
        else "Intelligence unavailable. Monitor key levels manually."
    )


def get_symbol_levels(symbol):
    """Returns the current WATCH levels and proximity for a symbol."""
    state = active_signal_states.get(symbol.upper())
    if not state:
        return f"❌ No levels tracked for {symbol} yet."

    price = state["price"]
    res = state["resistance"]
    sup = state["support"]

    prox_res = (1 - (price / res)) * 100 if res > 0 else 0
    prox_sup = ((price / sup) - 1) * 100 if sup > 0 else 0

    return (
        f"📊 <b>Levels: {symbol.upper()}</b>\n\n"
        f"Resistance: ${res:,.2f} ({prox_res:.1f}% away)\n"
        f"Support: ${sup:,.2f} ({prox_sup:.1f}% away)\n\n"
        f"State: {state['state']}"
    )


def get_symbol_bias(symbol):
    """Returns the macro and micro bias for a symbol."""
    # This would ideally come from the latest scan. For now, we use active_signal_states.
    state = active_signal_states.get(symbol.upper())
    if not state:
        return f"❌ No bias data for {symbol}."

    return (
        f"🧭 <b>Bias: {symbol.upper()}</b>\n\n"
        f"Structure: {state['state']}\n"
        f"Zone: {state.get('zone', 'NEUTRAL')}\n\n"
        f"Current Price: ${state['price']:,.2f}"
    )


async def telegram_long_polling(session):
    offset = None
    print("🤖 Telegram Polling Engine & Auto-Database is Live...")

    while True:
        url = f"{TELEGRAM_API_URL}/getUpdates"
        params = {
            "timeout": 30,
            "allowed_updates": ["message", "my_chat_member", "channel_post"],
        }
        if offset:
            params["offset"] = offset

        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=45)
            ) as response:
                if response.status != 200:
                    print(f"Telegram polling failed ({response.status})")
                    await asyncio.sleep(1)
                    continue

                updates = await response.json()
                for result in updates.get("result", []):
                    offset = result["update_id"] + 1

                    if "my_chat_member" in result:
                        member_event = result["my_chat_member"]
                        new_status = member_event["new_chat_member"]["status"]
                        chat_info = member_event["chat"]
                        chat_id = chat_info["id"]
                        chat_name = chat_info.get(
                            "title", f"Private User {chat_id}"
                        )
                        if new_status in ["administrator", "member"]:
                            await asyncio.to_thread(
                                database.add_channel, str(chat_id), chat_name
                            )
                            print(
                                f"Registered new Channel: {chat_name} to SQLite."
                            )

                    message = result.get("message")
                    if not message or "text" not in message:
                        continue

                    chat = message["chat"]
                    chat_id = str(chat["id"])
                    chat_type = chat.get("type", "")
                    sender_id = str(message.get("from", {}).get("id", chat_id))
                    text = message["text"].strip()
                    command = text.split(" ", 1)[0].split("@", 1)[0]
                    args = text.split(" ", 1)[1] if " " in text else ""
                    is_admin = sender_id == ADMIN_USER_ID

                    if command == "/start":
                        if chat_type == "private":
                            first_name = message.get("from", {}).get(
                                "first_name", ""
                            )
                            last_name = message.get("from", {}).get(
                                "last_name", ""
                            )
                            full_name = (
                                f"{first_name} {last_name}".strip()
                                or first_name
                                or "Telegram User"
                            )
                            username = message.get("from", {}).get(
                                "username", ""
                            )
                            await asyncio.to_thread(
                                database.add_user, chat_id, username, full_name
                            )
                            welcome = (
                                "<b>🤖 Welcome to the Crypto Analysis Bot</b>\n"
                                "You are now registered for DM updates.\n\n"
                                f"{build_help_message(is_admin)}"
                            )
                            await send_telegram_message(
                                session, chat_id, welcome
                            )
                        else:
                            await send_telegram_message(
                                session,
                                chat_id,
                                "Bot is online. Use /help for commands.",
                            )
                        continue

                    if command == "/help":
                        await send_telegram_message(
                            session, chat_id, build_help_message(is_admin)
                        )
                        continue

                    if command == "/feedback":
                        if not args:
                            await send_telegram_message(
                                session,
                                chat_id,
                                "Usage: /feedback &lt;your suggestion&gt;",
                            )
                            continue

                        user_info = message.get("from", {})
                        username = user_info.get("username", "N/A")
                        first_name = user_info.get("first_name", "User")

                        relay_msg = (
                            f"<b>📬 NEW USER FEEDBACK</b>\n\n"
                            f"<b>From:</b> {html_safe(first_name)} (@{html_safe(username)})\n"
                            f"<b>ID:</b> <code>{chat_id}</code>\n\n"
                            f"<b>Suggestion:</b>\n{html_safe(args)}"
                        )

                        # Forward to Admin
                        await send_telegram_message(
                            session, ADMIN_USER_ID, relay_msg
                        )
                        # Confirm to User
                        await send_telegram_message(
                            session,
                            chat_id,
                            "✅ Thank you! Your feedback has been sent to the developers.",
                        )
                        continue

                    if command == "/mycoins":
                        if not args:
                            coins = await asyncio.to_thread(
                                database.get_watchlist, chat_id
                            )
                            if coins:
                                await send_telegram_message(
                                    session,
                                    chat_id,
                                    f"✅ Current Watchlist: {', '.join(coins)}",
                                )
                            else:
                                await send_telegram_message(
                                    session,
                                    chat_id,
                                    "Your watchlist is empty. Set it using <code>/mycoins BTC ETH SOL</code>",
                                )
                            continue

                        if args.lower() == "clear":
                            await asyncio.to_thread(
                                database.clear_watchlist, chat_id
                            )
                            await send_telegram_message(
                                session, chat_id, "✅ Watchlist cleared."
                            )
                            continue

                        # Save new watchlist (Fix 6: Deduplicate inputs)
                        new_coins = list(
                            dict.fromkeys(
                                [
                                    c.strip().upper()
                                    for c in args.split()
                                    if c.strip()
                                ]
                            )
                        )
                        if not new_coins:
                            await send_telegram_message(
                                session,
                                chat_id,
                                "Usage: <code>/mycoins BTC ETH SOL</code>",
                            )
                            continue

                        await asyncio.to_thread(
                            database.save_watchlist, chat_id, new_coins
                        )

                        tier = await asyncio.to_thread(
                            database.get_user_tier, chat_id
                        )
                        msg = f"✅ Watchlist saved — {', '.join(new_coins)}."
                        if tier == "free":
                            msg += (
                                "\n\nUpgrade to Pro and every digest, alert, and analysis "
                                "will be built around only these coins. /upgrade"
                            )
                        await send_telegram_message(session, chat_id, msg)
                        continue

                    if command in ["/explain", "/levels", "/bias"]:
                        if not args:
                            await send_telegram_message(
                                session,
                                chat_id,
                                f"Usage: {command} &lt;COIN&gt; (e.g., BTC)",
                            )
                            continue

                        user_tier = await asyncio.to_thread(
                            database.get_user_tier, sender_id
                        )
                        if user_tier != "pro" and not is_admin:
                            await send_telegram_message(
                                session,
                                chat_id,
                                "💎 This is a <b>PRO</b> feature. Upgrade with /upgrade to access on-demand intelligence.",
                            )
                            continue

                        symbol = args.upper()
                        if command == "/explain":
                            res = await get_symbol_explanation(session, symbol)
                        elif command == "/levels":
                            res = get_symbol_levels(symbol)
                        elif command == "/bias":
                            res = get_symbol_bias(symbol)

                        await send_telegram_message(session, chat_id, res)
                        continue

                    if command == "/upgrade":
                        user_tier = await asyncio.to_thread(
                            database.get_user_tier, chat_id
                        )
                        if user_tier == "pro":
                            await send_telegram_message(
                                session,
                                chat_id,
                                "🌟 You are already a <b>Pro</b> subscriber! Enjoy your premium market intelligence.",
                            )
                        else:
                            msg = (
                                "🚀 <b>Upgrade to Pro</b>\n\n"
                                "Unlock the full potential of Crypto Intel Bot:\n"
                                "• <b>Personalized Intelligence:</b> Every digest & alert follows YOUR watchlist.\n"
                                "• <b>Gemini Synthesized News:</b> Pro-level interpretation of every major headline.\n"
                                "• <b>4H Dual Digest:</b> Exclusive market analysis twice a day.\n"
                                "• <b>Priority Alerts:</b> Instant notifications for critical shifts.\n\n"
                                "<b>Price:</b> ₹249/month\n\n"
                                "<i>Use the payment link below to upgrade instantly. Your account will "
                                "automatically switch to Pro upon successful payment.</i>\n\n"
                                f"⚠️ <b>IMPORTANT:</b> Include your Telegram ID (<code>{chat_id}</code>) in "
                                "the payment notes so we can automatically upgrade your account!\n\n"
                                "👉 <a href='https://rzp.io/l/your_payment_link'>Click Here to Subscribe</a>"
                            )
                            await send_telegram_message(session, chat_id, msg)
                        continue

                    if is_admin and command.startswith("/"):
                        await handle_admin_command(
                            session, command, args, chat_id
                        )

        except Exception as exc:
            print(f"Error in telegram_long_polling: {exc}")
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Sunday Weekly Report (Pro Only)
# ---------------------------------------------------------------------------


async def build_weekly_report(session) -> str:
    """
    Builds a premium weekly intelligence report.
    Includes: total signals, average score, top signals, most active coin,
    current lifecycle status, and an AI commentary.
    """
    summary = await asyncio.to_thread(database.get_weekly_signal_summary, 7)
    if not summary or summary.get("total", 0) == 0:
        return (
            "📊 <b>WEEKLY INTELLIGENCE REPORT</b>\n"
            f"📅 Week ending: {datetime.now(timezone.utc).strftime('%B %d, %Y')}\n\n"
            "No signals were generated this week.\n"
            "The market remained in neutral territory across all tracked assets.\n\n"
            "<i>Market analysis only. Not financial advice.</i>"
        )

    total = summary["total"]
    avg_score = summary["avg_score"]
    most_active = summary["most_active"]
    best_signals = summary.get("best_signals", [])

    # Build best signals section
    best_lines = []
    for sig in best_signals[:5]:
        sym, direction, score, _ = sig[0], sig[1], sig[2], sig[3]
        emoji = "🟢" if direction == "LONG" else "🔴"
        best_lines.append(f"  {emoji} #{sym} {direction} — Score: {score}%")

    # Current lifecycle status snapshot
    lifecycle_lines = []
    config = await load_config()
    for sym in config["tracked_coins"]:
        ls = active_signal_states.get(sym, {})
        state = ls.get("state", "NEUTRAL")
        icons = {
            "NEUTRAL": "⏸️",
            "WATCH": "👁️",
            "EARLY": "⚡",
            "CONFIRMED": "✅",
            "INVALIDATED": "❌",
        }
        lifecycle_lines.append(f"  {icons.get(state, '⏸️')} #{sym}: {state}")

    # AI Summary (optional, with fallback)
    ai_summary = "Market conditions were mixed this week. Stay disciplined and follow your levels."
    try:
        prompt = (
            f"You are a crypto market analyst. Summarize this week's activity in 2-3 sentences:\n"
            f"- Total signals generated: {total}\n"
            f"- Average signal confidence: {avg_score}%\n"
            f"- Most active asset: {most_active}\n"
            f"Be concise, professional, and avoid hype."
        )
        ai_raw = await call_gemini_rest(session, prompt)
        if ai_raw and len(ai_raw.strip()) > 20:
            ai_summary = html_safe(ai_raw.strip())
    except Exception:
        pass

    report = (
        f"📊 <b>WEEKLY INTELLIGENCE REPORT</b>\n"
        f"📅 Week ending: {datetime.now(timezone.utc).strftime('%B %d, %Y')}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📈 <b>Week Summary</b>\n"
        f"  Total Signals: {total}\n"
        f"  Avg Confidence: {avg_score}%\n"
        f"  Most Active: #{most_active}\n\n"
        f"🏆 <b>Top Signals This Week</b>\n"
        f"{chr(10).join(best_lines) if best_lines else '  No qualifying signals'}\n\n"
        f"📍 <b>Current Lifecycle Status</b>\n"
        f"{chr(10).join(lifecycle_lines)}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 <b>AI Analysis</b>\n"
        f"{ai_summary}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )

    return report


async def weekly_report_loop(session):
    """
    Sends weekly report to Pro users every Sunday at 03:30 UTC (9:00 AM IST).
    """
    print("📊 Weekly Report Scheduler is Live...")
    while True:
        try:
            now = datetime.now(timezone.utc)
            week_str = now.strftime("%Y-%W")

            # Calculate seconds until next Sunday 03:30 UTC
            days_until_sunday = (6 - now.weekday()) % 7
            if days_until_sunday == 0 and (
                now.hour > 3 or (now.hour == 3 and now.minute >= 30)
            ):
                days_until_sunday = 7

            next_report = now.replace(
                hour=3, minute=30, second=0, microsecond=0
            ) + timedelta(days=days_until_sunday)
            wait_seconds = (next_report - now).total_seconds()

            print(f"⏳ Next weekly report in {wait_seconds / 3600:.1f} hours")
            await asyncio.sleep(wait_seconds)

            # Check if already sent for this week
            if await asyncio.to_thread(database.check_weekly_sent, week_str):
                continue

            # Build and send report
            report = await build_weekly_report(session)

            # Send to Pro users only
            users_with_tiers = await asyncio.to_thread(
                database.get_all_users_with_tiers
            )
            sent = 0
            for row in users_with_tiers:
                uid, tier = row[0], row[2]
                if tier == "pro":
                    if await send_telegram_message(session, uid, report):
                        sent += 1
                    await asyncio.sleep(0.1)  # Prevent flood

            await asyncio.to_thread(database.mark_weekly_sent, week_str)
            await asyncio.to_thread(
                database.log_alert, "weekly_report", "PRO", report, iso_now()
            )
            print(
                f"✅ Weekly Intelligence Report dispatched to {sent} Pro users for week {week_str}"
            )

        except Exception as exc:
            print(f"Weekly Report Error: {exc}")
            await asyncio.sleep(3600)


async def database_maintenance_loop():
    """Background loop to keep SQLite healthy."""
    print("🔧 Database Maintenance Engine is Live...")
    while True:
        try:
            # Run every 6 hours
            await asyncio.sleep(6 * 3600)
            await asyncio.to_thread(database.run_maintenance)
            await asyncio.to_thread(database.cleanup_old_data)
        except Exception as exc:
            print(f"Database Maintenance Error: {exc}")
            await asyncio.sleep(3600)


async def main():
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    await load_config()
    connector = aiohttp.TCPConnector(keepalive_timeout=60)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Sync Telegram menu commands
        await register_admin_commands(session)

        async def safe_loop(func, *args):
            while True:
                try:
                    await func(*args)
                    break
                except Exception as e:
                    print(
                        f"🚨 Background loop {func.__name__} crashed: {e}. "
                        "Restarting in 10s..."
                    )
                    await asyncio.sleep(10)

        await asyncio.gather(
            safe_loop(telegram_long_polling, session),
            safe_loop(binance_liquidation_listener, session),
            safe_loop(rsi_scanner_loop, session),
            safe_loop(daily_digest_loop, session),
            safe_loop(news_checker_loop, session),
            safe_loop(macro_calendar_loop, session),
            safe_loop(weekly_report_loop, session),
            safe_loop(database_maintenance_loop),
            safe_loop(run_webhook_server, session, TELEGRAM_API_URL),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
