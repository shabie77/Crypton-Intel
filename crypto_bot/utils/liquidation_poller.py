"""
utils/liquidation_poller.py

Real-time Binance Futures liquidation stream listener.
Uses Binance public WebSocket — NO API key required.
Writes to existing liquidations table via database.log_liquidation().
"""
import asyncio
import json
from datetime import datetime, timezone
import aiohttp
import database

STREAM_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"

# Only track coins relevant to our digest
TRACKED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"}


async def _listen(session: aiohttp.ClientSession):
    """Inner WebSocket listener. Reconnects on disconnect."""
    async with session.ws_connect(STREAM_URL, heartbeat=30) as ws:
        print("✅ Binance liquidation stream connected.")
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    order = data.get("o", {})
                    symbol = order.get("s", "")

                    if symbol not in TRACKED_SYMBOLS:
                        continue

                    # SELL = long liquidated, BUY = short liquidated
                    side = order.get("S", "")
                    qty = float(order.get("z", 0))
                    price = float(order.get("ap", 0))
                    value = qty * price

                    # Ignore dust liquidations under $500
                    if value < 500:
                        continue

                    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    await asyncio.to_thread(database.log_liquidation, symbol, side, value, timestamp)

                except Exception as e:
                    print(f"⚠️ Liquidation parse error: {e}")

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break


async def start_liquidation_poller():
    """
    Background task. Connects to Binance public liquidation stream.
    Auto-reconnects on failure with 10s backoff.
    """
    print("💧 Liquidation Poller is Live...")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                await _listen(session)
        except Exception as e:
            print(f"⚠️ Liquidation stream error: {e}. Reconnecting in 10s...")
        await asyncio.sleep(10)
