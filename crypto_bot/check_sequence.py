import sqlite3
db_path = 'subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sqlite_sequence;")
    print(f"Sequence: {cursor.fetchall()}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
