"""
commands/cmd_signal.py
"""
import asyncio
from utils.config import ADMIN_USER_ID
from utils.config import load_config
import aiohttp
import database

from signals.signal_engine import scan_coin_logic

async def handle_signal(update, context):
    """Manual trigger for a specific signal."""
    user_id = update.effective_user.id
    if str(user_id) != str(ADMIN_USER_ID):
        return

    args = context.args
    symbol = args[0].upper() if args else "BTC"

    await update.message.reply_text(f"🚨 Triggering Manual BROADCAST for {symbol}...")

    async with aiohttp.ClientSession() as session:
        config = await load_config()
        sensitivity = await asyncio.to_thread(database.get_sensitivity)
        await scan_coin_logic(session, symbol, config, sensitivity)
        
    await update.message.reply_text(f"✅ Manual scan executed for {symbol}. If conditions are met, a signal will be broadcasted.")
