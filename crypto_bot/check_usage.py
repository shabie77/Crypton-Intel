import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import sqlite3
db_path = 'subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_usage;")
    rows = cursor.fetchall()
    print(f"TOTAL IN USER_USAGE: {len(rows)}")
    for r in rows:
        print(r)
    conn.close()
except Exception as e:
    print(f"Error: {e}")
