from utils.config import STARTED_AT
import utils.health_monitor as health

async def handle_status(update, context):
    msg = await health.get_status_message(STARTED_AT)
    await update.message.reply_text(msg, parse_mode="HTML")
