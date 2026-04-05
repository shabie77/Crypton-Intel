import asyncio
import websockets
import json
import time

async def test_liq_stream():
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"
    print(f"Connecting to {url}...")
    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            print("Successfully connected!")
            start_time = time.time()
            msg_count = 0
            while time.time() - start_time < 30: # 30s
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    payload = json.loads(msg)
                    data_list = payload if isinstance(payload, list) else [payload]
                    for event in data_list:
                        o = event.get('o', {})
                        symbol = o.get('s', 'UNKNOWN')
                        side = o.get('S', 'UNKNOWN')
                        q = float(o.get('q', 0))
                        p = float(o.get('p', 0))
                        val = q * p
                        msg_count += 1
                        if val > 10000:
                            print(f"Received: {symbol} | {side} | ${val:,.0f}")
                except asyncio.TimeoutError:
                    print("- No event in last 5s...")
            print(f"Done. {msg_count} events total.")
    except Exception as e:
        print(f"WS Failure: {e}")

if __name__ == "__main__":
    asyncio.run(test_liq_stream())
