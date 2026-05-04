import asyncio
from datetime import datetime, timezone
from utils.state import active_signal_states
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

async def dispatch_watch_alert(session, symbol, price, zone):
    """
    Dispatches a WATCH ZONE alert when a coin approaches key levels.
    """
    # Check global cooldown to prevent spam (e.g., 60 mins per coin for watch zones)
    can_fire = await asyncio.to_thread(
        database.claim_cooldown_for_user,
        "GLOBAL_ALERT", symbol, "WATCH_ZONE", 60
    )
    
    if not can_fire:
        return

    state = active_signal_states.get(symbol, {})
    support = state.get("support", 0.0)
    resistance = state.get("resistance", 0.0)
    
    pro_msg = (
        f"👁️ <b>WATCH ZONE: {symbol}</b>\n\n"
        f"Asset has entered the <b>{zone}</b> zone.\n"
        f"<b>Current Price:</b> ${price:,.2f}\n\n"
        f"📍 <b>Key Levels:</b>\n"
        f"Resistance: ${resistance:,.2f}\n"
        f"Support: ${support:,.2f}\n\n"
        f"<i>Prepare for potential structure shifts. Waiting for confirmation.</i>"
    )
    
    free_msg = (
        f"👁️ <b>WATCH ZONE: {symbol}</b>\n\n"
        f"Asset is approaching a major volatility zone.\n"
        f"<b>Current Price:</b> ${price:,.2f}\n\n"
        f"Upgrade to PRO to view exact Support & Resistance levels and receive early alerts.\n"
        f"Use /start for details."
    )

    users = await asyncio.to_thread(database.get_all_users_with_tiers)
    sent_count = 0
    for u in users:
        uid, _, tier, _ = u
        msg = pro_msg if tier == "pro" else free_msg
        if await send_telegram_message(session, uid, msg):
            sent_count += 1
        await asyncio.sleep(0.05)
        
    print(f"👁️ Watch Zone alert broadcasted for {symbol} to {sent_count} users.")
