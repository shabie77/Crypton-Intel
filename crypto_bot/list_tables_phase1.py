import sqlite3
db_path = r'D:\info of bots\crypto_bot\subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables in this DB: {tables}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
