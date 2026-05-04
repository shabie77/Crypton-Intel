import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import sqlite3
db_path = r'D:\info of bots\crypto_bot\subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, full_name FROM users WHERE status='ACTIVE';")
    users = cursor.fetchall()
    print(f"FOUND {len(users)} ACTIVE USERS IN THIS DATABASE.")
    for u in users:
        print(f"ID: {u[0]}, Name: {u[1]}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
