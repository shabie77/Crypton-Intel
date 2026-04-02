import asyncio
import os
import json
import websockets
import aiohttp
from datetime import datetime, timedelta
from dotenv import load_dotenv
import database  # Our custom SQLite SQLite Registration System

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set. Bot shutting down.")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Initialize the SQL Database Phase 5
database.init_db()

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
if not DATA_DIR:
    raise ValueError("CRITICAL ERROR: RAILWAY_VOLUME_MOUNT_PATH environment variable is not set. A mounted volume is required to persist state.")
RSI_STATE_FILE = os.getenv("RSI_STATE_FILE", os.path.join(DATA_DIR, "rsi_state.json"))
LAST_DIGEST_FILE = os.getenv("LAST_DIGEST_FILE", os.path.join(DATA_DIR, "last_digest.txt"))

def load_rsi_state():
    if os.path.exists(RSI_STATE_FILE):
        try:
            with open(RSI_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading RSI state from {RSI_STATE_FILE}: {e}")
    return {}

def save_rsi_state(state):
    try:
        with open(RSI_STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Error saving RSI state to {RSI_STATE_FILE}: {e}")

rsi_state_tracker = load_rsi_state()

def has_sent_digest_today(target_date_str):
    if os.path.exists(LAST_DIGEST_FILE):
        try:
            with open(LAST_DIGEST_FILE, 'r') as f:
                if f.read().strip() == target_date_str:
                    return True
        except Exception as e:
            print(f"Error reading last digest file {LAST_DIGEST_FILE}: {e}")
    return False

def mark_digest_sent(target_date_str):
    try:
        with open(LAST_DIGEST_FILE, 'w') as f:
            f.write(target_date_str)
    except Exception as e:
        print(f"Error writing to last digest file {LAST_DIGEST_FILE}: {e}")

async def fetch_with_backoff(session, url, params=None, retries=3):
    """ Helper to retry API calls with exponential backoff on failure """
    for attempt in range(retries):
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            print(f"API fetch error on attempt {attempt+1}: {e}")
        if attempt < retries - 1:
            await asyncio.sleep(2 ** attempt)
    return None

async def send_telegram_message(session, chat_id, text):
    """ Sends a formatted HTML message to a specific Telegram Chat ID """
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(3):
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return
        except Exception:
            await asyncio.sleep(1)
    print(f"Failed to send message to {chat_id}")

async def get_market_context(session, symbol):
    """
    Fetches the last 50 4-Hour candles for whatever coin got liquidated
    to determine the macro trend via the 50-Simple Moving Average.
    """
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
    """ Assigns a baseline score to the event and multiplies it by the Context. """
    base_score = 0
    insight = ""
    if side == "BUY": # Short Liquidation (Bullish for Price)
        base_score = 5 + (total_value / 500000)
    elif side == "SELL": # Long Liquidation (Bearish for Price)
        base_score = -5 - (total_value / 500000)
    
    # Phase 4 Dynamic Weighting
    if trend == "BULLISH 🟢" and side == "BUY":
        final_score = base_score * 1.5
        insight = "Short liquidation cascade propelling the Bull trend higher."
    elif trend == "BEARISH 🔴" and side == "BUY":
        final_score = base_score * 0.5
        insight = "Weak short squeeze against a strong Bear trend. Careful of a bounce!"
    elif trend == "BEARISH 🔴" and side == "SELL":
        final_score = base_score * 1.5
        insight = "Longs capitulating. Bearish momentum accelerating downward."
    elif trend == "BULLISH 🟢" and side == "SELL":
        final_score = base_score * 0.5
        insight = "Minor long flush-out. Macro Bullish structure remains intact."
    else:
        final_score = base_score
        insight = "Choppy market conditions. Signal strength neutral."

    abs_score = min(abs(final_score), 20)
    confidence = int((abs_score / 20) * 100)
    return final_score, confidence, insight

def calculate_rsi(prices, period=14):
    """ Mathematically calculates the Relative Strength Index (RSI) using pure Python. """
    if len(prices) < period + 1:
        return 50.0  # Safe default if not enough data
    
    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
            
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(prices)-1):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

async def rsi_scanner_loop(session):
    """
    Scans 4H, 1D, 1W charts for overbought (>70) and oversold (<30) conditions.
    State is persisted to disk to survive restarts.
    """
    print("📡 Multi-Timeframe RSI Scanner is Live...")
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    intervals = {
        "4h": ("Moderate", "🟡"),
        "1d": ("High", "🟠"),
        "1w": ("Very High", "🔴")
    }
    
    while True:
        try:
            for symbol in symbols:
                if symbol not in rsi_state_tracker:
                    rsi_state_tracker[symbol] = {}
                    
                for interval, (impact, icon) in intervals.items():
                    if interval not in rsi_state_tracker[symbol]:
                        rsi_state_tracker[symbol][interval] = "NEUTRAL"
                        
                    url = "https://api.binance.com/api/v3/klines"
                    params = {"symbol": symbol, "interval": interval, "limit": 50}
                    candles = await fetch_with_backoff(session, url, params)
                    
                    if candles:
                        close_prices = [float(candle[4]) for candle in candles]
                        current_price = close_prices[-1]
                        rsi = calculate_rsi(close_prices)
                        
                        current_state = "NEUTRAL"
                        if rsi >= 70: current_state = "OVERBOUGHT"
                        elif rsi <= 30: current_state = "OVERSOLD"
                        
                        previous_state = rsi_state_tracker[symbol][interval]
                        
                        # Trigger Alert ONLY when entering a new extreme state
                        if current_state != "NEUTRAL" and current_state != previous_state:
                            active_channels = database.get_active_channels()
                            condition = "🔥 OVERBOUGHT (High Risk of Reversal Down)" if current_state == "OVERBOUGHT" else "🥶 OVERSOLD (High Potential for Reversal Up)"
                            
                            alert = (
                                f"<b>{icon} RSI EXTREME ALERT - {impact} Impact</b>\n\n"
                                f"<b>Asset:</b> #{symbol[:-4]}\n"
                                f"<b>Timeframe:</b> {interval.upper()}\n"
                                f"<b>Current Price:</b> ${current_price:,.2f}\n"
                                f"<b>RSI Level:</b> {rsi:.2f}\n\n"
                                f"<b>Condition:</b> <i>{condition}</i>"
                            )
                            for chat_id in active_channels:
                                await send_telegram_message(session, chat_id, alert)
                                
                            print(f"RSI Alert sent: {symbol} {interval} {current_state}")
                            
                        # Update state safely
                        if current_state != previous_state:
                            rsi_state_tracker[symbol][interval] = current_state
                            save_rsi_state(rsi_state_tracker)
            
            # Wait 10 minutes before checking RSI again so we don't hit Binance API limits
            await asyncio.sleep(600)
        except Exception as e:
            print(f"RSI Scanner Error: {e}")
            await asyncio.sleep(60)

async def daily_digest_loop(session):
    """
    Daily Market Digest at 9:30 AM IST (04:00 UTC) with Sentiment.
    Includes persistence to avoid duplicate sends after restarts.
    """
    print("📈 Daily Digest Engine is Live...")
    while True:
        try:
            now = datetime.utcnow()
            target = now.replace(hour=4, minute=0, second=0, microsecond=0)
            target_date_str = target.strftime('%Y-%m-%d')
            
            # If we are past 04:00 AM UTC
            if now >= target:
                if not has_sent_digest_today(target_date_str):
                    # We fell asleep across the boundary or restarted, proceed to send immediately!
                    pass
                else:
                    # We already sent today's digest, target tomorrow!
                    target += timedelta(days=1)
                    target_date_str = target.strftime('%Y-%m-%d')
                    seconds_until_target = (target - now).total_seconds()
                    await asyncio.sleep(seconds_until_target)
            else:
                # We haven't reached 04:00 AM UTC yet
                seconds_until_target = (target - now).total_seconds()
                await asyncio.sleep(seconds_until_target)
            
            # Double check to prevent race condition
            if has_sent_digest_today(target_date_str):
                continue
            
            # Time to send the digest!
            digest = "<b>📰 DAILY MARKET DIGEST</b>\n\n"
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            
            for symbol in symbols:
                url = "https://api.binance.com/api/v3/klines"
                params = {"symbol": symbol, "interval": "1d", "limit": 50}
                candles = await fetch_with_backoff(session, url, params)
                
                if candles:
                    close_prices = [float(candle[4]) for candle in candles]
                    current_price = close_prices[-1]
                    sma_50 = sum(close_prices) / len(close_prices)
                    rsi = calculate_rsi(close_prices)
                    
                    trend = "BULLISH 🟢" if current_price > sma_50 else "BEARISH 🔴"
                    rsi_emoji = "🔥" if rsi >= 70 else "🥶" if rsi <= 30 else "⚪"
                    
                    digest += f"<b>#{symbol[:-4]}</b>: ${current_price:,.2f}\n"
                    digest += f"- <b>Trend:</b> {trend} (vs 50-SMA)\n"
                    digest += f"- <b>Daily RSI:</b> {rsi:.1f} {rsi_emoji}\n\n"
            
            # Fetch global sentiment
            try:
                fg_url = "https://api.alternative.me/fng/"
                fg_data = await fetch_with_backoff(session, fg_url, retries=2)
                if fg_data:
                    sentiment = fg_data['data'][0]['value_classification']
                    val = fg_data['data'][0]['value']
                    digest += f"<b>Global Market Sentiment:</b> {sentiment} ({val}/100)"
            except Exception as e:
                pass
            
            active_channels = database.get_active_channels()
            for chat_id in active_channels:
                await send_telegram_message(session, chat_id, digest)
                
            print(f"Daily Digest sent to {len(active_channels)} channels!")
            
            # Mark digest as sent for this scheduled target date
            mark_digest_sent(target_date_str)
            
            # Small sleep buffer to stabilize loop mechanics
            await asyncio.sleep(60)
            
        except Exception as e:
            print(f"Daily Digest Error: {e}")
            await asyncio.sleep(3600)

async def binance_liquidation_listener(session):
    """ 
    We changed the URL back to tracking strictly the Top 3 Crypto Assets: BTC, ETH, SOL.
    This guarantees high quality alerts, ignoring tiny random worthless altcoins.
    """
    url = "wss://fstream.binance.com/stream?streams=btcusdt@forceOrder/ethusdt@forceOrder/solusdt@forceOrder"
    print("📡 Connecting to Binance WebSocket for Premium Top 3 Coins (BTC, ETH, SOL)...")
    
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as websocket:
                print("✅ Connected to Premium Binance WebSocket successfully!")
                while True:
                    response = await websocket.recv()
                    payload = json.loads(response)
                    
                    data = payload.get('data', {})
                    if not data: continue
                    
                    order_data = data.get('o', {})
                    if not order_data: continue
                    
                    symbol = order_data['s']
                    side = order_data['S']
                    price = float(order_data['p'])
                    qty = float(order_data['q'])
                    total_value = price * qty
                    
                    if total_value > 50000:
                        position_type = "SHORT 🔴" if side == "BUY" else "LONG 🟢"
                        print(f"Liquidation detected: ${total_value:,.2f} on {symbol}")
                        
                        trend, sma = await get_market_context(session, symbol)
                        score, confidence, insight = await calculate_confluence_score(total_value, side, trend)
                        
                        if confidence > 40:
                            alert_message = (
                                f"🔥 <b>SIGNAL CONFIDENCE: {confidence}%</b> 🔥\n\n"
                                f"<b>📍 Macro Context:</b>\n"
                                f"- <b>Trend:</b> {trend} (Price vs. 50-SMA)\n\n"
                                f"<b>⚡ Trigger:</b>\n"
                                f"- <b>{position_type} LIQUIDATION</b>\n"
                                f"- <b>Asset:</b> #{symbol}\n"
                                f"- <b>Value:</b> ${total_value:,.2f}\n"
                                f"- <b>Price:</b> ${price:,.2f}\n\n"
                                f"<b>💡 Insight:</b> <i>{insight}</i>"
                            )
                            
                            active_channels = database.get_active_channels()
                            for chat_id in active_channels:
                                await send_telegram_message(session, chat_id, alert_message)
                                
                            print(f"Broadcasted Signal ({confidence}%) to {len(active_channels)} active channels!")
                        else:
                            print(f"Filtered out {symbol} Signal: {confidence}% (Too noisy)")
                            
        except Exception as e:
            print(f"❌ WebSocket connection dropped. Reconnecting in 5s... ({e})")
            await asyncio.sleep(5)

async def telegram_long_polling(session):
    """ 
    PHASE 5: Auto-Registration & Admin Kill-Switch Engine.
    Listens for you adding the bot to new channels or sending Admin commands.
    """
    offset = None
    print("🤖 Telegram Polling Engine & Auto-Database is Live...")
    
    while True:
        url = f"{TELEGRAM_API_URL}/getUpdates"
        params = {
            "timeout": 30, 
            "allowed_updates": ["message", "my_chat_member", "channel_post"]
        }
        if offset: params["offset"] = offset

        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status == 200:
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
                        if message and "text" in message:
                            text = message["text"]
                            from_id = str(message["chat"]["id"])
                            
                            if text.startswith("/revoke ") and from_id == "1120431469":
                                target_customer = text.split("/revoke ")[1].strip()
                                actual_id = database.get_channel_id_by_name(target_customer)
                                success = database.ban_channel(target_customer)
                                
                                if success:
                                    await send_telegram_message(session, from_id, f"🚫 Access Revoked for customer: {target_customer}")
                                    if actual_id and str(actual_id) != "1120431469":
                                        leave_url = f"{TELEGRAM_API_URL}/leaveChat"
                                        await session.post(leave_url, json={"chat_id": str(actual_id)})
                                        print(f"Bot physically left channel: {actual_id}")
                                else:
                                    await send_telegram_message(session, from_id, f"⚠️ Error: Could not find '{target_customer}'.")
                                
                            elif text.startswith("/renew ") and from_id == "1120431469":
                                target_customer = text.split("/renew ")[1].strip()
                                success = database.renew_channel(target_customer)
                                if success:
                                    await send_telegram_message(session, from_id, f"✅ Access RENEWED for customer: {target_customer}")
                                else:
                                    await send_telegram_message(session, from_id, f"⚠️ Error: Could not find '{target_customer}'.")
                                
                            elif text.startswith("/start"):
                                greeting = "🤖 Welcome to the Premium Crypto Signals Engine!"
                                await send_telegram_message(session, message["chat"]["id"], greeting)
                                
        except Exception as e:
            print(f"Error in telegram_long_polling: {e}")
        await asyncio.sleep(0.5)

async def main():
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    connector = aiohttp.TCPConnector(keepalive_timeout=60)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.gather(
            telegram_long_polling(session),
            binance_liquidation_listener(session),
            rsi_scanner_loop(session),
            daily_digest_loop(session)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
