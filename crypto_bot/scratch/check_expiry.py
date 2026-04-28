import sqlite3
from datetime import datetime, timezone

def check_expiry():
    conn = sqlite3.connect("subscribers.db")
    row = conn.execute("SELECT pro_expiry FROM users WHERE user_id='1120431469'").fetchone()
    print(f"Pro Expiry: {row[0]}")
    print(f"Current UTC: {datetime.now(timezone.utc)}")
    conn.close()

if __name__ == "__main__":
    check_expiry()
