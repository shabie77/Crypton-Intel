"""
commands/cmd_digest.py
"""
import asyncio
from utils.config import ADMIN_USER_ID
from digest.digest_scheduler import send_daily_digest
from utils.app_state import get_bot_app
import aiohttp

async def handle_digest(update, context):
    """Manual trigger for daily digest."""
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_USER_ID:
        return

    await update.message.reply_text("🚨 Triggering Manual Digest broadcast...")
    
    # We need a session here. In the modular version, we might want a global session or create one.
    # For now, create a temporary one or use a shared one if available.
    async with aiohttp.ClientSession() as session:
        await send_daily_digest(session, mark_as_sent=False)
    
    await update.message.reply_text("✅ Digest broadcasted manually.")
