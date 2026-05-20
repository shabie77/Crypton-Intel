"""
digest/digest_scheduler.py

4H Digest Scheduler.
Execution order per cycle:
  1. Fetch shared market data (populates cache)
  2. Build + broadcast Free Digest (only on 4 daytime IST slots)
  3. Build + broadcast personalised Pro Digest per user (all 6 slots)
"""
import asyncio
from datetime import datetime, timezone, timedelta

from utils.config import load_config
from digest.digest_engine import build_free_digest
from digest.pro_engine import build_pro_digest
import database
from utils.telegram import send_telegram_message_semaphore


# ── Slot Helpers ──────────────────────────────────────────────────────────────

def is_free_digest_slot() -> bool:
    """
    Free Digest only fires at 4 daytime IST slots:
    9:30 AM / 1:30 PM / 5:30 PM / 9:30 PM IST
    Mapped to UTC hours: 4, 8, 12, 16
    """
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return now_ist.hour in [9, 13, 17, 21]


def get_next_4h_candle() -> datetime:
    """Returns the UTC datetime of the next 4H candle close."""
    now = datetime.now(timezone.utc)
    next_hour = ((now.hour // 4) + 1) * 4
    if next_hour >= 24:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=next_hour, minute=0, second=0, microsecond=0)


async def has_sent_digest_4h(target_time: datetime) -> bool:
    last_sent = await asyncio.to_thread(database.load_bot_state, "last_4h_digest_time", "")
    return last_sent == target_time.isoformat()


async def mark_digest_sent_4h(target_time: datetime) -> None:
    await asyncio.to_thread(database.save_bot_state, "last_4h_digest_time", target_time.isoformat())


# ── Main Broadcast Orchestrator ───────────────────────────────────────────────

async def send_daily_digest(session, target_date=None, mark_as_sent=True, target_chat_id=None):
    """
    Orchestrates the 4H digest generation and broadcast.
    Shared cache is populated by the Free Digest build, then reused by Pro engine.
    """
    print("📣 Generating 4H Market Digest...")

    users      = await asyncio.to_thread(database.get_all_users_with_tiers)
    pro_users  = [u for u in users if u[2] == "pro"]
    free_users = [u for u in users if u[2] != "pro"]

    # ── Test mode: send to admin only ────────────────────────────────────────
    if target_chat_id:
        free_msg = await build_free_digest(session)
        await send_telegram_message_semaphore(session, target_chat_id, free_msg)

        watchlist = await asyncio.to_thread(database.get_watchlist, str(target_chat_id))
        if not watchlist:
            watchlist = ["BTC", "ETH", "SOL"]

        pro_msg = await build_pro_digest(session, str(target_chat_id), watchlist)
        await send_telegram_message_semaphore(session, target_chat_id, pro_msg)
        print(f"✅ Test Digest (Free & Pro) sent to {target_chat_id}.")
        return

    # ── STEP 1: Free Digest (schedule-gated) ─────────────────────────────────
    # Building the free digest also populates the shared market snapshot cache.
    # The Pro engine reads from this cache — no double-fetch occurs.
    free_msg  = None
    free_sent = 0

    if is_free_digest_slot():
        try:
            free_msg = await build_free_digest(session)
        except Exception as e:
            print(f"❌ Free digest build error: {e}")

        if free_msg:
            for u in free_users:
                try:
                    if await send_telegram_message_semaphore(session, u[0], free_msg):
                        free_sent += 1
                except Exception as e:
                    print(f"⚠️ Free send error to {u[0]}: {e}")
                await asyncio.sleep(0.05)
            print(f"📤 Free digest sent to {free_sent} users.")
    else:
        print("⏭️ Skipping free digest — not a scheduled IST slot.")

    # ── STEP 2: Pro Digest (all 6 slots) ─────────────────────────────────────
    pro_sent = 0

    for u in pro_users:
        uid       = u[0]
        watchlist = await asyncio.to_thread(database.get_watchlist, uid)
        if not watchlist:
            watchlist = ["BTC", "ETH", "SOL"]

        try:
            pro_msg = await build_pro_digest(session, uid, watchlist)
            if await send_telegram_message_semaphore(session, uid, pro_msg):
                pro_sent += 1
        except Exception as e:
            print(f"❌ Pro digest error for {uid}: {e}")

        await asyncio.sleep(0.05)  # rate-limit guard between users

    print(f"✅ Pro digest sent to {pro_sent} Pro users.")

    if mark_as_sent:
        await mark_digest_sent_4h(get_next_4h_candle() - timedelta(hours=4))


# ── Background Scheduler Loop ─────────────────────────────────────────────────

async def daily_digest_loop(session):
    """The 4H loop — waits for each candle close then fires the digest."""
    print("📅 4H Digest Scheduler is Live...")
    _last_cleanup_date = None

    while True:
        try:
            now    = datetime.now(timezone.utc)
            target = get_next_4h_candle()
            wait_sec = (target - now).total_seconds()
            print(f"⏳ Next 4H digest in {wait_sec / 3600:.1f} hours ({target.strftime('%H:%M UTC')})")
            await asyncio.sleep(wait_sec)
            await asyncio.sleep(60)  # 1 min buffer for exchange data to settle

            if not await has_sent_digest_4h(target):
                await send_daily_digest(session)

            # Daily DB cleanup — once per UTC day after the first successful cycle
            today = datetime.now(timezone.utc).date()
            if _last_cleanup_date != today:
                await asyncio.to_thread(database.cleanup_old_data, 30)
                _last_cleanup_date = today

        except Exception as e:
            print(f"❌ Digest Scheduler Error: {e}")
            await asyncio.sleep(60)


async def start_digest_scheduler(session):
    """Background loop entry point for 4H digest."""
    await daily_digest_loop(session)
