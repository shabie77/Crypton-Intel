import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import sqlite3
import os

DB_FILE = "subscribers.db"

def repair_db():
    if not os.path.exists(DB_FILE):
        print(f"Error: {DB_FILE} not found.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cursor.fetchall()]
    
    all_discovered_ids = set()
    
    for table in tables:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            cols = [c[1] for c in cursor.fetchall()]
            
            id_col = None
            if 'user_id' in cols: id_col = 'user_id'
            elif 'channel_id' in cols: id_col = 'channel_id'
            elif 'chat_id' in cols: id_col = 'chat_id'
            
            if id_col:
                cursor.execute(f"SELECT DISTINCT {id_col} FROM {table}")
                rows = cursor.fetchall()
                for r in rows:
                    raw_id = str(r[0])
                    # Handle negative IDs for groups
                    clean_id = raw_id.strip()
                    if clean_id.lstrip('-').isdigit():
                        all_discovered_ids.add(clean_id)
                print(f"Found {len(rows)} potential IDs in '{table}'")
        except Exception as e:
            print(f"Error reading {table}: {e}")

    print(f"Total Unique IDs discovered: {len(all_discovered_ids)}")
    print(f"IDs: {all_discovered_ids}")
    
    if all_discovered_ids:
        print("Migrating...")
        for uid in all_discovered_ids:
            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, status, tier)
                VALUES (?, 'ACTIVE', 'free')
            """, (uid,))
        
        admin_id = os.getenv("ADMIN_USER_ID", "1120431469")
        cursor.execute("UPDATE users SET tier='pro' WHERE user_id=?", (admin_id,))
        
        conn.commit()
        print("Done.")
    else:
        print("No IDs found.")
        
    conn.close()

if __name__ == "__main__":
    repair_db()
