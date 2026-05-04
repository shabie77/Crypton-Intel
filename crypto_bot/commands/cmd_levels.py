import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app
from utils.state import active_signal_states
import database

def get_symbol_levels(symbol):
    """Returns the current WATCH levels and proximity for a symbol."""
    state = active_signal_states.get(symbol.upper())
    if not state:
        return f"❌ No levels tracked for {symbol} yet."

    price = state.get("price", 0.0)
    res = state.get("resistance", 0.0)
    sup = state.get("support", 0.0)

    prox_res = (1 - (price / res)) * 100 if res > 0 and price > 0 else 0
    prox_sup = ((price / sup) - 1) * 100 if sup > 0 and price > 0 else 0

    return (
        f"📊 <b>Levels: {symbol.upper()}</b>\n\n"
        f"Resistance: ${res:,.2f} ({prox_res:.1f}% away)\n"
        f"Support: ${sup:,.2f} ({prox_sup:.1f}% away)\n\n"
        f"State: {state.get('state', 'NEUTRAL')}\n"
        f"Price: ${price:,.2f}"
    )


async def handle_levels(update, context):
    user_id = update.effective_user.id
    tier = await asyncio.to_thread(database.get_user_tier, user_id)
    
    if tier != "pro":
        await update.message.reply_text("❌ The /levels command is exclusive to Pro subscribers. Upgrade with /start.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Please provide a symbol. Usage: /levels BTC")
        return

    symbol = context.args[0].upper()
    msg = get_symbol_levels(symbol)
    await update.message.reply_text(msg, parse_mode="HTML")
