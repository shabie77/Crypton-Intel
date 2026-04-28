import sqlite3
import os

DB_FILE = "subscribers.db"

def check_user():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, tier, status FROM users WHERE user_id = '1120431469'")
        row = cursor.fetchone()
        print(f"User Data: {row}")
    except Exception as e:
        print(f"Error: {e}")
    conn.close()

if __name__ == "__main__":
    check_user()
