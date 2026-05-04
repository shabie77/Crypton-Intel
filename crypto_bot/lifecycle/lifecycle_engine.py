import asyncio
from datetime import datetime, timezone
from utils.state import active_signal_states
from database import save_lifecycle_state
import database

try:
    from indicators.levels import calculate_key_levels
except ImportError:
    def calculate_key_levels(*args, **kwargs): return None

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

async def evaluate_signal_lifecycle(session, symbol, price):
    """
    Evaluates the lifecycle state of a symbol based on price action.
    Transitions through 5 states: NEUTRAL -> WATCH -> EARLY -> CONFIRMED -> EXPIRED
    """
    state_data = active_signal_states.get(symbol, {})
    if not state_data:
        return

    current_state = state_data.get("state", "NEUTRAL")
    direction = state_data.get("direction", "LONG")
    entry_price = state_data.get("price", price)
    
    if current_state == "NEUTRAL":
        # Waiting for initial trigger
        return

    new_state = current_state
    
    # State Machine Logic
    if current_state == "WATCH":
        # Proximity to key levels can trigger EARLY
        new_state = "EARLY"
    elif current_state == "EARLY":
        # Confirmation of trend/volume can trigger CONFIRMED
        pass
    elif current_state == "CONFIRMED":
        # Monitor for expiry (Target reached, Stop Loss hit, or Time Decay)
        if direction == "LONG" and price < entry_price * 0.95:  # 5% drop
            new_state = "EXPIRED"
        elif direction == "SHORT" and price > entry_price * 1.05:  # 5% rise
            new_state = "EXPIRED"
        
        # Check time decay (e.g., 24 hours)
        timestamp = state_data.get("timestamp")
        if timestamp:
            age = datetime.now(timezone.utc).timestamp() - timestamp
            if age > 86400:  # 24 hours
                new_state = "EXPIRED"

    if new_state != current_state:
        state_data["state"] = new_state
        state_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        active_signal_states[symbol] = state_data
        
        # Persist state automatically to Supabase or local fallback
        await save_lifecycle_state(active_signal_states)
        
        if new_state == "EXPIRED":
            await send_invalidation_notice(session, symbol, direction, price)

async def send_invalidation_notice(session, symbol, direction, price):
    """Broadcasts a lifecycle invalidation/expiry notice to Pro users."""
    msg = (
        f"⚠️ <b>LIFECYCLE UPDATE: {symbol}</b>\n\n"
        f"The previous {direction} signal has reached <b>EXPIRED</b> status.\n"
        f"Current Price: ${price:,.2f}\n\n"
        f"<i>Position closed or invalidated based on market structure or time decay.</i>"
    )
    
    users = await asyncio.to_thread(database.get_all_users_with_tiers)
    for u in users:
        uid, _, tier, _ = u
        if tier == "pro":
            await send_telegram_message(session, uid, msg)
            await asyncio.sleep(0.05)
