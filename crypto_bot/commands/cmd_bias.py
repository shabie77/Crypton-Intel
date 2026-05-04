import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app
from utils.state import active_signal_states
import database

def get_symbol_bias(symbol):
    """Returns the macro and micro bias for a symbol."""
    # This would ideally come from the latest scan. For now, we use active_signal_states.
    state = active_signal_states.get(symbol.upper())
    if not state:
        return f"❌ No bias data for {symbol}."

    return (
        f"🧭 <b>Bias: {symbol.upper()}</b>\n\n"
        f"Structure: {state.get('state', 'NEUTRAL')}\n"
        f"Zone: {state.get('zone', 'NEUTRAL')}\n\n"
        f"Current Price: ${state.get('price', 0.0):,.2f}"
    )

async def handle_bias(update, context):
    user_id = update.effective_user.id
    tier = await asyncio.to_thread(database.get_user_tier, user_id)
    
    if tier != "pro":
        await update.message.reply_text("❌ The /bias command is exclusive to Pro subscribers. Upgrade with /start.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Please provide a symbol. Usage: /bias BTC")
        return

    symbol = context.args[0].upper()
    msg = get_symbol_bias(symbol)
    await update.message.reply_text(msg, parse_mode="HTML")
