import asyncio
import os
import json
import websockets
import aiohttp
from dotenv import load_dotenv
import database  # Our custom SQLite SQLite Registration System

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Initialize the SQL Database Phase 5
database.init_db()

async def send_telegram_message(session, chat_id, text):
    """ Sends a formatted HTML message to a specific Telegram Chat ID """
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with session.post(url, json=payload) as response:
            if response.status != 200:
                print(f"Failed to send message to {chat_id}")
    except Exception as e:
        print(f"Error sending message: {e}")

async def get_market_context(session, symbol):
    """
    Fetches the last 50 4-Hour candles for whatever coin got liquidated
    to determine the macro trend via the 50-Simple Moving Average.
    """
    url = "https://api.binance.com/api/v3/klines"
    # Convert 'BTCUSDT' (from webSockets) to 'BTCUSDT' for REST
    params = {"symbol": symbol.upper(), "interval": "4h", "limit": 50}
    try:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                candles = await response.json()
                close_prices = [float(candle[4]) for candle in candles]
                current_price = close_prices[-1]
                sma_50 = sum(close_prices) / len(close_prices)
                
                trend = "BULLISH 🟢" if current_price > sma_50 else "BEARISH 🔴"
                return trend, sma_50
    except Exception:
        pass
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

async def binance_liquidation_listener(session):
    """ 
    We changed the URL back to tracking strictly the Top 3 Crypto Assets: BTC, ETH, SOL.
    This guarantees high quality alerts, ignoring tiny random worthless altcoins.
    """
    # The 'stream' endpoint allows multiple coins at once!
    url = "wss://fstream.binance.com/stream?streams=btcusdt@forceOrder/ethusdt@forceOrder/solusdt@forceOrder"
    print("📡 Connecting to Binance WebSocket for Premium Top 3 Coins (BTC, ETH, SOL)...")
    
    while True:
        try:
            async with websockets.connect(url) as websocket:
                print("✅ Connected to Premium Binance WebSocket successfully!")
                while True:
                    response = await websocket.recv()
                    payload = json.loads(response)
                    
                    # Because we use 'stream', the order data is nested inside 'data'
                    data = payload.get('data', {})
                    if not data: continue
                    
                    order_data = data.get('o', {})
                    if not order_data: continue
                    
                    symbol = order_data['s']
                    side = order_data['S']
                    price = float(order_data['p'])
                    qty = float(order_data['q'])
                    total_value = price * qty
                    
                    # Back to reality! Only Massive $50k+ liquidations.
                    if total_value > 50000:
                        position_type = "SHORT 🔴" if side == "BUY" else "LONG 🟢"
                        print(f"Liquidation detected: ${total_value:,.2f} on {symbol}")
                        
                        trend, sma = await get_market_context(session, symbol)
                        score, confidence, insight = await calculate_confluence_score(total_value, side, trend)
                        
                        # Restored the noise filter: Confidence must be > 40%
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
                            
                            # --------- THE MULTI-CHANNEL BROADCAST LOOP --------- #
                            active_channels = database.get_active_channels()
                            
                            for chat_id in active_channels:
                                # Send the alert simultaneously to every customer perfectly formatted
                                await send_telegram_message(session, chat_id, alert_message)
                                
                            print(f"Broadcasted Signal ({confidence}%) to {len(active_channels)} active channels!")
                        else:
                            print(f"Filtered out {symbol} Signal: {confidence}% (Too noisy)")
                            
        except Exception as e:
            print(f"❌ WebSocket connection dropped. Reconnecting...")
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
        # We explicitly tell Telegram we care when the bot is added to or removed from a Channel ('my_chat_member')
        params = {
            "timeout": 30, 
            "allowed_updates": ["message", "my_chat_member", "channel_post"]
        }
        if offset: params["offset"] = offset

        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    updates = await response.json()
                    for result in updates.get("result", []):
                        offset = result["update_id"] + 1
                        
                        # =========================================================
                        # FEATURE 1: AUTO-REGISTER NEW CHANNELS TO DATABASE!
                        # =========================================================
                        if "my_chat_member" in result:
                            member_event = result["my_chat_member"]
                            new_status = member_event["new_chat_member"]["status"]
                            chat_info = member_event["chat"]
                            chat_id = chat_info["id"]
                            chat_name = chat_info.get("title", f"Private User {chat_id}")
                            
                            # If someone adds the bot as an Administrator...
                            if new_status in ["administrator", "member"]:
                                database.add_channel(str(chat_id), chat_name)
                                print(f"Registered new Channel: {chat_name} to SQLite.")
                        
                        # =========================================================
                        # FEATURE 2: ADMIN KILL-SWITCH & COMMANDS
                        # =========================================================     
                        message = result.get("message")
                        if message and "text" in message:
                            text = message["text"]
                            from_id = str(message["chat"]["id"])
                            
                            # Kill-Switch: Only YOU (Admin ID: 1120431469) can revoke access!
                            if text.startswith("/revoke ") and from_id == "1120431469":
                                target_customer = text.split("/revoke ")[1].strip()
                                
                                # Get the exact ID *before* we revoke it
                                actual_id = database.get_channel_id_by_name(target_customer)
                                
                                success = database.ban_channel(target_customer)
                                if success:
                                    await send_telegram_message(session, from_id, f"🚫 Access Revoked for customer: {target_customer}")
                                    
                                    # PHYSICAL KILL SWITCH: Kick the bot out of the channel entirely!
                                    if actual_id and str(actual_id) != "1120431469":
                                        leave_url = f"{TELEGRAM_API_URL}/leaveChat"
                                        await session.post(leave_url, json={"chat_id": str(actual_id)})
                                        print(f"Bot physically left channel: {actual_id}")
                                else:
                                    await send_telegram_message(session, from_id, f"⚠️ Error: Could not find '{target_customer}'. Did you misspell it? Try using their exact numerical ID.")
                                
                            # Renewal Switch: Restore access if they pay again!
                            elif text.startswith("/renew ") and from_id == "1120431469":
                                target_customer = text.split("/renew ")[1].strip()
                                success = database.renew_channel(target_customer)
                                if success:
                                    await send_telegram_message(session, from_id, f"✅ Access RENEWED for customer: {target_customer}")
                                else:
                                    await send_telegram_message(session, from_id, f"⚠️ Error: Could not find '{target_customer}'.")
                                
                            elif text.startswith("/start"):
                                greeting = "🤖 Welcome to the Premium Crypto Signals Engine! The database has secured your Chat ID."
                                await send_telegram_message(session, message["chat"]["id"], greeting)
                                
        except Exception:
            pass
        await asyncio.sleep(0.5)

async def main():
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            telegram_long_polling(session),
            binance_liquidation_listener(session)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
