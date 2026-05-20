"""
commands/cmd_digest.py
"""
from utils.config import ADMIN_USER_ID
from digest.digest_scheduler import send_daily_digest
import aiohttp

async def handle_digest(update, context):
    """Manual trigger for daily digest."""
    user_id = update.effective_user.id
    if str(user_id) != str(ADMIN_USER_ID):
        return

    await update.message.reply_text("🚨 Triggering Manual Digest broadcast...")
    
    # We need a session here. In the modular version, we might want a global session or create one.
    # For now, create a temporary one or use a shared one if available.
    async with aiohttp.ClientSession() as session:
        await send_daily_digest(session, mark_as_sent=False)
    
    await update.message.reply_text("✅ Digest broadcasted manually.")

async def handle_test_digest(update, context):
    """Manual trigger for testing daily digest (sent only to admin)."""
    user_id = update.effective_user.id
    if str(user_id) != str(ADMIN_USER_ID):
        return

    await update.message.reply_text("🚨 Triggering Test Digest broadcast...")
    
    # We need a session here. In the modular version, we might want a global session or create one.
    # For now, create a temporary one or use a shared one if available.
    async with aiohttp.ClientSession() as session:
        await send_daily_digest(session, mark_as_sent=False, target_chat_id=ADMIN_USER_ID)
    
    await update.message.reply_text("✅ Test Digest sent manually.")
