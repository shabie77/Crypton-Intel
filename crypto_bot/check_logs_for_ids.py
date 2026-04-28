import sqlite3
db_path = 'subscribers.db'
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM alerts_log;")
    rows = cursor.fetchall()
    ids = set()
    for (content,) in rows:
        # Search for IDs in the log content
        import re
        matches = re.findall(r'\d{9,10}', content)
        for m in matches: ids.add(m)
    print(f"IDs found in logs: {ids}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
