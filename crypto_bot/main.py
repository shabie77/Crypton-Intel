"""
CRYPTON INTEL — Main Entry Point
Initialises shared state, registers handlers, starts schedulers.
"""

# ============================================================
# SECTION 1 — ENVIRONMENT (MUST BE FIRST — NEVER MOVE THIS)
# ============================================================
import os
import asyncio
import aiohttp
from dotenv import load_dotenv
_app_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_app_dir, ".env"))  # Loads .env file — must run before any project imports

# ============================================================
# SECTION 2 — SHARED STATE INITIALISATION
# ============================================================
from utils.app_state import set_db, set_bot_app
from database.db_users import initialise_database

# Initialise database — returns live SQLite connection
db_conn = initialise_database()
set_db(db_conn)

# ============================================================
# SECTION 3 — TELEGRAM APPLICATION
# ============================================================
from telegram.ext import Application, CommandHandler
from utils.config import TELEGRAM_BOT_TOKEN

app = None
if TELEGRAM_BOT_TOKEN:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    set_bot_app(app)
else:
    print("⚠️ WARNING: TELEGRAM_BOT_TOKEN not set")

# ============================================================
# SECTION 4 — COMMAND HANDLER IMPORTS
# ============================================================
from commands.cmd_digest import handle_digest, handle_test_digest
from commands.cmd_signal import handle_signal
from commands.cmd_explain import handle_explain
from commands.cmd_levels import handle_levels
from commands.cmd_bias import handle_bias
from commands.cmd_weekly import handle_weekly
from commands.cmd_status import handle_status

# ============================================================
# SECTION 5 — SCHEDULER IMPORTS
# ============================================================
from digest.digest_scheduler import start_digest_scheduler
from signals.signal_engine import start_signal_loop
from utils.liquidation_poller import start_liquidation_poller
from commands.cmd_weekly import weekly_report_loop

# ============================================================
# SECTION 6 — HANDLER REGISTRATION
# ============================================================
if app:
    app.add_handler(CommandHandler("digest", handle_digest))
    app.add_handler(CommandHandler("test_digest", handle_test_digest))
    app.add_handler(CommandHandler("testdigest", handle_test_digest))
    app.add_handler(CommandHandler("signal", handle_signal))
    app.add_handler(CommandHandler("explain", handle_explain))
    app.add_handler(CommandHandler("levels", handle_levels))
    app.add_handler(CommandHandler("bias", handle_bias))
    app.add_handler(CommandHandler("weekly", handle_weekly))
    app.add_handler(CommandHandler("status", handle_status))

# ============================================================
# SECTION 7 — START SCHEDULERS AND BOT
# ============================================================

async def main():
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    connector = aiohttp.TCPConnector(keepalive_timeout=60)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Start background engines
        bg_tasks = [
            asyncio.create_task(start_digest_scheduler(session)),
            asyncio.create_task(start_signal_loop(session)),
            asyncio.create_task(start_liquidation_poller()),
            asyncio.create_task(weekly_report_loop(session)),
        ]
        
        if app:
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            print("🤖 Crypton Intel Bot is Live...")
        
        # Keep alive
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            for task in bg_tasks:
                task.cancel()
            if app:
                await app.stop()
                await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot turned off.")
