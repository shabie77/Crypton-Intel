import asyncio
from datetime import datetime, timezone
import database

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

async def dispatch_signal_alert(session, result, price, target_chat_id=None):
    symbol = result["symbol"]
    direction = result["direction"]
    score = result["score"]
    strength = result["strength"]
    meter = result["meter"]
    rationale = "\n".join([f"• {r}" for r in result["rationale"]])
    emoji = "🟢" if direction == "LONG" else "🔴"
    
    pro_msg = (
        f"{emoji} <b>PRO SIGNAL: {symbol} {direction}</b>\n\n"
        f"<b>Price:</b> ${price:,.2f}\n"
        f"<b>Confidence:</b> {score}% | {strength}\n"
        f"<b>Meter:</b> {meter}\n\n"
        f"<b>Confluence Factors:</b>\n{rationale}\n\n"
        f"<i>Manage your risk. Wait for candle close.</i>"
    )
    
    free_msg = (
        f"{emoji} <b>SIGNAL DETECTED: {symbol} {direction}</b>\n\n"
        f"<b>Price:</b> ${price:,.2f}\n"
        f"<b>Confidence:</b> {score}%\n\n"
        f"Upgrade to PRO to see full confluence rationale and key levels.\n"
        f"Use /start for details."
    )
    
    if target_chat_id:
        tier = await asyncio.to_thread(database.get_user_tier, target_chat_id)
        msg = pro_msg if tier == "pro" else free_msg
        await send_telegram_message(session, target_chat_id, msg)
        return
        
    users = await asyncio.to_thread(database.get_all_users_with_tiers)
    sent_count = 0
    for u in users:
        uid, _, tier, _ = u
        msg = pro_msg if tier == "pro" else free_msg
        if await send_telegram_message(session, uid, msg):
            sent_count += 1
        await asyncio.sleep(0.05)
    print(f"✅ Signal broadcasted to {sent_count} users.")

async def call_gemini_rest(session, prompt):
    return "AI response"

async def call_groq_rest(session, prompt):
    return "AI response"
