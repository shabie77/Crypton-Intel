import sqlite3
import os

DB_FILE = "c:/TRADING BRAIN/crypto_bot/subscribers.db"

def check_tables():
    if not os.path.exists(DB_FILE):
        print(f"Error: {DB_FILE} does not exist.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables in {DB_FILE}: {tables}")

    for table in tables:
        t_name = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM {t_name}")
        count = cursor.fetchone()[0]
        print(f" - {t_name}: {count} rows")

    conn.close()

if __name__ == "__main__":
    check_tables()
