import asyncio
import html
import json
import os
from datetime import datetime, timedelta, timezone

import aiohttp
import websockets
from dotenv import load_dotenv

import database

load_dotenv()

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "1120431469")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set. Bot shutting down.")

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
if not DATA_DIR:
    raise ValueError(
        "CRITICAL ERROR: RAILWAY_VOLUME_MOUNT_PATH environment variable is not set. "
        "A mounted volume is required to persist state."
    )

os.makedirs(DATA_DIR, exist_ok=True)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
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
}


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


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
        config = read_json_file(CONFIG_FILE, DEFAULT_CONFIG)
        write_json_file(CONFIG_FILE, config)
        return config


async def update_config(mutator):
    async with CONFIG_LOCK:
        config = read_json_file(CONFIG_FILE, DEFAULT_CONFIG)
        updated_config = mutator(config)
        write_json_file(CONFIG_FILE, updated_config)
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
                print(f"API fetch failed ({response.status}) for {url}")
        except Exception as exc:
            print(f"API fetch error on attempt {attempt + 1}: {exc}")
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)
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
                print(f"Telegram send failed ({response.status}) for {chat_id}")
        except Exception as exc:
            print(f"Telegram send error on attempt {attempt + 1}: {exc}")
        await asyncio.sleep(1)
    print(f"Failed to send message to {chat_id}")
    return False


async def broadcast_message(session, text, include_channels=True, include_users=True):
    recipients = []
    if include_channels:
        recipients.extend(database.get_active_channels())
    if include_users:
        recipients.extend(database.get_active_users())

    sent = 0
    for chat_id in sorted(set(recipients)):
        if await send_telegram_message(session, chat_id, text):
            sent += 1
    return sent


def html_safe(value):
    return html.escape(str(value), quote=False)


def symbol_to_pair(symbol):
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"


def pair_to_tag(symbol):
    return symbol.replace("USDT", "")


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
    """Returns 'critical', 'high', or None based on keywords in the headline."""
    title_lower = title.lower()
    critical_keywords = (
        "hack", "hacked", "exploit", "stolen", "ban", "banned", "arrest",
        "sec sues", "sec charges", "etf approved", "etf rejected",
        "collapse", "insolvent", "emergency", "halted",
    )
    high_keywords = (
        "etf", "regulation", "federal reserve", "fed rate", "interest rate",
        "inflation", "cpi", "powell", "blackrock", "grayscale", "coinbase",
        "binance", "whale", "all-time high", "ath", "crash", "dump", "pump",
    )
    if any(kw in title_lower for kw in critical_keywords):
        return "critical"
    if any(kw in title_lower for kw in high_keywords):
        return "high"
    return None


async def get_market_context(session, symbol):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": "4h", "limit": 50}
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


async def fetch_symbol_snapshot(session, symbol):
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
    current_volume = volumes[-1]
    recent_volumes = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
    average_volume = sum(recent_volumes) / max(len(recent_volumes), 1)
    volume_ratio = current_volume / average_volume if average_volume else 1.0
    volume_delta = (volume_ratio - 1) * 100
    change_24h = ((current_price - previous_price) / previous_price * 100) if previous_price else 0.0
    trend = "BULLISH 🟢" if current_price > sma_50 else "BEARISH 🔴"
    volume_status = "Above average" if volume_ratio >= 1.15 else "Normal"

    return {
        "symbol": pair,
        "tag": pair_to_tag(pair),
        "price": current_price,
        "change_24h": change_24h,
        "trend": trend,
        "rsi": rsi,
        "volume_ratio": volume_ratio,
        "volume_delta": volume_delta,
        "volume_status": volume_status,
    }


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
            score -= 8
            bearish_count += 1

        if snapshot["rsi"] >= 65:
            score += 5
        elif snapshot["rsi"] <= 35:
            score -= 5

        if snapshot["change_24h"] >= 2:
            score += 4
        elif snapshot["change_24h"] <= -2:
            score -= 4

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

    if bullish_count > bearish_count:
        watch_next = "Watch whether bullish trend breadth holds into the next US session."
    elif bearish_count > bullish_count:
        watch_next = "Watch whether sellers can keep control or if oversold conditions trigger a relief bounce."
    else:
        watch_next = "Watch for a breakout from the current balance between trend and momentum."

    return {"score": score, "label": label, "watch_next": watch_next}


def build_digest_message(snapshots, pulse, config, top_news=None):
    lines = [
        "<b>📰 DAILY MARKET DIGEST</b>",
        f"<b>Time:</b> {html_safe(iso_now())}",
        "",
        f"<b>Market Pulse:</b> {pulse['label']} ({pulse['score']}/100)",
        f"<b>Why it matters:</b> {html_safe(config['powell']['stance'].title())} Fed tone and current trend breadth are shaping risk appetite.",
        f"<b>Watch next:</b> {html_safe(pulse['watch_next'])}",
        "",
        "<b>Macro Context</b>",
        f"- <b>Powell Tone:</b> {html_safe(config['powell']['stance'].title())} ({config['powell']['score']}/5) | {html_safe(config['powell']['note'])}",
        f"- <b>ETF Note:</b> {html_safe(config['etf']['summary'])}",
        "",
        "<b>Major Coin Snapshot</b>",
    ]

    for symbol in config["digest"]["symbols"]:
        snapshot = next((item for item in snapshots if item and item["tag"] == symbol), None)
        if snapshot is None:
            lines.append(f"<b>#{html_safe(symbol)}</b>: Data unavailable")
            lines.append("- Why it matters: market input missing")
            lines.append("- Watch next: retry next scheduled digest")
            lines.append("")
            continue

        lines.append(f"<b>#{html_safe(snapshot['tag'])}</b>: ${snapshot['price']:,.2f} ({snapshot['change_24h']:+.2f}% 24h)")
        lines.append(f"- <b>Trend:</b> {snapshot['trend']} vs 50-day average")
        lines.append(f"- <b>Daily RSI:</b> {snapshot['rsi']:.1f}")
        lines.append(f"- <b>Volume:</b> {html_safe(snapshot['volume_status'])} ({snapshot['volume_delta']:+.1f}% vs average)")
        lines.append("")

    # News section
    lines.append("<b>📰 Top News (Last 24h)</b>")
    if top_news:
        for i, item in enumerate(top_news, 1):
            lines.append(f"{i}. {html_safe(item['title'])} — <i>{html_safe(item['source'])}</i>")
    else:
        lines.append("- No major news detected in the last 24 hours.")

    lines.append("")
    lines.append("<i>Disclaimer: Market analysis only, not financial advice.</i>")

    return "\n".join(lines).strip()


async def fetch_top_news(session):
    """Fetch up to 3 critical/high-importance headlines published in the last 24 hours."""
    try:
        url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"
        data = await fetch_with_backoff(session, url, retries=2)
        if not data or data.get("Type") != 100:
            return []
        cutoff = utc_now().timestamp() - 86400  # 24 hours ago
        top_news = []
        for article in data.get("Data", [])[:30]:
            published_on = article.get("published_on", 0)
            if published_on < cutoff:
                continue  # Skip articles older than 24 hours
            title = article.get("title", "")
            source = article.get("source_info", {}).get("name", "")
            if classify_news_importance(title) in ("critical", "high"):
                top_news.append({"title": title, "source": source})
            if len(top_news) >= 3:
                break
        return top_news
    except Exception as exc:
        print(f"fetch_top_news error: {exc}")
        return []


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
    return (
        "<b>Bot Status</b>\n"
        f"- <b>Uptime:</b> {str(uptime).split('.')[0]}\n"
        f"- <b>Tracked Coins:</b> {html_safe(tracked)}\n"
        f"- <b>Channels:</b> {len(database.get_active_channels())}\n"
        f"- <b>Users:</b> {len(database.get_active_users())}\n"
        f"- <b>Data Dir:</b> <code>{html_safe(DATA_DIR)}</code>"
    )


def build_help_message(is_admin):
    common_lines = [
        "<b>Available Commands</b>",
        "/start — register and receive updates in DM",
        "/help — show command list",
    ]
    if not is_admin:
        return "\n".join(common_lines)

    admin_lines = [
        "/status — bot health summary",
        "/channels — list active channels",
        "/users — list active DM users",
        "/send &lt;message&gt; — broadcast to users and channels",
        "/digest — send the digest immediately",
        "/update_powell &lt;stance&gt; &lt;score&gt; &lt;note&gt;",
        "/update_etf &lt;summary&gt;",
        "/update_coins &lt;add/remove&gt; &lt;symbol&gt;",
        "/revoke &lt;channel name or id&gt;",
        "/renew &lt;channel name or id&gt;",
    ]
    return "\n".join(common_lines + [""] + admin_lines)


async def send_daily_digest(session, target_date_str, mark_as_sent=True):
    config = await load_config()
    snapshots = []
    for symbol in config["digest"]["symbols"]:
        snapshots.append(await fetch_symbol_snapshot(session, symbol))

    top_news = await fetch_top_news(session)
    pulse = calculate_market_pulse(snapshots, config)
    digest = build_digest_message(snapshots, pulse, config, top_news=top_news)
    sent = await broadcast_message(session, digest)
    database.log_alert("daily_digest", "MULTI", digest, iso_now())
    if mark_as_sent:
        mark_digest_sent(target_date_str)
    print(f"Daily Digest sent to {sent} recipients.")
    return digest


async def rsi_scanner_loop(session):
    print("📡 Multi-Timeframe RSI Scanner is Live...")
    while True:
        try:
            config = await load_config()
            tracked_pairs = [symbol_to_pair(symbol) for symbol in config["tracked_coins"]]
            rsi_config = config["rsi"]

            for pair in tracked_pairs:
                for interval, meta in rsi_config["intervals"].items():
                    state = parse_timeframe_state(pair, interval)
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

                    recent_volumes = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
                    average_volume = sum(recent_volumes) / max(len(recent_volumes), 1)
                    volume_ratio = (volumes[-1] / average_volume) if average_volume else 1.0

                    current_state = "NEUTRAL"
                    if rsi >= rsi_config["overbought_threshold"]:
                        current_state = "OVERBOUGHT"
                    elif rsi <= rsi_config["oversold_threshold"]:
                        current_state = "OVERSOLD"

                    should_alert = (
                        current_state != "NEUTRAL"
                        and current_state != state["state"]
                        and volume_ratio >= rsi_config["volume_multiplier"]
                        and cooldown_complete(state["last_alert_at"], rsi_config["cooldown_hours"])
                    )

                    if should_alert:
                        condition = (
                            "🔥 Overbought momentum with heavy volume. Watch for exhaustion."
                            if current_state == "OVERBOUGHT"
                            else "🥶 Oversold momentum with heavy volume. Watch for rebound risk."
                        )
                        alert = (
                            f"<b>{meta['icon']} RSI EXTREME ALERT - {meta['impact']}</b>\n\n"
                            f"<b>Asset:</b> #{pair_to_tag(pair)}\n"
                            f"<b>Timeframe:</b> {interval.upper()}\n"
                            f"<b>Price:</b> ${current_price:,.2f}\n"
                            f"<b>RSI:</b> {rsi:.2f}\n"
                            f"<b>Volume:</b> {((volume_ratio - 1) * 100):+.1f}% vs average\n"
                            f"<b>Why it matters:</b> {condition}\n"
                            f"<b>Watch next:</b> Confirm follow-through before treating it as a reversal."
                        )
                        sent = await broadcast_message(session, alert)
                        database.log_alert("rsi_alert", pair, alert, iso_now())
                        state["last_alert_at"] = utc_now().isoformat()
                        print(f"RSI Alert sent: {pair} {interval} {current_state} to {sent} recipients")

                    if current_state != state["state"]:
                        state["state"] = current_state
                        save_rsi_state(rsi_state_tracker)

            await asyncio.sleep(600)
        except Exception as exc:
            print(f"RSI Scanner Error: {exc}")
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

            if now >= target and has_sent_digest_today(target_date_str):
                target += timedelta(days=1)
                target_date_str = target.strftime("%Y-%m-%d")
            if now < target:
                await asyncio.sleep((target - now).total_seconds())

            if has_sent_digest_today(target_date_str):
                continue

            await send_daily_digest(session, target_date_str, mark_as_sent=True)
            await asyncio.sleep(60)
        except Exception as exc:
            print(f"Daily Digest Error: {exc}")
            await asyncio.sleep(3600)


async def binance_liquidation_listener(session):
    url = "wss://fstream.binance.com/stream?streams=btcusdt@forceOrder/ethusdt@forceOrder/solusdt@forceOrder"
    print("📡 Connecting to Binance WebSocket for Premium Top 3 Coins (BTC, ETH, SOL)...")

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as websocket:
                print("✅ Connected to Premium Binance WebSocket successfully!")
                while True:
                    response = await websocket.recv()
                    payload = json.loads(response)
                    data = payload.get("data", {})
                    order_data = data.get("o", {})
                    if not order_data:
                        continue

                    symbol = order_data["s"]
                    side = order_data["S"]
                    price = float(order_data["p"])
                    qty = float(order_data["q"])
                    total_value = price * qty

                    if total_value <= 50000:
                        continue

                    position_type = "SHORT 🔴" if side == "BUY" else "LONG 🟢"
                    trend, _ = await get_market_context(session, symbol)
                    _, confidence, insight = await calculate_confluence_score(total_value, side, trend)

                    if confidence <= 40:
                        print(f"Filtered out {symbol} Signal: {confidence}% (Too noisy)")
                        continue

                    alert_message = (
                        f"<b>🔥 LIQUIDATION CONTEXT ALERT</b>\n\n"
                        f"<b>Asset:</b> #{pair_to_tag(symbol)}\n"
                        f"<b>Trigger:</b> {position_type} liquidation\n"
                        f"<b>Value:</b> ${total_value:,.2f}\n"
                        f"<b>Price:</b> ${price:,.2f}\n"
                        f"<b>Trend:</b> {trend}\n"
                        f"<b>Why it matters:</b> {html_safe(insight)}\n"
                        f"<b>Watch next:</b> See whether follow-through confirms this move or fades quickly."
                    )

                    sent = await broadcast_message(session, alert_message, include_users=False)
                    database.log_alert("liquidation", symbol, alert_message, iso_now())
                    print(f"Broadcasted liquidation alert ({confidence}%) to {sent} channels")
        except Exception as exc:
            print(f"❌ WebSocket connection dropped. Reconnecting in 5s... ({exc})")
            await asyncio.sleep(5)


async def handle_admin_command(session, command, args, chat_id):
    if command == "/status":
        await send_telegram_message(session, chat_id, await build_status_message())
        return

    if command == "/channels":
        channels = [row for row in database.get_all_channels() if row[2] == "ACTIVE"]
        lines = ["<b>Active Channels</b>"] + [format_channel_label(row) for row in channels] if channels else ["No channels found."]
        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/users":
        users = [row for row in database.get_all_users() if row[3] == "ACTIVE"]
        lines = ["<b>Registered Users</b>"] + [format_user_label(row) for row in users] if users else ["No users found."]
        await send_telegram_message(session, chat_id, "\n".join(lines))
        return

    if command == "/send":
        if not args:
            await send_telegram_message(session, chat_id, "Usage: /send <message>")
            return
        message = f"<b>📢 ADMIN BROADCAST</b>\n\n{html_safe(args)}"
        sent = await broadcast_message(session, message)
        database.log_alert("admin_broadcast", "MULTI", message, iso_now())
        await send_telegram_message(session, chat_id, f"Broadcast delivered to {sent} recipients.")
        return

    if command == "/update_powell":
        parts = args.split(" ", 2)
        if len(parts) < 3:
            await send_telegram_message(session, chat_id, "Usage: /update_powell <stance> <score> <note>")
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
            await send_telegram_message(session, chat_id, "Usage: /update_etf <summary>")
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
            await send_telegram_message(session, chat_id, "Usage: /update_coins <add/remove> <symbol>")
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
        await send_telegram_message(session, chat_id, "Digest sent manually.")
        return

    if command == "/help":
        await send_telegram_message(session, chat_id, build_help_message(True))
        return

    if command == "/revoke":
        target_customer = args.strip()
        if not target_customer:
            await send_telegram_message(session, chat_id, "Usage: /revoke <channel name or id>")
            return
        actual_id = database.get_channel_id_by_name(target_customer)
        success = database.ban_channel(target_customer)
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
            await send_telegram_message(session, chat_id, "Usage: /renew <channel name or id>")
            return
        success = database.renew_channel(target_customer)
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
    sent_ids = load_news_state()

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

                    importance = classify_news_importance(title)
                    if importance not in ("critical", "high"):
                        continue

                    icon = "🔴" if importance == "critical" else "🟠"
                    alert = (
                        f"<b>{icon} BREAKING NEWS</b>\n\n"
                        f"<b>{html_safe(title)}</b>\n\n"
                        f"<b>Source:</b> {html_safe(source)}\n"
                        f"<b>Impact:</b> {importance.upper()}\n\n"
                        f"<i>{html_safe(body_snippet)}...</i>\n\n"
                        f"<b>Why it matters:</b> Monitor price action closely for any market reaction.\n"
                        f"<i>Disclaimer: Market analysis only, not financial advice.</i>"
                    )

                    await broadcast_message(session, alert)
                    database.log_alert("breaking_news", "MULTI", alert, iso_now())
                    new_alerts_sent = True
                    print(f"Breaking news alert sent: {title[:60]}")

                if new_alerts_sent or len(sent_ids) % 50 == 0:
                    save_news_state(sent_ids)

        except Exception as exc:
            print(f"News Checker Error: {exc}")

        await asyncio.sleep(300)  # Check every 5 minutes


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
                            database.add_channel(str(chat_id), chat_name)
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
                            database.add_user(chat_id, username, full_name)
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
        await asyncio.gather(
            telegram_long_polling(session),
            binance_liquidation_listener(session),
            rsi_scanner_loop(session),
            daily_digest_loop(session),
            news_checker_loop(session),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
