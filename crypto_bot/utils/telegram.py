import os
import asyncio
import aiohttp
from utils.app_state import get_db

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""
BROADCAST_SEMAPHORE = asyncio.Semaphore(25)

async def _send_telegram_message_chunk(session, chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    for attempt in range(3):
        try:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    return True
                if response.status == 403:
                    print(f"Telegram send failed (403) for {chat_id} - Revoking access.")
                    database = get_db()
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
                    print(f"⚠️ Telegram Rate Limited (429). Waiting {wait}s...")
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
    paragraphs = text.split('\n')
    current_part = ""
    for p in paragraphs:
        if len(current_part) + len(p) + 1 <= 4000:
            current_part += p + "\n"
        else:
            if current_part:
                parts.append(current_part)
            if len(p) > 4000:
                parts.extend([p[i:i+4000] for i in range(0, len(p), 4000)])
                current_part = ""
            else:
                current_part = p + "\n"
    if current_part:
        parts.append(current_part)

    success = True
    for part in parts:
        res = await _send_telegram_message_chunk(session, chat_id, part.strip())
        if not res: success = False
        await asyncio.sleep(0.5)
    return success

async def send_telegram_message_semaphore(session, chat_id, text):
    async with BROADCAST_SEMAPHORE:
        return await send_telegram_message(session, chat_id, text)

async def broadcast_message(session, text, include_channels=True, include_users=True):
    database = get_db()
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
