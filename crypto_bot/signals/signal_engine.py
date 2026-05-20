"""
signals/signal_engine.py
"""
import asyncio
import traceback
from datetime import datetime, timezone
from utils.config import load_config
from utils.state import active_signal_states, rsi_state_tracker
import database

from utils.binance_api import fetch_symbol_snapshot
from utils.scoring_engine import calculate_score
from signals.signal_formatter import dispatch_signal_alert
from signals.signal_cooldown import parse_timeframe_state

async def scan_coin_logic(session, symbol, config, sensitivity):
    """Core logic to evaluate a single coin for a signal."""
    snapshot = await fetch_symbol_snapshot(session, symbol, config)
    if not snapshot:
        return
    
    active_signal_states[symbol] = {
        "price": snapshot["price"],
        "rsi": snapshot["rsi"],
        "state": active_signal_states.get(symbol, {}).get("state", "NEUTRAL"),
        "timestamp": datetime.now(timezone.utc).timestamp()
    }
    
    long_liq = await asyncio.to_thread(database.get_coin_liquidations_30min, symbol, "BUY")
    short_liq = await asyncio.to_thread(database.get_coin_liquidations_30min, symbol, "SELL")
    
    market_pulse = 50
    result = calculate_score(snapshot, market_pulse, {"longs": long_liq, "shorts": short_liq})
    
    if not result or result["score"] < sensitivity:
        return
        
    direction = result["direction"]
    score = result["score"]
    
    can_fire = await asyncio.to_thread(
        database.claim_cooldown_for_user,
        "GLOBAL_SIGNAL", symbol, direction, 240
    )
    
    if not can_fire:
        return
        
    print(f"🔥 SIGNAL FIRED: {symbol} {direction} (Score: {score}%)")
    
    active_signal_states[symbol]["state"] = "CONFIRMED"
    await asyncio.to_thread(database.save_bot_state, "lifecycle_state", active_signal_states)
    
    await dispatch_signal_alert(session, result, snapshot["price"])

async def rsi_scanner_loop(session):
    """Scans tracked coins for RSI extremes and structure changes."""
    print("🔍 Signal Engine is Live...")
    await asyncio.sleep(5)
    while True:
        try:
            config = await load_config()
            coins = config.get("tracked_coins", ["BTC", "ETH", "SOL"])
            sensitivity = await asyncio.to_thread(database.get_sensitivity)
            print(f"📊 Scan Cycle: {len(coins)} symbols")
            for symbol in coins:
                await scan_coin_logic(session, symbol, config, sensitivity)
                await asyncio.sleep(2)
            await asyncio.sleep(60)
        except Exception as e:
            print(f"❌ Signal Scanner Error: {e}")
            traceback.print_exc()
            await asyncio.sleep(30)

async def start_signal_loop(session):
    """Entry point for the signal engine."""
    await rsi_scanner_loop(session)
