import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app
from utils.state import active_signal_states
from utils.config import load_config
import database
import aiohttp

from digest.digest_gemini import call_gemini_rest, call_groq_rest
from utils.binance_api import fetch_symbol_snapshot

async def get_symbol_explanation(session, symbol):
    """Provides a detailed AI-driven explanation of the current confluence for a symbol."""
    state = active_signal_states.get(symbol.upper())
    if not state:
        return f"❌ No active data for {symbol}. Wait for the next scan."

    # We don't have the full signal_result here, so we synthesize from current state
    price = state.get("price", 0.0)
    if price == 0.0:
        snap = await fetch_symbol_snapshot(session, symbol, await load_config())
        if snap: price = snap["price"]

    prompt = (
        f"Explain the trading context for {symbol} at ${price:,.2f}.\n"
        f"State: {state.get('state', 'NEUTRAL')}, Zone: {state.get('zone', 'N/A')}, Support: ${state.get('support', 0):,.2f}, Resistance: ${state.get('resistance', 0):,.2f}.\n"
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

async def handle_explain(update, context):
    user_id = update.effective_user.id
    tier = await asyncio.to_thread(database.get_user_tier, user_id)
    
    if tier != "pro":
        await update.message.reply_text("❌ The /explain command is exclusive to Pro subscribers. Upgrade with /start.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Please provide a symbol. Usage: /explain BTC")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"🔍 Analyzing confluence and market structure for {symbol}...")

    async with aiohttp.ClientSession() as session:
        explanation = await get_symbol_explanation(session, symbol)
        
    await update.message.reply_text(f"🤖 <b>AI Analysis: {symbol}</b>\n\n{explanation}", parse_mode="HTML")
