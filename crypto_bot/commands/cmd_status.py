import os
from datetime import datetime, timezone
from utils.config import load_config, STARTED_AT, DATA_DIR
from utils.helpers import html_safe
import database

async def handle_status(update, context):
    chat_id = update.effective_chat.id
    now = datetime.now(timezone.utc)
    uptime = now - STARTED_AT
    await update.message.reply_text(f"🤖 Bot Status: Online\nUptime: {uptime}")
