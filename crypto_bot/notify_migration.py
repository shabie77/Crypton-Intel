import sqlite3
import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def broadcast_migration():
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        return

    try:
        conn = sqlite3.connect('subscribers.db')
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE status='ACTIVE';")
        users = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Database error: {e}")
        return

    if not users:
        print("No active users found to notify.")
        return

    print(f"Starting migration broadcast to {len(users)} users...")
    
    msg = (
        "🔄 <b>SYSTEM UPDATE</b>\n\n"
        "Crypton Intel has moved to a new high-performance cloud server!\n"
        "To reactivate your personalized alerts and Pro features, please send /start now.\n\n"
        "Thank you for your patience!"
    )
    
    async with aiohttp.ClientSession() as session:
        for (user_id,) in users:
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            payload = {"chat_id": user_id, "text": msg, "parse_mode": "HTML"}
            try:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        print(f"Notified {user_id}")
                    else:
                        text = await resp.text()
                        print(f"Failed for {user_id}: {text}")
            except Exception as e:
                print(f"Connection error for {user_id}: {e}")

if __name__ == "__main__":
    asyncio.run(broadcast_migration())
