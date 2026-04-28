import sqlite3
db_path = 'subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    ids = set()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cursor.fetchall()]
    for table in tables:
        try:
            cursor.execute(f"SELECT DISTINCT user_id FROM {table}")
            rows = cursor.fetchall()
            for r in rows:
                if r[0]: ids.add(r[0])
        except:
            pass
    print(f"Total Unique User IDs found in ALL tables: {len(ids)}")
    print(ids)
    conn.close()
except Exception as e:
    print(f"Error: {e}")
