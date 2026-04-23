import asyncio
import html
import json
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import websockets
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

import database
from utils.gemini_rotator import GeminiRotator, GroqRotator
from utils.scoring_engine import calculate_score
from webhook_server import run_webhook_server

load_dotenv()

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "1120431469")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set. Bot shutting down.")

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.path.join(os.getcwd(), "data"))
if not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
    print(f"⚠️ Warning: RAILWAY_VOLUME_MOUNT_PATH not set. Using local directory: {DATA_DIR}")

os.makedirs(DATA_DIR, exist_ok=True)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Gemini/Groq AI status
gemini_rotator = GeminiRotator()
groq_rotator = GroqRotator()

if gemini_rotator.has_keys():
    print(f"🤖 Gemini AI Intelligence Layer initialized ({len(gemini_rotator.get_all_keys())} keys).")
if groq_rotator.has_keys():
    print(f"🧠 Groq AI Failover Layer initialized ({len(groq_rotator.get_all_keys())} keys).")
if not gemini_rotator.has_keys() and not groq_rotator.has_keys():
    print("⚠️ AI API Keys missing. Bot will use manual market summary logic.")

RSI_STATE_FILE = os.getenv("RSI_STATE_FILE", os.path.join(DATA_DIR, "rsi_state.json"))
LAST_DIGEST_FILE = os.getenv("LAST_DIGEST_FILE", os.path.join(DATA_DIR, "last_digest.txt"))
CONFIG_FILE = os.getenv("CONFIG_FILE", os.path.join(DATA_DIR, "config.json"))
STARTED_AT = datetime.now(timezone.utc)
CONFIG_LOCK = asyncio.Lock()

database.init_db()

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
        "cooldown_hours": 6,
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


def iso_now():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


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
        # Add 5h 30m
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


def deep_merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


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
        config = await asyncio.to_thread(read_json_file, CONFIG_FILE, DEFAULT_CONFIG)
        await asyncio.to_thread(write_json_file, CONFIG_FILE, config)
        return config


async def update_config(mutator):
    async with CONFIG_LOCK:
        config = await asyncio.to_thread(read_json_file, CONFIG_FILE, DEFAULT_CONFIG)
        updated_config = mutator(config)
        await asyncio.to_thread(write_json_file, CONFIG_FILE, updated_config)
        return updated_config


def load_rsi_state():
    raw_state = read_json_file(RSI_STATE_FILE, {})
    normalized = {}
    for symbol, interval_states in raw_state.items():
        normalized[symbol] = {}
        if not isinstance(interval_states, dict):
            continue
        for interval, value in interval_states.items():
            if isinstance(value, str):
                normalized[symbol][interval] = {"state": value, "last_alert_at": None}
            elif isinstance(value, dict):
                normalized[symbol][interval] = {
                    "state": value.get("state", "NEUTRAL"),
                    "last_alert_at": value.get("last_alert_at"),
                }
    return normalized


def save_rsi_state(state):
    try:
        write_json_file(RSI_STATE_FILE, state)
    except Exception as exc:
        print(f"Error saving RSI state to {RSI_STATE_FILE}: {exc}")


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
                    print(f"⚠️ API Rate Limited (429) for {url}. Waiting {wait}s...")
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
                    print(f"Telegram send failed (403) for {chat_id} - Revoking access.")
                    await asyncio.to_thread(database.revoke_access, str(chat_id))
                    return False
                if response.status == 429:
                    wait = (
                        (await response.json()).get("parameters", {}).get("retry_after", 5)
                        if response.content_type == "application/json"
                        else 5
                    )
                    print(f"⚠️ Telegram Rate Limited (429). Waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                print(f"Telegram send failed ({response.status}) for {chat_id}")
        except Exception as exc:
            print(f"Telegram send error on attempt {attempt + 1}: {exc}")
        await asyncio.sleep(1)
    print(f"Failed to send message to {chat_id}")
    return False


async def broadcast_message(session, text, include_channels=True, include_users=True):
    sent = 0
    sent_ids = set()

    # 1. Channels (Always receive everything)
    if include_channels:
        channels = await asyncio.to_thread(database.get_active_channels)
        for chat_id in set(channels):
            if chat_id in sent_ids:
                continue
            if await send_telegram_message(session, chat_id, text):
                sent += 1
                sent_ids.add(chat_id)

    # 2. Users (Pro Tier get filtered content)
    if include_users:
        users = await asyncio.to_thread(database.get_active_users)
        for user_id in set(users):
            if user_id in sent_ids:
                continue

            tier = await asyncio.to_thread(database.get_user_tier, user_id)
            if tier == "pro":
                watchlist = await asyncio.to_thread(database.get_watchlist, user_id)
                if watchlist:
                    mention_found = any(coin.upper() in text.upper() for coin in watchlist)
                    if not mention_found:
                        continue

            if await send_telegram_message(session, user_id, text):
                sent += 1
                sent_ids.add(user_id)

    return sent


async def dispatch_signal_alert(session, signal_result, current_price):
    """
    Dispatches a scored and threshold-passed signal alert to all active users.
    Optimized for batching and atomic cooldowns.
    """
    symbol = signal_result["symbol"]
    score = signal_result["score"]
    strength = signal_result["strength"]
    direction = signal_result["direction"]
    meter = signal_result["meter"]
    rationale = signal_result["rationale"]

    direction_emoji = "🟢" if direction == "LONG" else "🔴"
    direction_label = "LONG — Oversold Setup" if direction == "LONG" else "SHORT — Overbought Setup"

    # ---- MESSAGES ----
    free_msg = (
        f"🚨 <b>{symbol} Signal Detected</b>\n\n"
        f"Direction: {direction_emoji} {direction_label}\n"
        f"Price: ${current_price:,.2f}\n\n"
        f"📊 Signal Confidence: {meter} {score}%\n"
        f"Strength: <b>{strength}</b>\n\n"
        f"<i>Upgrade to Pro for full signal breakdown,\n"
        f"rationale, and actionable focus note.</i>\n"
        f"/upgrade\n\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )

    rationale_lines = "\n".join([f"  • {html_safe(r)}" for r in rationale])
    focus = "High conviction setup." if score >= 80 else "Monitor for confirmation."
    if direction == "LONG":
        focus = (
            "High conviction oversold setup. Watch for volume confirmation on the bounce."
            if score >= 80
            else "Oversold condition. Wait for stabilization."
        )
    else:
        focus = (
            "High conviction overbought setup. Watch for momentum exhaustion."
            if score >= 80
            else "Overbought condition. Look for rejection."
        )
    focus = html_safe(focus)

    pro_msg = (
        f"🚨 <b>{symbol} Intelligence Signal</b>\n\n"
        f"Direction: {direction_emoji} {direction_label}\n"
        f"Price: ${current_price:,.2f}\n\n"
        f"📊 Signal Confidence: {meter} {score}%\n"
        f"Strength: <b>{strength}</b>\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<b>Confluence Breakdown:</b>\n"
        f"{rationale_lines}\n\n"
        f"<b>Focus:</b>\n"
        f"{focus}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )

    # ---- DISPATCH ----
    # 1. Fetch all users with tiers in one go (batching)
    users_with_tiers = await asyncio.to_thread(database.get_all_users_with_tiers)
    active_channels = await asyncio.to_thread(database.get_active_channels)

    semaphore = asyncio.Semaphore(20)

    async def send_to_user(user_id, tier):
        async with semaphore:
            cooldown_mins = 5 if tier == "pro" else 15
            # Atomic cooldown claim
            if not await asyncio.to_thread(database.claim_cooldown_for_user, user_id, symbol, "signal", cooldown_mins):
                return

            msg = pro_msg if tier == "pro" else free_msg
            success = await send_telegram_message(session, user_id, msg)
            if not success:
                # If send failed after all retries, release the cooldown so they don't miss the next one
                await asyncio.to_thread(database.reset_cooldown_for_user, user_id, symbol, "signal")

    async def send_to_channel(channel_id):
        async with semaphore:
            await send_telegram_message(session, channel_id, free_msg)

    # 2. Parallelize broadcast
    user_tasks = [send_to_user(u[0], u[2]) for u in users_with_tiers]
    chan_tasks = [send_to_channel(c) for c in active_channels]

    await asyncio.gather(*(user_tasks + chan_tasks), return_exceptions=True)


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


def classify_news_importance(title):
    """Returns a dictionary with level, emoji, and a brief impact explanation."""
    title_lower = title.lower()
    critical_kws = (
        "hack",
        "exploit",
        "stolen",
        "ban",
        "banned",
        "arrest",
        "sec sues",
        "sec charges",
        "etf approved",
        "etf rejected",
        "collapse",
        "insolvent",
        "emergency",
        "halted",
        "lawsuit",
        "bankruptcy",
    )
    moderate_kws = (
        "binance",
        "blackrock",
        "coinbase",
        "etf",
        "regulation",
        "fed rate",
        "interest rate",
        "inflation",
        "cpi",
        "powell",
        "grayscale",
        "whale",
        "ath",
        "crash",
        "dump",
        "pump",
        "bullish",
        "bearish",
        "market share",
    )

    if any(kw in title_lower for kw in critical_kws):
        return {
            "level": "CRITICAL",
            "emoji": "🔴",
            "impact": "Major regulatory development, security breach, or systemic shift.",
        }
    if any(kw in title_lower for kw in moderate_kws):
        return {
            "level": "MODERATE",
            "emoji": "🟠",
            "impact": "Significant market shift, institutional activity, or macro update.",
        }
    return {"level": "LOW", "emoji": "🟡", "impact": "General ecosystem update or standard market movement."}


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
    """
    Binance mapping:
    - BUY force order = Short position liquidated.
    - SELL force order = Long position liquidated.
    """
    shorts = await asyncio.to_thread(database.get_coin_liquidations_30min, symbol, "BUY")
    longs = await asyncio.to_thread(database.get_coin_liquidations_30min, symbol, "SELL")
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
        insight = "Weak short squeeze against a bearish trend. Watch for fade risk."
    elif trend == "BEARISH 🔴" and side == "SELL":
        final_score = base_score * 1.5
        insight = "Longs capitulating. Bearish momentum is accelerating."
    elif trend == "BULLISH 🟢" and side == "SELL":
        final_score = base_score * 0.5
        insight = "Long flush inside a bullish structure. Trend remains intact."
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
    for index in range(1, len(prices)):
        change = prices[index] - prices[index - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for index in range(period, len(prices) - 1):
        avg_gain = ((avg_gain * (period - 1)) + gains[index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# Global Market Cache to reduce API overhead during broadcasts
# Global Caches & Locks
_AI_SUMMARY_CACHE = {}  # {(snapshots_key, pulse_score): (summary, timestamp)}
_MARKET_SNAPSHOT_CACHE = {}  # {symbol: (snapshot, timestamp)}

_AI_CACHE_LOCK = asyncio.Lock()
_MARKET_CACHE_LOCK = asyncio.Lock()


async def fetch_symbol_snapshot(session, symbol, config):
    """Fetches full market metrics for a coin with a short-lived cache."""
    now = datetime.now()
    async with _MARKET_CACHE_LOCK:
        if symbol in _MARKET_SNAPSHOT_CACHE:
            cached_val, timestamp = _MARKET_SNAPSHOT_CACHE[symbol]
            # 30-second cache is enough to cover a broadcast burst
            if (now - timestamp).total_seconds() < 30:
                return cached_val

        pair = symbol_to_pair(symbol)
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": pair, "interval": "1d", "limit": 50}
        candles = await fetch_with_backoff(session, url, params)
        if not candles:
            return None

        close_prices = [float(candle[4]) for candle in candles]
        volumes = [float(candle[5]) for candle in candles]
        current_price = close_prices[-1]
        previous_price = close_prices[-2] if len(close_prices) > 1 else current_price
        sma_50 = sum(close_prices) / len(close_prices)
        rsi = calculate_rsi(close_prices)

        # Yesterday's Completed Volume vs 20-day Moving Average (last 20 days)
        current_volume = volumes[-2]
        ma_period = 20
        recent_volumes = volumes[-ma_period - 1:-1]
        average_volume = sum(recent_volumes) / max(len(recent_volumes), 1)

        volume_ratio = current_volume / average_volume if average_volume else 1.0
        volume_percent = volume_ratio * 100
        volume_delta = (volume_ratio - 1) * 100

        change_24h = ((current_price - previous_price) / previous_price * 100) if previous_price else 0.0
        trend = "BULLISH 🟢" if current_price > sma_50 else "BEARISH 🔴"

        vol_cfg = config.get("volume_settings", DEFAULT_CONFIG["volume_settings"])
        if volume_percent >= vol_cfg["high"]:
            volume_status = "HIGH 🟢"
        elif volume_percent >= vol_cfg["normal"]:
            volume_status = "NORMAL ⚪"
        elif volume_percent >= vol_cfg["low"]:
            volume_status = "LOW 🔴"
        else:
            volume_status = "VERY LOW 🔴"

        res = {
            "symbol": pair,
            "tag": pair_to_tag(pair),
            "price": current_price,
            "change_24h": change_24h,
            "trend": trend,
            "rsi": rsi,
            "volume_percent": volume_percent,
            "volume_delta": volume_delta,
            "volume_status": volume_status,
        }
        # FIFO Cache Purge
        if len(_MARKET_SNAPSHOT_CACHE) >= 100:
            oldest_key = next(iter(_MARKET_SNAPSHOT_CACHE))
            _MARKET_SNAPSHOT_CACHE.pop(oldest_key)

        _MARKET_SNAPSHOT_CACHE[symbol] = (res, datetime.now())
        return res


def calculate_market_pulse(snapshots, config):
    score = 50
    bullish_count = 0
    bearish_count = 0

    for snapshot in snapshots:
        if snapshot is None:
            continue
        if "BULLISH" in snapshot["trend"]:
            score += 8
            bullish_count += 1
        else:
            # Bearish trend is heavily weighted during crashes
            score -= 12
            bearish_count += 1

        if snapshot["rsi"] >= 65:
            score += 5
        elif snapshot["rsi"] <= 35:
            # Low RSI (Fear) gets higher penalty
            score -= 10

        if snapshot["change_24h"] >= 2:
            score += 4
        elif snapshot["change_24h"] <= -2:
            score -= 6

    powell_score = int(config["powell"]["score"])
    score += (3 - powell_score) * 4

    etf_bias = config["etf"]["bias"]
    if etf_bias == "bullish":
        score += 10
    elif etf_bias == "bearish":
        score -= 10

    score = max(0, min(100, score))
    if score <= 25:
        label = "EXTREME FEAR"
    elif score <= 40:
        label = "FEAR"
    elif score <= 60:
        label = "NEUTRAL"
    elif score <= 75:
        label = "GREED"
    else:
        label = "EXTREME GREED"

    # Dynamic "Watch Next" Logic
    if bullish_count > bearish_count:
        if score > 75:
            watch_next = "Watch for potential overextension. Bulls are in control, but profit-taking risk is high."
        else:
            watch_next = "Watch for trend continuation. Strong breadth suggests a stable upward trajectory."
    elif bearish_count > bullish_count:
        if score < 25:
            watch_next = "Watch for a capitulation bounce. Extreme fear often leads to local reversals."
        else:
            watch_next = "Watch for lower lows. Bearish trend structure remains dominant."
    else:
        watch_next = "Watch for a decisive breakout. Market is compressed and searching for direction."

    return {"score": score, "label": label, "watch_next": watch_next}


def summarize_market_logic(snapshots, pulse):
    """Manual fallback market summary."""
    bulls = [s for s in snapshots if s and "BULLISH" in s["trend"]]
    bears = [s for s in snapshots if s and "BEARISH" in s["trend"]]
    oversold = [s for s in snapshots if s and s["rsi"] < 35]

    if len(bulls) >= 2:
        return "Market is showing resilient bullish structure across majors, though volume remains thin."
    if len(bears) >= 2:
        if oversold:
            return f"Heavily bearish momentum, however {oversold[0]['tag']} is approaching oversold territory."
        return "Broad bearish momentum persists. Institutional flows remain cautious."
    return "Market is currently in an accumulation phase with mixed signals across the board."


GEMINI_MODELS = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-pro"]


async def call_gemini_rest(session, prompt):
    """Calls Gemini REST API via aiohttp with key rotation and model failover."""
    if not gemini_rotator.has_keys():
        return None

    # Try up to N times where N is number of keys
    for _ in range(len(gemini_rotator.get_all_keys())):
        reserved_index, key = await gemini_rotator.reserve_key()
        if not key:
            return None

        for model_name in GEMINI_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get("candidates", [])
                        if candidates and "content" in candidates[0]:
                            text = candidates[0]["content"].get("parts", [{}])[0].get("text", "")
                            if text:
                                return text.strip()
                        else:
                            print(f"⚠️ Gemini Safety Filter Block or empty response: {data}")
                            continue
                    elif resp.status == 404:
                        # Model not supported by this key or region, try next model
                        continue
                    elif resp.status == 429:
                        print("ℹ️ Gemini Key 429 (Rate Limit). Rotating...")
                        await gemini_rotator.advance_after_rate_limit(reserved_index)
                        break  # Try the next key
                    else:
                        error_body = await resp.text()
                        print(f"❌ Gemini API error ({resp.status}): {error_body[:100]}")
                        # Other errors (500, etc) might be temporary, try next model or next key
                        continue
            except Exception as e:
                print(f"❌ Gemini call error: {e}")
                continue

    return None


async def call_groq_rest(session, prompt):
    """Calls Groq REST API (High-speed Backup) via aiohttp with rotation."""
    if not groq_rotator.has_keys():
        return None

    # Try all keys
    for _ in range(len(groq_rotator.get_all_keys())):
        reserved_index, key = await groq_rotator.reserve_key()
        if not key:
            continue

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 512,
        }
        try:
            async with session.post(
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if text:
                        print("✅ Groq AI success (Llama 3.3).")
                        return text.strip()
                elif resp.status == 429:
                    print("⚠️ Groq Key 429 (Rate Limit). Rotating...")
                    await groq_rotator.advance_after_rate_limit(reserved_index)
                    continue
                else:
                    body = await resp.text()
                    print(f"❌ Groq API error ({resp.status}): {body[:100]}")
        except Exception as e:
            print(f"❌ Groq REST call failed: {e}")
    return None


async def get_ai_market_summary(session, snapshots, pulse):
    """Generates a professional AI market summary with Gemini -> Groq failover and caching."""
    # Create a unique key based on the coins and their prices (rounded to prevent minor jitter)
    cache_key = "|".join([f"{s['tag']}:{round(s['price'], 2)}" for s in snapshots if s])

    async with _AI_CACHE_LOCK:
        if cache_key in _AI_SUMMARY_CACHE:
            # Check if cache is still fresh (within 10 minutes)
            cached_val, timestamp = _AI_SUMMARY_CACHE[cache_key]
            if (datetime.now() - timestamp).total_seconds() < 600:
                return cached_val

        if not gemini_rotator.has_keys() and not groq_rotator.has_keys():
            return summarize_market_logic(snapshots, pulse)

        coin_info = []
        for s in snapshots:
            if s:
                coin_info.append(
                    f"  #{s['tag']}: ${s['price']:,.2f} "
                    f"({s['change_24h']:+.2f}% 24h, Trend: {s['trend']}, RSI: {s['rsi']:.1f})"
                )

        context = "\n".join(coin_info)
        prompt = (
            f"Act as a professional Crypto Market Analyst. Summarize the following 24h market data into 2-3 precise, "
            f"insightful sentences for a high-level trading digest. Focus on trend confluence and momentum.\n\n"
            f"Market Pulse Score: {pulse['score']}/100 ({pulse['label']})\n"
            f"24h Snapshots:\n{context}\n\n"
            f"Output only the summary text."
        )

        # Attempt 1: Gemini (with rotation)
        result = await call_gemini_rest(session, prompt)
        if result:
            # FIFO Cache Purge
            if len(_AI_SUMMARY_CACHE) >= 100:
                oldest_key = next(iter(_AI_SUMMARY_CACHE))
                _AI_SUMMARY_CACHE.pop(oldest_key)
            _AI_SUMMARY_CACHE[cache_key] = (result, datetime.now())
            return result

        # Attempt 2: Groq (Failover)
        print("🔄 Gemini failed/exhausted. Switching to Groq Backup Brain...")
        groq_result = await call_groq_rest(session, prompt)
        if groq_result:
            # FIFO Cache Purge
            if len(_AI_SUMMARY_CACHE) >= 100:
                oldest_key = next(iter(_AI_SUMMARY_CACHE))
                _AI_SUMMARY_CACHE.pop(oldest_key)
            _AI_SUMMARY_CACHE[cache_key] = (groq_result, datetime.now())
            return groq_result

        # Final Fallback: Manual
        print("🤖 All AI Layers failed. Falling back to manual logic.")
        final_result = summarize_market_logic(snapshots, pulse)

        # Cache manual fallback too
        if len(_AI_SUMMARY_CACHE) >= 100:
            oldest_key = next(iter(_AI_SUMMARY_CACHE))
            _AI_SUMMARY_CACHE.pop(oldest_key)
        _AI_SUMMARY_CACHE[cache_key] = (final_result, datetime.now())

        return final_result


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
    lines = [
        "📰 <b>DAILY MARKET DIGEST</b>",
        f"Time: {html_safe(ist_now())}",
        "",
        f"<b>Market Pulse:</b> {pulse['label']} ({pulse['score']}/100)",
    ]

    # 24h Stance Timeout (Powell update)
    powell = config.get("powell", {})
    last_updated_str = powell.get("updated_at")
    show_powell = False
    if last_updated_str:
        try:
            last_ts = datetime.fromisoformat(last_updated_str).timestamp()
            if (utc_now().timestamp() - last_ts) < 86400:  # 24 hours
                show_powell = True
        except Exception:
            pass

    if show_powell:
        lines.append(
            "<b>Why it matters:</b> "
            f"{html_safe(config['powell']['stance'].title())} Fed tone and current "
            "trend breadth are shaping risk appetite."
        )
    else:
        lines.append(
            "<b>Review:</b> Neutral Fed baseline. Market trend breadth and macro inflows are shaping risk appetite."
        )

    lines.append(f"<b>Watch next:</b> {html_safe(pulse['watch_next'])}")
    lines.append("")

    # 24h Liquidation Section
    if liq_stats:
        total = liq_stats["total"]
        if total > 0:
            long_p = (liq_stats["longs"] / total) * 100
            short_p = (liq_stats["shorts"] / total) * 100
            composition = f"{long_p:.1f}% Longs | {short_p:.1f}% Shorts"
        else:
            composition = "0% Longs | 0% Shorts"

        liq_cfg = config.get("liquidation_settings", DEFAULT_CONFIG["liquidation_settings"])
        status = "NORMAL ⚪"
        if total >= liq_cfg["extreme"]:
            status = "EXTREME 🔴"
        elif total >= liq_cfg["high"]:
            status = "HIGH 🟠"

        lines.append("<b>24h Liquidations</b>")
        lines.append(f"- Total: ${total:,.0f} [{status}]")
        lines.append(f"- Longs: ${liq_stats['longs']:,.0f} | Shorts: ${liq_stats['shorts']:,.0f}")
        lines.append(f"- Composition: {composition}")
        lines.append("")

    lines.append("<b>Major Coin Snapshot</b>")
    for snapshot in snapshots:
        if snapshot is None:
            continue

        emoji = "🔼" if snapshot["change_24h"] >= 0 else "🔽"
        lines.append(
            f"<b>  #{html_safe(snapshot['tag'])}</b>: ${snapshot['price']:,.2f} "
            f"({emoji} {snapshot['change_24h']:+.2f}% 24h)"
        )
        lines.append(f"- Trend: {snapshot['trend']} vs 50-day average")
        lines.append(f"- Daily RSI: {snapshot['rsi']:.1f}")
        lines.append(
            f"- Volume: {html_safe(snapshot['volume_status'])} "
            f"({snapshot['volume_delta']:+.1f}% vs 20-day avg)"
        )
        lines.append("")

    # News section
    lines.append("📰 <b>Top News (Last 24h)</b>")
    if top_news:
        for item in top_news:
            lines.append(f"{item['emoji']} <b>{item['level']}:</b> {html_safe(item['title'])}")
    else:
        lines.append("- No major news events recorded in last 24h.")
    lines.append("")

    # Market Summary section
    summary = await get_ai_market_summary(session, snapshots, pulse)
    lines.append("📊 <b>Market Summary</b>")
    lines.append(f"{html_safe(summary)}")
    lines.append("")

    lines.append("---")
    lines.append("(Data Sources: Binance, Trading Economics, CoinTelegraph, CoinGlass)")
    lines.append("")
    lines.append("⚠️ <b>Disclaimer:</b> Market analysis only, not financial advice.")
    lines.append("")

    if user_tier == "pro":
        lines.append("💎 <b>Intelligence Tier Active</b>")
        lines.append("You are receiving full signal confluence rationale and professional focus notes.")
    else:
        lines.append("💡 <b>Free Tier Active</b>")
        lines.append("Upgrade to <b>PRO</b> for full intelligence signals and confluence breakdowns.")
        lines.append("/upgrade")

    return "\n".join(lines).strip()


async def fetch_top_news(session):
    """
    Fetch headlines from the last 24 hours via CoinTelegraph RSS.
    Priority: Critical/High Importance events.
    Fallback: General headlines if no high-impact news exists.
    """
    try:
        url = "https://cointelegraph.com/rss"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with session.get(url, headers=headers, timeout=10) as response:
            if response.status != 200:
                return []
            xml_data = await response.text()

            root = ET.fromstring(xml_data)
            channel = root.find("channel")
            if channel is None:
                return []

            cutoff = utc_now().timestamp() - 86400  # 24 hours ago
            critical_high = []
            general = []

            for item in channel.findall("item")[:40]:
                pubDate = item.find("pubDate")
                if pubDate is None:
                    continue

                try:
                    dt = parsedate_to_datetime(pubDate.text)
                    published_on = dt.timestamp()
                except Exception:
                    continue

                if published_on < cutoff:
                    continue

                title = item.find("title")
                title_text = title.text if title is not None else ""

                importance_data = classify_news_importance(title_text)
                news_item = {
                    "title": title_text,
                    "source": "CoinTelegraph",
                    "level": importance_data["level"],
                    "emoji": importance_data["emoji"],
                    "impact": importance_data["impact"],
                }

                if importance_data["level"] in ("CRITICAL", "MODERATE"):
                    critical_high.append(news_item)
                else:
                    general.append(news_item)

                if len(critical_high) >= 3:
                    break

            # Sort top news by severity (CRITICAL first)
            # Use numeric ranking to avoid alphabetical sorting issues (M > C)
            SEVERITY_RANK = {"CRITICAL": 3, "MODERATE": 2, "LOW": 1}
            final_news = sorted(
                critical_high + general[:3], key=lambda x: SEVERITY_RANK.get(x["level"], 0), reverse=True
            )
            return final_news[:3]  # Strictly Top 3 news items
    except Exception as exc:
        print(f"fetch_top_news error: {exc}")
        return []


async def _fetch_investing_macro_events(session, timeframe_tab):
    """
    Fetches high-impact macro events from Investing.com via internal AJAX API.
    Bypasses the broken ForexFactory Next Week feed.
    """
    try:
        pass
    except ImportError:
        print("   - ❌ Error: BeautifulSoup4 not installed. Cannot fetch Investing.com data.")
        return False, []

    url = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    # US(5), EU(72), UK(4), Ger(17), Jpn(35), Aus(25), Can(6), Swz(12), NZ(43)
    payload = (
        "country[]=5&country[]=72&country[]=4&country[]=17&country[]=35&country[]=25&country[]=6&country[]=12"
        "&country[]=43&timeZone=8&timeFilter=timeOnly&currentTab={timeframe_tab}&submitFilters=1&limit_from=0"
        "&importance=3"
    ).format(timeframe_tab=timeframe_tab)

    events = []
    print(f"📡 Macro Fetch: Checking {timeframe_tab} via Investing.com API...")
    try:
        async with session.post(url, headers=headers, data=payload, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()
            print(f"   - Status: {resp.status} | Content-Type: {content_type}")

            if resp.status != 200:
                print(f"   - ❌ Error: Investing API returned non-200 status ({resp.status}).")
                return False, []

            raw_text = await resp.text()
            html = ""
            try:
                data = json.loads(raw_text)
                html = data.get("data", "")
            except Exception:
                # If it's not JSON, might be raw HTML (which we can still attempt to parse)
                html = raw_text

            if not html or len(html.strip()) < 50:
                print("   - ❌ Error: Investing API returned empty or too short response.")
                return False, []

            soup = BeautifulSoup(html, "html.parser")

            # 1. Look for the actual row markers to confirm it's a real calendar
            rows = soup.find_all("tr", class_="js-event-item")
            if not rows:
                # Check for "No Results" specifically
                if "No Events Scheduled" in html or "noResults" in html:
                    print("   - ✅ Success: Calendar loaded, but truly no events scheduled.")
                    return True, []
                print("   - ❌ Error: Calendar structure markers (js-event-item) not found. Blocked or format change?")
                return False, []

            print(f"   - Status: Found {len(rows)} raw calendar rows. Filtering for High Impact...")

            for tr in rows:
                try:
                    datetime_str = tr.get("data-event-datetime")  # e.g., "2026/04/10 15:30:00"
                    if not datetime_str:
                        continue

                    title_elem = tr.find("td", class_="event")
                    title = title_elem.text.strip() if title_elem else "Unknown"
                    title = title.replace("  ", " ").strip()

                    country_td = tr.find("td", class_="flagCur")
                    country = country_td.text.replace("\xa0", "").strip() if country_td else ""
                    if len(country) > 3:
                        country = country[-3:]  # Extract currency ISO

                    sentiment_td = tr.find("td", class_="sentiment")
                    if sentiment_td and sentiment_td.get("data-img_key") == "bull3":
                        dt = datetime.strptime(datetime_str, "%Y/%m/%d %H:%M:%S")
                        event_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())

                        events.append(
                            {
                                "title": title,
                                "country": country,
                                "date": dt.strftime("%m-%d"),
                                "time": dt.strftime("%H:%M UTC"),
                                "timestamp": event_ts,
                            }
                        )
                except Exception:
                    continue

        print(f"   - ✅ Success: Extracted {len(events)} High-Impact events from Investing.com.")
        return True, events
    except Exception as e:
        print(f"   - ❌ Investing Fetch Error: {e}")
        return False, []


async def _fetch_all_high_impact_events(session, horizon_days=1):
    """
    Consolidated helper to fetch High Impact events from both current and next week.
    Handles primary/fallback URLs, detailed logging, and success/failure signaling.
    """
    now_ts = utc_now().timestamp()
    limit_ts = now_ts + (86400 * horizon_days)
    all_events = []
    seen_titles = set()
    headers = {"User-Agent": "Mozilla/5.0"}

    # We only use ForexFactory for the current week, as they removed nextweek support.
    feed_targets = [
        {
            "label": "This Week",
            "primaries": ["https://nfs.faireconomy.media/ff_calendar_thisweek.xml"],
            "fallbacks": ["https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.xml"],
        }
    ]

    total_successful_feeds = 0
    # total_attempted_feeds = len(feed_targets)
    target_status = {}

    for target in feed_targets:
        feed_success = False
        target_event_count = 0
        for url in target["primaries"] + target.get("fallbacks", []):
            print(f"📡 Macro Fetch: Checking {target['label']} via {url}...")
            try:
                async with session.get(url, headers=headers, timeout=10) as response:
                    content_type = response.headers.get("Content-Type", "").lower()
                    print(f"   - Status: {response.status} | Content-Type: {content_type}")

                    if response.status != 200:
                        continue

                    if "xml" not in content_type:
                        print(f"   - ⚠️ Warning: Expected XML but got {content_type}. Skipping.")
                        continue

                    xml_data = await response.text()
                    if not xml_data.strip().startswith("<?xml") and not xml_data.strip().startswith("<weeklyevents"):
                        print("   - ⚠️ Warning: Invalid XML body. Skipping.")
                        continue

                    root = ET.fromstring(xml_data)
                    events_in_this_feed = 0

                    for item in root.findall("event"):
                        impact_node = item.find("impact")
                        if impact_node is None or impact_node.text.strip() != "High":
                            continue

                        title_node = item.find("title")
                        country_node = item.find("country")
                        date_node = item.find("date")
                        time_node = item.find("time")

                        if any(n is None or n.text is None for n in (title_node, country_node, date_node, time_node)):
                            continue

                        dt_str = f"{date_node.text.strip()} {time_node.text.strip()}"
                        try:
                            event_dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
                            event_ts = event_dt.timestamp()
                        except Exception:
                            continue  # Skip malformed dates

                        unique_id = f"{title_node.text.strip()}_{event_ts}"

                        if now_ts <= event_ts <= limit_ts and unique_id not in seen_titles:
                            all_events.append(
                                {
                                    "title": title_node.text.strip(),
                                    "country": country_node.text.strip(),
                                    "date": event_dt.strftime("%m-%d"),
                                    "time": event_dt.strftime("%H:%M UTC"),
                                    "timestamp": event_ts,
                                }
                            )
                            seen_titles.add(unique_id)
                            events_in_this_feed += 1
                            target_event_count += 1

                    if events_in_this_feed > 0:
                        print(f"   - ✅ Success: Extracted {events_in_this_feed} unique High-Impact events.")
                    else:
                        print("   - 💡 Note: No future High-Impact events found (Weekend Rollover Mode).")

                    feed_success = True
                    total_successful_feeds += 1
                    break
            except Exception as e:
                print(f"   - ❌ Error: {e}")
                continue

        if not feed_success:
            print(f"   - {target['label']} feed unavailable after primary/fallback attempts.")
        target_status[target["label"]] = {
            "ok": feed_success,
            "count": target_event_count,
        }

    # --- Next Week Injection via Investing.com ---
    # Fetch Next Week data exclusively from Investing.com if requested
    investing_success = False
    investing_count = 0
    if horizon_days > 1:
        success, investing_events = await _fetch_investing_macro_events(session, "nextWeek")
        if success:
            investing_success = True
            for ev in investing_events:
                unique_id = f"{ev['title']}_{ev['timestamp']}"
                if now_ts <= ev["timestamp"] <= limit_ts and unique_id not in seen_titles:
                    all_events.append(ev)
                    seen_titles.add(unique_id)
                    investing_count += 1

        target_status["Next Week"] = {
            "ok": investing_success,
            "count": investing_count,
        }

    this_week_ok = target_status.get("This Week", {}).get("ok", False)
    next_week_ok = target_status.get("Next Week", {}).get("ok", False)
    this_week_count = target_status.get("This Week", {}).get("count", 0)
    next_week_count = target_status.get("Next Week", {}).get("count", 0)

    if horizon_days > 1:
        # For weekly reports, we need both This Week (FF) and Next Week (Investing) to be OK
        partial_failure = (this_week_ok or next_week_ok) and not (this_week_ok and next_week_ok)
        total_success = this_week_ok and next_week_ok
    else:
        # For daily reports, we usually only care about the This Week feed
        partial_failure = not this_week_ok
        total_success = this_week_ok

    print(
        "📊 Macro Fetch Summary: "
        f"this_week_ok={this_week_ok} count={this_week_count} | "
        f"next_week_ok={next_week_ok} count={next_week_count} | "
        f"partial_failure={partial_failure} | total_events={len(all_events)}"
    )

    return {
        "success": total_success,
        "partial_failure": partial_failure,
        "this_week_ok": this_week_ok,
        "next_week_ok": next_week_ok,
        "events": sorted(all_events, key=lambda x: x["timestamp"]),
    }


async def fetch_macro_events(session):
    """Fetch high-impact economic events for the next 24 hours (Daily Digest)."""
    return await _fetch_all_high_impact_events(session, horizon_days=1)


async def fetch_weekly_macro_events(session):
    """Fetch high-impact economic events for the next 7 days (Admin Audit)."""
    return await _fetch_all_high_impact_events(session, horizon_days=7)


def format_user_label(user_row):
    user_id, username, full_name, status = user_row
    handle = f"@{username}" if username else "no_username"
    return f"- {html_safe(full_name)} ({handle}) [{status}] — <code>{user_id}</code>"


def format_channel_label(channel_row):
    channel_id, channel_name, status = channel_row
    return f"- {html_safe(channel_name)} [{status}] — <code>{channel_id}</code>"


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
    ]
    return "\n".join(common_lines + [""] + admin_lines)


async def send_daily_digest(session, target_date_str, mark_as_sent=True, target_chat_id=None):
    config = await load_config()

    # 1. Fetch shared context (Macro, Liq, News) to reuse across users
    top_news = await fetch_top_news(session)
    macro_data = await fetch_macro_events(session)
    upcoming_macro = macro_data["events"] if macro_data.get("events") else []
    cutoff_iso = (utc_now() - timedelta(hours=24)).isoformat()
    liq_stats = await asyncio.to_thread(database.get_24h_liquidation_stats, cutoff_iso)

    async def build_content(symbols_list, user_tier="free"):
        snapshots = []
        for symbol in symbols_list:
            snapshots.append(await fetch_symbol_snapshot(session, symbol, config))

        pulse = calculate_market_pulse(snapshots, config)
        return await build_digest_message(
            session,
            snapshots,
            pulse,
            config,
            top_news=top_news,
            upcoming_macro=upcoming_macro,
            liq_stats=liq_stats,
            macro_partial_failure=macro_data.get("partial_failure", False) and not upcoming_macro,
            user_tier=user_tier,
        )

    # 2. Prepare Global Content (for Free users and Channels)
    global_digest = await build_content(config["digest"]["symbols"], "free")

    if target_chat_id:
        # Handle specific target (e.g., test command)
        tier = await asyncio.to_thread(database.get_user_tier, target_chat_id)
        digest_to_send = global_digest
        if tier == "pro":
            watchlist = await asyncio.to_thread(database.get_watchlist, target_chat_id)
            if watchlist:
                digest_to_send = await build_content(watchlist, "pro")
            else:
                digest_to_send = await build_content(config["digest"]["symbols"], "pro")

        await send_telegram_message(session, target_chat_id, digest_to_send)
        await asyncio.to_thread(database.log_alert, "test_digest", "ADMIN", digest_to_send, iso_now())
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
            watchlist = await asyncio.to_thread(database.get_watchlist, user_id)
            if watchlist:
                # Build custom content for this specific Pro user
                user_digest = await build_content(watchlist, "pro")
                if await send_telegram_message(session, user_id, user_digest):
                    sent += 1
                continue

        # Free users or Pro without watchlist get global content
        if await send_telegram_message(session, user_id, global_digest):
            sent += 1

    await asyncio.to_thread(database.log_alert, "daily_digest", "MULTI", global_digest, iso_now())
    if mark_as_sent:
        await asyncio.to_thread(mark_digest_sent, target_date_str)
    print(f"Daily Digest sent to {sent} recipients.")
    return global_digest


async def rsi_scanner_loop(session):
    print("📡 Intelligence Signal Scanner is Live (Confluence Mode)...")
    while True:
        try:
            config = await load_config()
            tracked_coins = config["tracked_coins"]
            rsi_config = config["rsi"]

            # Fetch global sensitivity threshold
            sensitivity = await asyncio.to_thread(database.get_sensitivity)

            # Fetch Market Pulse once per scan cycle
            pulse_symbols = ["BTC", "ETH", "BNB", "SOL"]
            pulse_snapshots = []
            for ps in pulse_symbols:
                snap = await fetch_symbol_snapshot(session, ps, config)
                if snap:
                    pulse_snapshots.append(snap)

            pulse_result = calculate_market_pulse(pulse_snapshots, config)
            pulse_score = pulse_result["score"]

            for symbol in tracked_coins:
                pair = symbol_to_pair(symbol)
                for interval, meta in rsi_config["intervals"].items():
                    state = parse_timeframe_state(pair, interval)

                    # Fetch candles for RSI and local SMA
                    candles = await fetch_with_backoff(
                        session,
                        "https://api.binance.com/api/v3/klines",
                        {"symbol": pair, "interval": interval, "limit": 80},
                    )
                    if not candles:
                        continue

                    close_prices = [float(candle[4]) for candle in candles]
                    volumes = [float(candle[5]) for candle in candles]
                    current_price = close_prices[-1]
                    rsi = calculate_rsi(close_prices)

                    # Basic direction gate
                    if rsi > 30 and rsi < 70:
                        # Reset state if neutral
                        if state["state"] != "NEUTRAL":
                            state["state"] = "NEUTRAL"
                            await asyncio.to_thread(save_rsi_state, rsi_state_tracker)
                        continue

                    recent_volumes = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
                    average_volume = sum(recent_volumes) / max(len(recent_volumes), 1)
                    volume_ratio = (volumes[-1] / average_volume) if average_volume else 1.0

                    current_state = "OVERBOUGHT" if rsi >= 70 else "OVERSOLD"

                    # Check if state changed or we are re-triggering after cooldown
                    is_new_state = current_state != state["state"]
                    is_cooldown_done = cooldown_complete(state["last_alert_at"], rsi_config["cooldown_hours"])

                    if is_new_state or is_cooldown_done:
                        # Fetch extra factors for scoring
                        sma50_4h = await fetch_sma_trend(session, symbol, "4h", 50)
                        sma50_1d = await fetch_sma_trend(session, symbol, "1d", 50)
                        liq_data = await fetch_coin_liquidations(session, symbol)

                        symbol_data = {
                            "symbol": symbol,
                            "price": current_price,
                            "sma50": sma50_4h,  # Primary trend on 4H
                            "rsi": rsi,
                            "volume_ratio": volume_ratio,
                            "trend_4h": "BULL" if current_price > sma50_4h else "BEAR",
                            "trend_1d": "BULL" if current_price > sma50_1d else "BEAR",
                        }

                        signal_result = calculate_score(symbol_data, pulse_score, liq_data)

                        if signal_result:
                            score = signal_result["score"]

                            # Log to signal_history regardless of threshold (for backtesting)
                            await asyncio.to_thread(
                                database.log_signal,
                                symbol,
                                "RSI",
                                signal_result["direction"],
                                score,
                                signal_result["components"]["trend_score"],
                                signal_result["components"]["volume_score"],
                                signal_result["components"]["rsi_score"],
                                signal_result["components"]["liq_score"],
                                signal_result["components"]["pulse_score"],
                                signal_result["components"]["alignment_score"],
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

                            # Filter by sensitivity
                            if score >= sensitivity:
                                await dispatch_signal_alert(session, signal_result, current_price)
                                state["last_alert_at"] = utc_now().isoformat()
                                print(f"🚀 Signal dispatched: {symbol} {interval} Score:{score}")
                            else:
                                print(
                                    f"🟡 Signal suppressed (Score {score} < Threshold {sensitivity}): "
                                    f"{symbol} {interval}"
                                )

                        state["state"] = current_state
                        await asyncio.to_thread(save_rsi_state, rsi_state_tracker)

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

            if now >= target and await asyncio.to_thread(has_sent_digest_today, target_date_str):
                target += timedelta(days=1)
                target_date_str = target.strftime("%Y-%m-%d")
            if now < target:
                await asyncio.sleep((target - now).total_seconds())

            if await asyncio.to_thread(has_sent_digest_today, target_date_str):
                continue

            await send_daily_digest(session, target_date_str, mark_as_sent=True)
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
            tracked_coins = config.get("tracked_coins", DEFAULT_CONFIG["tracked_coins"])

            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as websocket:
                print("✅ Connected to Market-Wide WebSocket successfully!")
                retry_count = 0  # Reset on success
                while True:
                    response = await websocket.recv()
                    payload = json.loads(response)

                    # Binance !forceOrder@arr returns an array of events
                    data_list = payload if isinstance(payload, list) else [payload]

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
                        await asyncio.to_thread(database.log_liquidation, symbol, side, total_value, iso_now())

                        # Alert logic for specific coins only
                        if symbol in tracked_coins:
                            # User requested less strict: $200k floor for "Small Scale"
                            if total_value >= 200000:
                                position_type = "SHORT 🔴" if side == "BUY" else "LONG 🟢"
                                trend, _ = await get_market_context(session, symbol)
                                _, confidence, insight = await calculate_confluence_score(total_value, side, trend)

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
                                    await broadcast_message(session, alert_message)
                                    await asyncio.to_thread(
                                        database.log_alert, "liquidation", symbol, alert_message, iso_now()
                                    )

        except Exception as exc:
            retry_count += 1
            print(f"Market-Wide WebSocket Error (Attempt {retry_count}): {exc}")

            if retry_count == 3:
                admin_alert = (
                    f"⚠️ <b>CRITICAL ALERT</b>\n\n"
                    f"Binance Market-Wide Liquidation WebSocket failed to reconnect after 3 attempts.\n"
                    f"<b>Error:</b> {html_safe(str(exc))}"
                )
                await send_telegram_message(session, ADMIN_USER_ID, admin_alert)
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
    ]

    try:
        # 1. Set global commands
        await session.post(url, json={"commands": common_commands})

        # 2. Set admin-specific commands
        admin_scope = {"type": "chat", "chat_id": str(ADMIN_USER_ID)}
        await session.post(url, json={"commands": admin_only, "scope": admin_scope})

        print("✅ Telegram Command Menu synchronized (Admin Scope included).")
    except Exception as exc:
        print(f"Error registering commands: {exc}")


async def handle_admin_command(session, command, args, chat_id):
    if command == "/status":
        await send_telegram_message(session, chat_id, await build_status_message())
        return

    if command == "/channels":
        all_chans = await asyncio.to_thread(database.get_all_channels)
        channels = [row for row in all_chans if row[2] == "ACTIVE"]
        lines = (
            ["<b>Active Channels</b>"] + [format_channel_label(row) for row in channels]
            if channels
            else ["No channels found."]
        )
        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/users":
        all_users = await asyncio.to_thread(database.get_all_users)
        users = [row for row in all_users if row[3] == "ACTIVE"]
        lines = (
            ["<b>Registered Users</b>"] + [format_user_label(row) for row in users] if users else ["No users found."]
        )
        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/setpro":
        target_user = args.strip() if args else ""
        if not target_user or not re.match(r"^-?\d+$", target_user):
            await send_telegram_message(session, chat_id, "ℹ️ Usage: /setpro &lt;id&gt;")
            return
        await asyncio.to_thread(database.set_user_tier, chat_id, target_user, "pro")
        await send_telegram_message(session, chat_id, f"✅ User {target_user} upgraded to Pro.")
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
            await send_telegram_message(session, chat_id, "ℹ️ Usage: /setfree &lt;id&gt;")
            return
        await asyncio.to_thread(database.set_user_tier, chat_id, target_user, "free")
        await send_telegram_message(session, chat_id, f"⚠️ User {target_user} moved to Free tier.")
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
            chunk = lines[i:i + 20]
            header = f"👥 <b>User Directory</b> (Total: {total})\n\n" if i == 0 else ""
            await send_telegram_message(session, chat_id, header + "\n".join(chunk))
        return

    if command == "/prolist":
        users = await asyncio.to_thread(database.get_all_users_with_tiers)
        pro_users = [u for u in users if u[2] == "pro"]
        if not pro_users:
            await send_telegram_message(session, chat_id, "ℹ️ No Pro users found.")
            return

        lines = []
        for uid, uname, tier, expiry in pro_users:
            label = f"@{uname}" if uname else uid
            exp_date = expiry[:10] if expiry else "N/A"
            lines.append(f"💎 {label} — ID: <code>{uid}</code> (Exp: {exp_date})")

        header = f"💎 <b>Pro User Directory</b> (Total: {len(pro_users)})\n\n"
        await send_telegram_message(session, chat_id, header + "\n".join(lines))
        return

    if command == "/send":
        if not args:
            await send_telegram_message(session, chat_id, "Usage: /send &lt;message&gt;")
            return
        message = f"<b>📢 ADMIN BROADCAST</b>\n\n{html_safe(args)}"
        sent = await broadcast_message(session, message)
        await asyncio.to_thread(database.log_alert, "admin_broadcast", "MULTI", message, iso_now())
        await send_telegram_message(session, chat_id, f"Broadcast delivered to {sent} recipients.")
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
            await send_telegram_message(session, chat_id, "❌ Invalid value. Must be between 40 and 90.")
            return
        await asyncio.to_thread(database.set_sensitivity, new_val)
        await send_telegram_message(session, chat_id, f"✅ Signal sensitivity updated to {new_val}/100.")
        return

    if command == "/testsignal":
        symbol = args.strip().upper() if args else "BTC"
        await send_telegram_message(session, chat_id, f"🔍 Simulating Private Intelligence Signal for {symbol}...")
        
        # Fetch current price
        price_url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
        price_data = await fetch_with_backoff(session, price_url)
        current_price = float(price_data['price']) if price_data else 0.0
        
        # Dummy market context
        liq_data = {"longs": 55000, "shorts": 12000} 
        pulse_score = 25 # Extreme Fear
        
        from utils.scoring_engine import calculate_score
        test_data = {
            "symbol": symbol,
            "price": current_price,
            "sma50": current_price * 0.98,
            "rsi": 28,
            "volume_ratio": 2.1,
            "trend_4h": "BEAR",
            "trend_1d": "BULL"
        }
        
        signal_result = calculate_score(test_data, pulse_score, liq_data)
        if signal_result:
            # SEND ONLY TO THE ADMIN (Private)
            # Use logic to pick pro_msg since admin is pro
            pro_msg = (
                f"🚨 <b>{html_safe(symbol)} Intelligence Signal</b> (Private Test)\n\n"
                f"Direction: 🟢 LONG — Oversold Setup\n"
                f"Price: ${current_price:,.2f}\n\n"
                f"📊 Signal Confidence: {signal_result['meter']} {signal_result['score']}%\n"
                f"Strength: <b>{html_safe(signal_result['strength'])}</b>\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<b>Confluence Breakdown:</b>\n"
                f"{chr(10).join(['  • ' + html_safe(r) for r in signal_result['rationale']])}\n\n"
                f"<b>Focus:</b>\n"
                f"{html_safe('High conviction oversold setup. Watch for volume confirmation on the bounce.' if signal_result['score'] >= 80 else 'Oversold condition. Wait for stabilization.')}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<i>Market analysis only. Not financial advice.</i>"
            )
            await send_telegram_message(session, chat_id, pro_msg)
        return

    if command == "/signal":
        symbol = args.strip().upper() if args else "BTC"
        await send_telegram_message(session, chat_id, f"🚨 Triggering Manual BROADCAST for {symbol}...")
        
        # Fetch price
        price_url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
        price_data = await fetch_with_backoff(session, price_url)
        current_price = float(price_data['price']) if price_data else 0.0
        
        # Dummy context
        liq_data = {"longs": 55000, "shorts": 12000} 
        pulse_score = 25 
        
        from utils.scoring_engine import calculate_score
        test_data = {
            "symbol": symbol, "price": current_price, "sma50": current_price * 0.98,
            "rsi": 28, "volume_ratio": 2.1, "trend_4h": "BEAR", "trend_1d": "BULL"
        }
        
        signal_result = calculate_score(test_data, pulse_score, liq_data)
        if signal_result:
            # BROADCAST TO EVERYONE
            await dispatch_signal_alert(session, signal_result, current_price)
        return

    if command == "/macro":
        result = await fetch_weekly_macro_events(session)
        events = result.get("events", [])
        this_week_ok = result.get("this_week_ok", False)
        next_week_ok = result.get("next_week_ok", False)
        partial_failure = result.get("partial_failure", False)

        # Case 1: Both sources failed AND no events at all
        if not this_week_ok and not next_week_ok and not events:
            await send_telegram_message(
                session, chat_id, "⚠️ Macro calendar feed unavailable right now. Please try again later."
            )
            return

        # Case 2: Both sources succeeded but truly no events
        if this_week_ok and next_week_ok and not events:
            await send_telegram_message(session, chat_id, "📅 No High Impact macro events found for the next 7 days.")
            return

        # Case 3: No events but partial failure (one source down)
        if not events and partial_failure:
            source_down = "Current-week" if not this_week_ok else "Next-week"
            await send_telegram_message(
                session,
                chat_id,
                f"⚠️ {source_down} macro source unavailable right now. "
                "No events from the working source either. Try again later.",
            )
            return

        # Case 4: Events exist — always show them, with warning if partial failure
        lines = ["<b>📅 WEEKLY MACRO AUDIT (High Impact)</b>", ""]
        if partial_failure:
            source_down = "Current-week" if not this_week_ok else "Next-week"
            lines.insert(0, f"⚠️ {source_down} macro source partially unavailable. Showing available events.\n")
        for e in events:
            lines.append(f"• <code>{e['date']} {e['time']}</code>")
            lines.append(f"  <b>{html_safe(e['title'])}</b> ({e['country']})")
            lines.append("")

        await send_telegram_message(session, chat_id, "Audit report:\n" + "\n".join(lines))
        return

    if command == "/update_powell":
        parts = args.split(" ", 2)
        if len(parts) < 3:
            await send_telegram_message(
                session, chat_id, "Usage: /update_powell &lt;stance&gt; &lt;score&gt; &lt;note&gt;"
            )
            return
        stance, score_text, note = parts
        try:
            score = max(1, min(5, int(score_text)))
        except ValueError:
            await send_telegram_message(session, chat_id, "Powell score must be a number between 1 and 5.")
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
        await send_telegram_message(session, chat_id, "Powell context updated successfully.")
        return

    if command == "/update_etf":
        if not args:
            await send_telegram_message(session, chat_id, "Usage: /update_etf &lt;summary&gt;")
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
        await send_telegram_message(session, chat_id, "ETF note updated successfully.")
        return

    if command == "/update_coins":
        parts = args.split()
        if len(parts) != 2:
            await send_telegram_message(session, chat_id, "Usage: /update_coins &lt;add/remove&gt; &lt;symbol&gt;")
            return
        action, symbol = parts[0].lower(), parts[1].upper()
        if action not in {"add", "remove"}:
            await send_telegram_message(session, chat_id, "Action must be add or remove.")
            return
        if action == "remove" and symbol in {"BTC", "ETH", "SOL"}:
            await send_telegram_message(session, chat_id, "BTC, ETH, and SOL stay pinned for the core digest.")
            return

        def mutate(config):
            coins = list(dict.fromkeys(config["tracked_coins"]))
            if action == "add" and symbol not in coins:
                coins.append(symbol)
            if action == "remove" and symbol in coins and len(coins) > 1:
                coins.remove(symbol)

            digest_symbols = [coin for coin in config["digest"]["symbols"] if coin in coins]
            for required in ["BTC", "ETH", "SOL"]:
                if required in coins and required not in digest_symbols:
                    digest_symbols.append(required)

            config["tracked_coins"] = coins
            config["digest"]["symbols"] = [coin for coin in digest_symbols if coin in coins][:3]
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
        await send_telegram_message(session, chat_id, "Digest broadcasted manually.")
        return

    if command == "/test_digest":
        target_date_str = utc_now().strftime("%Y-%m-%d")
        await send_daily_digest(session, target_date_str, mark_as_sent=False, target_chat_id=chat_id)
        # Test digest sends a confirmation inside the digest engine output or can be silent.
        return

    if command == "/help":
        await send_telegram_message(session, chat_id, build_help_message(True))
        return

    if command == "/revoke":
        target_customer = args.strip()
        if not target_customer:
            await send_telegram_message(session, chat_id, "Usage: /revoke &lt;channel name or id&gt;")
            return
        actual_id = await asyncio.to_thread(database.get_channel_id_by_name, target_customer)
        success = await asyncio.to_thread(database.ban_channel, target_customer)
        if success:
            await send_telegram_message(session, chat_id, f"🚫 Access revoked for {html_safe(target_customer)}")
            if actual_id and str(actual_id) != ADMIN_USER_ID:
                leave_url = f"{TELEGRAM_API_URL}/leaveChat"
                await session.post(leave_url, json={"chat_id": str(actual_id)})
        else:
            await send_telegram_message(session, chat_id, f"⚠️ Could not find {html_safe(target_customer)}.")
        return

    if command == "/renew":
        target_customer = args.strip()
        if not target_customer:
            await send_telegram_message(session, chat_id, "Usage: /renew &lt;channel name or id&gt;")
            return
        success = await asyncio.to_thread(database.renew_channel, target_customer)
        if success:
            await send_telegram_message(session, chat_id, f"✅ Access renewed for {html_safe(target_customer)}")
        else:
            await send_telegram_message(session, chat_id, f"⚠️ Could not find {html_safe(target_customer)}.")
        return

    await send_telegram_message(session, chat_id, "Unknown admin command. Use /help.")


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
                    source = article.get("source_info", {}).get("name", "Unknown Source")
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
                    if importance_data["level"] not in ("CRITICAL", "MODERATE"):
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
                    await asyncio.to_thread(database.log_alert, "breaking_news", "MULTI", alert, iso_now())
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
            result = await fetch_macro_events(session)
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
                    titles_block = "\n".join([f"• {html_safe(e['title'])}" for e in grouped_evs])
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
                await asyncio.to_thread(database.log_alert, "macro_warning", "GLOBAL", alert, iso_now())

                # Mark all as sent
                for ev in grouped_evs:
                    eid = f"{ev['timestamp']}_{ev['title']}"
                    sent_events.append(eid)

                await asyncio.to_thread(save_macro_state, sent_events)
                print(f"Grouped Macro warning sent: {len(grouped_evs)} events for {country}")

        except Exception as exc:
            print(f"Macro Calendar Error: {exc}")
            print(traceback.format_exc())

        await asyncio.sleep(300)  # Check every 5 minutes for precise 2-hour alerts


async def database_maintenance_loop():
    """Runs database cleanup once on startup and then every 7 days."""
    print("🧹 Database Maintenance Engine is Live...")
    while True:
        try:
            await database.cleanup_old_data()
        except Exception as exc:
            print(f"Database Maintenance Error: {exc}")

        # Wait for 7 days (604800 seconds)
        await asyncio.sleep(604800)


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
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=45)) as response:
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
                        chat_name = chat_info.get("title", f"Private User {chat_id}")
                        if new_status in ["administrator", "member"]:
                            await asyncio.to_thread(database.add_channel, str(chat_id), chat_name)
                            print(f"Registered new Channel: {chat_name} to SQLite.")

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
                            first_name = message.get("from", {}).get("first_name", "")
                            last_name = message.get("from", {}).get("last_name", "")
                            full_name = f"{first_name} {last_name}".strip() or first_name or "Telegram User"
                            username = message.get("from", {}).get("username", "")
                            await asyncio.to_thread(database.add_user, chat_id, username, full_name)
                            welcome = (
                                "<b>🤖 Welcome to the Crypto Analysis Bot</b>\n"
                                "You are now registered for DM updates.\n\n"
                                f"{build_help_message(is_admin)}"
                            )
                            await send_telegram_message(session, chat_id, welcome)
                        else:
                            await send_telegram_message(session, chat_id, "Bot is online. Use /help for commands.")
                        continue

                    if command == "/help":
                        await send_telegram_message(session, chat_id, build_help_message(is_admin))
                        continue

                    if command == "/feedback":
                        if not args:
                            await send_telegram_message(session, chat_id, "Usage: /feedback &lt;your suggestion&gt;")
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
                        await send_telegram_message(session, ADMIN_USER_ID, relay_msg)
                        # Confirm to User
                        await send_telegram_message(
                            session, chat_id, "✅ Thank you! Your feedback has been sent to the developers."
                        )
                        continue

                    if command == "/mycoins":
                        if not args:
                            coins = await asyncio.to_thread(database.get_watchlist, chat_id)
                            if coins:
                                await send_telegram_message(
                                    session, chat_id, f"✅ Current Watchlist: {', '.join(coins)}"
                                )
                            else:
                                await send_telegram_message(
                                    session,
                                    chat_id,
                                    "Your watchlist is empty. Set it using <code>/mycoins BTC ETH SOL</code>",
                                )
                            continue

                        if args.lower() == "clear":
                            await asyncio.to_thread(database.clear_watchlist, chat_id)
                            await send_telegram_message(session, chat_id, "✅ Watchlist cleared.")
                            continue

                        # Save new watchlist (Fix 6: Deduplicate inputs)
                        new_coins = list(dict.fromkeys([c.strip().upper() for c in args.split() if c.strip()]))
                        if not new_coins:
                            await send_telegram_message(session, chat_id, "Usage: <code>/mycoins BTC ETH SOL</code>")
                            continue

                        await asyncio.to_thread(database.save_watchlist, chat_id, new_coins)

                        tier = await asyncio.to_thread(database.get_user_tier, chat_id)
                        msg = f"✅ Watchlist saved — {', '.join(new_coins)}."
                        if tier == "free":
                            msg += (
                                "\n\nUpgrade to Pro and every digest, alert, and analysis "
                                "will be built around only these coins. /upgrade"
                            )
                        await send_telegram_message(session, chat_id, msg)
                        continue

                    if command == "/upgrade":
                        user_tier = await asyncio.to_thread(database.get_user_tier, chat_id)
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
                        await handle_admin_command(session, command, args, chat_id)

        except Exception as exc:
            print(f"Error in telegram_long_polling: {exc}")
        await asyncio.sleep(0.5)


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
            safe_loop(database_maintenance_loop),
            safe_loop(run_webhook_server, session, TELEGRAM_API_URL),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
