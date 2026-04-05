import asyncio
import html
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import websockets
from dotenv import load_dotenv
import google.generativeai as genai

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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize Gemini if key exists
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("🤖 Gemini AI Intelligence Layer initialized.")
    except Exception as e:
        print(f"❌ Gemini Initialization failed: {e}")
else:
    print("⚠️ Gemini API Key missing. Bot will use manual market summary logic.")

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
    """ Converts 'HH:MM UTC' string to 'HH:MM IST' string. """
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
        if h_display == 0: h_display = 12
        return f"{h_display:02d}:{m:02d} {period} IST"
    except:
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
                if response.status == 403:
                    print(f"Telegram send failed (403) for {chat_id} - Revoking access.")
                    database.revoke_access(str(chat_id))
                    return False
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
    """Returns a dictionary with level, emoji, and a brief impact explanation."""
    title_lower = title.lower()
    critical_kws = (
        "hack", "exploit", "stolen", "ban", "banned", "arrest", "sec sues", 
        "sec charges", "etf approved", "etf rejected", "collapse", "insolvent", 
        "emergency", "halted", "lawsuit", "bankruptcy",
    )
    moderate_kws = (
        "binance", "blackrock", "coinbase", "etf", "regulation", "fed rate", 
        "interest rate", "inflation", "cpi", "powell", "grayscale", "whale", 
        "ath", "crash", "dump", "pump", "bullish", "bearish", "market share",
    )
    
    if any(kw in title_lower for kw in critical_kws):
        return {
            "level": "CRITICAL",
            "emoji": "🔴",
            "impact": "Major regulatory development, security breach, or systemic shift."
        }
    if any(kw in title_lower for kw in moderate_kws):
        return {
            "level": "MODERATE",
            "emoji": "🟠",
            "impact": "Significant market shift, institutional activity, or macro update."
        }
    return {
        "level": "LOW",
        "emoji": "🟡",
        "impact": "General ecosystem update or standard market movement."
    }


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


async def fetch_symbol_snapshot(session, symbol, config):
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
    # Binary: volumes[-1] is UNFINISHED TODAY. volumes[-2] is COMPLETED YESTERDAY.
    current_volume = volumes[-2] 
    ma_period = 20
    recent_volumes = volumes[-ma_period-1:-1] # The 20 bars BEFORE today
    average_volume = sum(recent_volumes) / max(len(recent_volumes), 1)
    
    # Calculate: (Yesterday's volume / 20-day MA) * 100
    volume_ratio = current_volume / average_volume if average_volume else 1.0
    volume_percent = volume_ratio * 100
    volume_delta = (volume_ratio - 1) * 100
    
    change_24h = ((current_price - previous_price) / previous_price * 100) if previous_price else 0.0
    trend = "BULLISH 🟢" if current_price > sma_50 else "BEARISH 🔴"
    
    # Load thresholds from config
    vol_cfg = config.get("volume_settings", DEFAULT_CONFIG["volume_settings"])
    if volume_percent >= vol_cfg["high"]:
        volume_status = "HIGH 🟢"
    elif volume_percent >= vol_cfg["normal"]:
        volume_status = "NORMAL ⚪"
    elif volume_percent >= vol_cfg["low"]:
        volume_status = "LOW 🔴"
    else:
        volume_status = "VERY LOW 🔴"

    return {
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
    """ Manual fallback market summary. """
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


async def get_ai_market_summary(session, snapshots, pulse):
    """ Generates a professional AI market summary using Gemini. """
    if not GEMINI_API_KEY:
        return summarize_market_logic(snapshots, pulse)
        
    try:
        # Prepare context for AI
        coin_info = []
        for s in snapshots:
            if s:
                coin_info.append(f"#{s['tag']}: ${s['price']:,.2f} ({s['change_24h']:+.2f}% 24h, Trend: {s['trend']}, RSI: {s['rsi']:.1f})")
        
        context = "\n".join(coin_info)
        prompt = (
            f"Act as a professional Crypto Market Analyst. Summarize the following 24h market data into 2-3 precise, "
            f"insightful sentences for a high-level trading digest. Focus on trend confluence and momentum.\n\n"
            f"Market Pulse Score: {pulse['score']}/100 ({pulse['label']})\n"
            f"24h Snapshots:\n{context}\n\n"
            f"Output only the summary text."
        )
        
        # Upgrade to gemini-1.5-flash (more reliable for free tiers)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = await asyncio.to_thread(model.generate_content, prompt)
        
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        err_str = str(e).lower()
        status_label = "(API Error)"
        if "api_key_invalid" in err_str or "400" in err_str:
            status_label = "(Invalid Key ❌)"
            
        print(f"🤖 Gemini AI Synthesis failed: {e}. Falling back to manual logic.")
        # Private Alert for Admin
        alert_msg = (
            f"⚠️ <b>GEMINI AI FAILURE</b>\n\n"
            f"<b>Error:</b> {html_safe(str(e))}\n"
            f"<b>Status:</b> {status_label}\n\n"
            f"<i>The daily digest is falling back to manual logic. Please check your API key on Railway.</i>"
        )
        asyncio.create_task(send_telegram_message(session, ADMIN_USER_ID, alert_msg))
        
    return summarize_market_logic(snapshots, pulse)


async def build_digest_message(session, snapshots, pulse, config, top_news=None, upcoming_macro=None, liq_stats=None, macro_partial_failure=False):
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
            if (utc_now().timestamp() - last_ts) < 86400: # 24 hours
                show_powell = True
        except:
            pass
            
    if show_powell:
        lines.append(f"<b>Why it matters:</b> {html_safe(config['powell']['stance'].title())} Fed tone and current trend breadth are shaping risk appetite.")
    else:
        lines.append(f"<b>Review:</b> Neutral Fed baseline. Market trend breadth and macro inflows are shaping risk appetite.")
        
    lines.append(f"<b>Watch next:</b> {html_safe(pulse['watch_next'])}")
    lines.append("")
    
    # 24h Liquidation Section
    if liq_stats:
        total = liq_stats["total"]
        if total > 0:
            long_p = (liq_stats['longs'] / total) * 100
            short_p = (liq_stats['shorts'] / total) * 100
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
        lines.append(f"<b>#{html_safe(snapshot['tag'])}</b>: ${snapshot['price']:,.2f} ({emoji} {snapshot['change_24h']:+.2f}% 24h)")
        lines.append(f"- Trend: {snapshot['trend']} vs 50-day average")
        lines.append(f"- Daily RSI: {snapshot['rsi']:.1f}")
        lines.append(f"- Volume: {html_safe(snapshot['volume_status'])} ({snapshot['volume_delta']:+.1f}% vs 20-day avg)")
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
            
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_data)
            channel = root.find('channel')
            if channel is None:
                return []
                
            cutoff = utc_now().timestamp() - 86400  # 24 hours ago
            critical_high = []
            general = []
            
            for item in channel.findall('item')[:40]:
                pubDate = item.find('pubDate')
                if pubDate is None:
                    continue
                
                try:
                    dt = parsedate_to_datetime(pubDate.text)
                    published_on = dt.timestamp()
                except:
                    continue
                    
                if published_on < cutoff:
                    continue
                
                title = item.find('title')
                title_text = title.text if title is not None else ""
                
                importance_data = classify_news_importance(title_text)
                news_item = {
                    "title": title_text,
                    "source": "CoinTelegraph",
                    "level": importance_data["level"],
                    "emoji": importance_data["emoji"],
                    "impact": importance_data["impact"]
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
                critical_high + general[:3], 
                key=lambda x: SEVERITY_RANK.get(x["level"], 0), 
                reverse=True
            )
            return final_news[:3] # Strictly Top 3 news items
    except Exception as exc:
        print(f"fetch_top_news error: {exc}")
        return []


async def _fetch_investing_macro_events(session, timeframe_tab):
    """
    Fetches high-impact macro events from Investing.com via internal AJAX API.
    Bypasses the broken ForexFactory Next Week feed.
    """
    try:
        from bs4 import BeautifulSoup
        import json
    except ImportError:
        print("   - ❌ Error: BeautifulSoup4 not installed. Cannot fetch Investing.com data.")
        return False, []

    url = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    # US(5), EU(72), UK(4), Ger(17), Jpn(35), Aus(25), Can(6), Swz(12), NZ(43)
    payload = f"country[]=5&country[]=72&country[]=4&country[]=17&country[]=35&country[]=25&country[]=6&country[]=12&country[]=43&timeZone=8&timeFilter=timeOnly&currentTab={timeframe_tab}&submitFilters=1&limit_from=0&importance=3"
    
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
                    if not datetime_str: continue
                    
                    title_elem = tr.find("td", class_="event")
                    title = title_elem.text.strip() if title_elem else "Unknown"
                    title = title.replace("  ", " ").strip()
                    
                    country_td = tr.find("td", class_="flagCur")
                    country = country_td.text.replace('\xa0', '').strip() if country_td else ""
                    if len(country) > 3: country = country[-3:] # Extract currency ISO

                    sentiment_td = tr.find("td", class_="sentiment")
                    if sentiment_td and sentiment_td.get("data-img_key") == "bull3":
                        dt = datetime.strptime(datetime_str, "%Y/%m/%d %H:%M:%S")
                        event_ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
                        
                        events.append({
                            "title": title,
                            "country": country,
                            "date": dt.strftime("%m-%d"),
                            "time": dt.strftime("%H:%M UTC"),
                            "timestamp": event_ts
                        })
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


    import xml.etree.ElementTree as ET
    
    total_successful_feeds = 0
    total_attempted_feeds = len(feed_targets)
    target_status = {}

    for target in feed_targets:
        feed_success = False
        target_event_count = 0
        for url in (target["primaries"] + target.get("fallbacks", [])):
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
                    
                    for item in root.findall('event'):
                        impact_node = item.find('impact')
                        if impact_node is None or impact_node.text.strip() != 'High':
                            continue
                        
                        title_node = item.find('title')
                        country_node = item.find('country')
                        date_node = item.find('date')
                        time_node = item.find('time')
                        
                        if any(n is None or n.text is None for n in (title_node, country_node, date_node, time_node)):
                            continue
                        
                        dt_str = f"{date_node.text.strip()} {time_node.text.strip()}"
                        try:
                            event_dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
                            event_ts = event_dt.timestamp()
                        except:
                            continue # Skip malformed dates
                        
                        unique_id = f"{title_node.text.strip()}_{event_ts}"
                        
                        if now_ts <= event_ts <= limit_ts and unique_id not in seen_titles:
                            all_events.append({
                                "title": title_node.text.strip(),
                                "country": country_node.text.strip(),
                                "date": event_dt.strftime("%m-%d"),
                                "time": event_dt.strftime("%H:%M UTC"),
                                "timestamp": event_ts
                            })
                            seen_titles.add(unique_id)
                            events_in_this_feed += 1
                            target_event_count += 1

                    if events_in_this_feed > 0:
                        print(f"   - ✅ Success: Extracted {events_in_this_feed} unique High-Impact events.")
                    else:
                        print(f"   - 💡 Note: No future High-Impact events found (Weekend Rollover Mode).")
                    
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
                if now_ts <= ev['timestamp'] <= limit_ts and unique_id not in seen_titles:
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
        "events": sorted(all_events, key=lambda x: x['timestamp']),
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
        snapshots.append(await fetch_symbol_snapshot(session, symbol, config))

    top_news = await fetch_top_news(session)
    # Fetch 24h Macro Events
    macro_data = await fetch_macro_events(session)
    # Always use events if they exist, regardless of overall success flag
    upcoming_macro = macro_data["events"] if macro_data.get("events") else []
    
    # Fetch 24h Liquidation Stats
    cutoff_iso = (utc_now() - timedelta(hours=24)).isoformat()
    liq_stats = database.get_24h_liquidation_stats(cutoff_iso)
    
    pulse = calculate_market_pulse(snapshots, config)
    digest = await build_digest_message(
        session, snapshots, pulse, config, 
        top_news=top_news, 
        upcoming_macro=upcoming_macro, 
        liq_stats=liq_stats,
        macro_partial_failure=macro_data.get("partial_failure", False) and not upcoming_macro
    )
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
                        database.log_liquidation(symbol, side, total_value, iso_now())

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
                                    database.log_alert("liquidation", symbol, alert_message, iso_now())

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
        {"command": "digest", "description": "Get latest market digest"},
        {"command": "feedback", "description": "Send suggestion/feedback"}
    ]
    
    # Admin-only commands (Scoped to ADMIN_USER_ID)
    admin_only = common_commands + [
        {"command": "status", "description": "Show bot health"},
        {"command": "macro", "description": "Audit upcoming macro events"},
        {"command": "channels", "description": "List active channels"},
        {"command": "users", "description": "List active DM users"}
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

    if command == "/macro":
        result = await fetch_weekly_macro_events(session)
        events = result.get("events", [])
        this_week_ok = result.get("this_week_ok", False)
        next_week_ok = result.get("next_week_ok", False)
        partial_failure = result.get("partial_failure", False)

        # Case 1: Both sources failed AND no events at all
        if not this_week_ok and not next_week_ok and not events:
            await send_telegram_message(session, chat_id, "⚠️ Macro calendar feed unavailable right now. Please try again later.")
            return

        # Case 2: Both sources succeeded but truly no events
        if this_week_ok and next_week_ok and not events:
            await send_telegram_message(session, chat_id, "📅 No High Impact macro events found for the next 7 days.")
            return

        # Case 3: No events but partial failure (one source down)
        if not events and partial_failure:
            source_down = "Current-week" if not this_week_ok else "Next-week"
            await send_telegram_message(session, chat_id, f"⚠️ {source_down} macro source unavailable right now. No events from the working source either. Try again later.")
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
        
        await send_telegram_message(session, chat_id, f"Audit report:\n" + "\n".join(lines))
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
                    database.log_alert("breaking_news", "MULTI", alert, iso_now())
                    new_alerts_sent = True
                    print(f"Breaking news alert sent: {title[:60]}")

                if new_alerts_sent or len(sent_ids) % 50 == 0:
                    save_news_state(sent_ids)

        except Exception as exc:
            print(f"News Checker Error: {exc}")

        await asyncio.sleep(300)  # Check every 5 minutes


async def macro_calendar_loop(session):
    """
    Checks economic calendar for high-impact events and sends an early warning alert
    approx 2 hours before they occur.
    """
    print("🏛️ Macro Calendar Watcher is Live...")
    sent_events = load_macro_state()

    while True:
        try:
            result = await fetch_macro_events(session)
            if not result["success"]:
                # Silent skip to avoid log spam, fetcher already prints errors
                await asyncio.sleep(3600) # Wait longer if feed is down
                continue

            events = result["events"]
            now_ts = utc_now().timestamp()
            
            for event in events:
                event_id = f"{event['timestamp']}_{event['title']}"
                if event_id in sent_events:
                    continue
                
                # Check if event is approx 2 hours away (7200 seconds)
                time_until = event['timestamp'] - now_ts
                if 0 < time_until <= 7200:
                    # Convert UTC to IST for the alert
                    ist_time = utc_to_ist(event["time"])
                    
                    alert = (
                        f"🏛️ <b>MACRO VOLATILITY WARNING</b>\n\n"
                        f"<b>Event:</b> {html_safe(event['title'])}\n"
                        f"<b>Country:</b> {event['country']}\n"
                        f"<b>Time:</b> {ist_time}\n\n"
                        f"<b>Why it matters:</b> This is a High Impact event. Expect significant volatility in BTC and ETH pairs around this time.\n"
                        f"<b>Watch next:</b> Ensure your trade positions are secured before the data drops."
                    )
                    
                    await broadcast_message(session, alert)
                    database.log_alert("macro_warning", "GLOBAL", alert, iso_now())
                    sent_events.append(event_id)
                    save_macro_state(sent_events)
                    print(f"Macro warning sent: {event['title']}")
                    
        except Exception as exc:
            import traceback
            print(f"Macro Calendar Error: {exc}")
            print(traceback.format_exc())

        await asyncio.sleep(300)  # Check every 5 minutes for precise 2-hour alerts


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

                    if command == "/feedback":
                        if not args:
                            await send_telegram_message(session, chat_id, "Usage: /feedback <your suggestion>")
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
                        await send_telegram_message(session, chat_id, "✅ Thank you! Your feedback has been sent to the developers.")
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
        
        await asyncio.gather(
            telegram_long_polling(session),
            binance_liquidation_listener(session),
            rsi_scanner_loop(session),
            daily_digest_loop(session),
            news_checker_loop(session),
            macro_calendar_loop(session),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
