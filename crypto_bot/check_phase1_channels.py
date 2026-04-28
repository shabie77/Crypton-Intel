import sqlite3
db_path = r'D:\info of bots\crypto_bot\subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM channels;")
    rows = cursor.fetchall()
    print(f"TOTAL CHANNELS IN THIS DB: {len(rows)}")
    for r in rows:
        print(r)
    conn.close()
except Exception as e:
    print(f"Error: {e}")
