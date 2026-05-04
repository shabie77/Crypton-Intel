"""
digest/digest_scheduler.py
"""
import asyncio
from datetime import datetime, timezone, timedelta
from utils.config import load_config
from digest.digest_builder import build_digest_message
from digest.digest_gemini import get_ai_digest_insights
import database

# Safe fallback while other sessions are being built
try:
    from utils.telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(session, chat_id, text):
        from utils.app_state import get_bot_app
        app = get_bot_app()
        if app:
            try:
                await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                return True
            except Exception:
                pass
        return False

async def has_sent_digest_today():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_sent = await asyncio.to_thread(database.load_bot_state, "last_digest_date", "")
    return last_sent == today

async def mark_digest_sent():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    await asyncio.to_thread(database.save_bot_state, "last_digest_date", today)

async def send_daily_digest(session, target_date=None, mark_as_sent=True):
    """Orchestrates the daily digest generation and broadcast."""
    print("📣 Generating Daily Digest...")
    try:
        from utils.state import active_signal_states
    except ImportError:
        active_signal_states = {}

    data = {
        "pulse": 55,
        "trend": "Bullish",
        "coins": []
    }
    
    for sym, state in active_signal_states.items():
        data["coins"].append({
            "symbol": sym,
            "price": state.get("price", 0.0),
            "rsi": state.get("rsi", 50.0),
            "state": state.get("state", "NEUTRAL")
        })

    if not data["coins"]:
        data["coins"] = [{"symbol": "BTC", "price": 0.0, "rsi": 50.0, "state": "NEUTRAL"}]

    data["ai_insights"] = await get_ai_digest_insights(session, str(data["coins"]))

    pro_msg = await build_digest_message(data, is_pro=True)
    free_msg = await build_digest_message(data, is_pro=False)

    users = await asyncio.to_thread(database.get_all_users_with_tiers)
    sent_count = 0
    for u in users:
        uid, _, tier, _ = u
        msg = pro_msg if tier == "pro" else free_msg
        if await send_telegram_message(session, uid, msg):
            sent_count += 1
        await asyncio.sleep(0.05)
    
    print(f"✅ Daily Digest broadcasted to {sent_count} users.")
    if mark_as_sent:
        await mark_digest_sent()

async def daily_digest_loop(session):
    """The actual loop logic."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            # Shift time to 03:30 UTC for optimal Indian morning delivery (9:00 AM IST)
            target = now.replace(hour=3, minute=30, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_sec = (target - now).total_seconds()
            print(f"⏳ Next digest in {wait_sec/3600:.1f} hours")
            await asyncio.sleep(wait_sec)
            if not await has_sent_digest_today():
                await send_daily_digest(session)
        except Exception as e:
            print(f"❌ Digest Scheduler Error: {e}")
            await asyncio.sleep(60)

async def start_digest_scheduler(session):
    """Background loop for daily digest."""
    print("📅 Digest Scheduler is Live...")
    asyncio.create_task(daily_digest_loop(session))
