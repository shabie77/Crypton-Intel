import sqlite3
import os

DB_FILE = "subscribers.db"

def init_db():
    print("🛠️ Initializing SQLite Database...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_name TEXT,
            status TEXT
        )
    ''')
    cursor.execute('''
        INSERT OR IGNORE INTO channels (channel_id, channel_name, status) 
        VALUES ('1120431469', 'Pes_Store (Admin)', 'ACTIVE')
    ''')
    conn.commit()
    conn.close()
    print("✅ Database ready.")

def add_channel(channel_id, channel_name):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM channels WHERE channel_id = ?", (str(channel_id),))
    existing_channel = cursor.fetchone()
    
    if existing_channel:
        print(f"🔒 Banned channel {channel_name} tried to re-add the bot. Access Denied.")
    else:
        cursor.execute('''
            INSERT INTO channels (channel_id, channel_name, status) 
            VALUES (?, ?, 'ACTIVE')
        ''', (str(channel_id), channel_name))
        print(f"🎉 New Channel Registered: {channel_name} ({channel_id})")
    conn.commit()
    conn.close()

def ban_channel(chat_identifier):
    """ Revokes access, using wildcards to ignore emojis! """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # We use LIKE %name% to catch partial matches like "JACK_eSports Gallery" instead of strictly demanding the completely exact string including the "🎮"
    cursor.execute('''
        UPDATE channels SET status = 'EXPIRED' 
        WHERE channel_name LIKE ? OR channel_id = ?
    ''', (f"%{chat_identifier}%", chat_identifier))
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_affected > 0:
        print(f"🚫 Channel Revoked: {chat_identifier}")
        return True
    else:
        print(f"⚠️ Error: Could not find '{chat_identifier}' in database. Try using their numerical ID.")
        return False

def renew_channel(chat_identifier):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE channels SET status = 'ACTIVE' 
        WHERE channel_name LIKE ? OR channel_id = ?
    ''', (f"%{chat_identifier}%", chat_identifier))
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_affected > 0:
        print(f"✅ Channel Renewed: {chat_identifier}")
        return True
    return False

def get_channel_id_by_name(chat_identifier):
    """ Helps the main script fetch the actual numerical ID to execute leaveChat. """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM channels WHERE channel_name LIKE ? OR channel_id = ?", (f"%{chat_identifier}%", chat_identifier))
    row = cursor.fetchone()
    conn.close()
    
    if row: return row[0]
    return None

def get_active_channels():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM channels WHERE status = 'ACTIVE'")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]
