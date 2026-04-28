import sqlite3
import os

DATA_DIR = os.path.join(os.getcwd(), "data")
DB_FILE = os.path.join(DATA_DIR, "subscribers.db")

def check_user():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, tier, pro_expiry, status FROM users WHERE user_id = '1120431469'")
    row = cursor.fetchone()
    print(f"User Data: {row}")
    conn.close()

if __name__ == "__main__":
    check_user()
