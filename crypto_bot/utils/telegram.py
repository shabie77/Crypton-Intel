import asyncio
import time

import aiohttp

from utils.app_state import get_db
from utils.config import BROADCAST_SEMAPHORE, TELEGRAM_API_URL
import database

_GLOBAL_SEND_LOCK = asyncio.Lock()
_CHAT_LOCKS_GUARD = asyncio.Lock()
_PER_CHAT_LOCKS: dict[str, asyncio.Lock] = {}
_NEXT_GLOBAL_SEND_TS = 0.0
_LAST_CHAT_SEND_TS: dict[str, float] = {}


async def _get_chat_lock(chat_id) -> asyncio.Lock:
    chat_key = str(chat_id)
    async with _CHAT_LOCKS_GUARD:
        lock = _PER_CHAT_LOCKS.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            _PER_CHAT_LOCKS[chat_key] = lock
        return lock


async def _wait_for_send_slot(chat_id):
    global _NEXT_GLOBAL_SEND_TS
    chat_key = str(chat_id)

    while True:
        now = time.monotonic()
        async with _GLOBAL_SEND_LOCK:
            earliest = max(
                _NEXT_GLOBAL_SEND_TS,
                _LAST_CHAT_SEND_TS.get(chat_key, 0.0) + 1.0,
            )
            if earliest <= now:
                _NEXT_GLOBAL_SEND_TS = now + 0.05
                _LAST_CHAT_SEND_TS[chat_key] = now
                return
            wait_for = earliest - now
        await asyncio.sleep(wait_for)


async def _send_telegram_message_chunk(session, chat_id, text):
    global _NEXT_GLOBAL_SEND_TS

    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    chat_lock = await _get_chat_lock(chat_id)

    async with chat_lock:
        for attempt in range(3):
            try:
                await _wait_for_send_slot(chat_id)
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        return True
                    if response.status == 403:
                        print(f"Telegram send failed (403) for {chat_id} - Revoking access.")
                        await asyncio.to_thread(database.revoke_access, str(chat_id))
                        return False
                    if response.status == 429:
                        wait = (
                            (await response.json())
                            .get("parameters", {})
                            .get("retry_after", 5)
                            if response.content_type == "application/json"
                            else 5
                        )
                        print(f"âš ï¸ Telegram Rate Limited (429). Waiting {wait}s...")
                        retry_until = time.monotonic() + wait
                        async with _GLOBAL_SEND_LOCK:
                            _NEXT_GLOBAL_SEND_TS = max(_NEXT_GLOBAL_SEND_TS, retry_until)
                        await asyncio.sleep(wait)
                        continue
                    print(f"Telegram send failed ({response.status}) for {chat_id}")
            except Exception as exc:
                print(f"Telegram send error on attempt {attempt + 1}: {exc}")
            await asyncio.sleep(1)

    print(f"Failed to send message to {chat_id}")
    return False


async def send_telegram_message(session, chat_id, text):
    if len(text) <= 4000:
        return await _send_telegram_message_chunk(session, chat_id, text)

    parts = []
    paragraphs = text.split("\n")
    current_part = ""
    for p in paragraphs:
        if len(current_part) + len(p) + 1 <= 4000:
            current_part += p + "\n"
        else:
            if current_part:
                parts.append(current_part)
            if len(p) > 4000:
                parts.extend([p[i:i + 4000] for i in range(0, len(p), 4000)])
                current_part = ""
            else:
                current_part = p + "\n"
    if current_part:
        parts.append(current_part)

    success = True
    for part in parts:
        res = await _send_telegram_message_chunk(session, chat_id, part.strip())
        if not res:
            success = False
        await asyncio.sleep(0.5)
    return success


async def send_telegram_message_semaphore(session, chat_id, text):
    async with BROADCAST_SEMAPHORE:
        return await send_telegram_message(session, chat_id, text)


async def broadcast_message(session, text, include_channels=True, include_users=True):
    sent = 0
    tasks = []

    if include_channels:
        channels = await asyncio.to_thread(database.get_active_channels)
        for chat_id in set(channels):
            tasks.append(send_telegram_message_semaphore(session, chat_id, text))

    if include_users:
        users = await asyncio.to_thread(database.get_active_users)
        for user_id in set(users):
            tier = await asyncio.to_thread(database.get_user_tier, user_id)
            if tier == "pro":
                watchlist = await asyncio.to_thread(database.get_watchlist, user_id)
                if watchlist:
                    mention_found = any(coin.upper() in text.upper() for coin in watchlist)
                    if not mention_found:
                        continue
            tasks.append(send_telegram_message_semaphore(session, user_id, text))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        sent = sum(1 for r in results if r is True)

    return sent
