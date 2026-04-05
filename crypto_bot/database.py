import sqlite3
import os

DB_FILE = os.getenv("DB_FILE", "subscribers.db")


def _connect():
    return sqlite3.connect(DB_FILE)

def init_db():
    print("🛠️ Initializing SQLite Database...")
    parent_dir = os.path.dirname(DB_FILE)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            channel_name TEXT,
            status TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            status TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT,
            symbol TEXT,
            content TEXT,
            timestamp TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS liquidations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            value REAL,
            timestamp TEXT
        )
    ''')
    cursor.execute('''
        INSERT OR IGNORE INTO channels (channel_id, channel_name, status) 
        VALUES ('1120431469', 'Pes_Store (Admin)', 'ACTIVE')
    ''')
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, full_name, status)
        VALUES ('1120431469', 'admin', 'Pes_Store (Admin)', 'ACTIVE')
    ''')
    conn.commit()
    conn.close()
    print("✅ Database ready.")

def add_channel(channel_id, channel_name):
    conn = _connect()
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

def add_user(user_id, username, full_name):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE user_id = ?", (str(user_id),))
    existing_user = cursor.fetchone()

    if existing_user:
        cursor.execute('''
            UPDATE users
            SET username = ?, full_name = ?, status = 'ACTIVE'
            WHERE user_id = ?
        ''', (username, full_name, str(user_id)))
    else:
        cursor.execute('''
            INSERT INTO users (user_id, username, full_name, status)
            VALUES (?, ?, ?, 'ACTIVE')
        ''', (str(user_id), username, full_name))
    conn.commit()
    conn.close()

def ban_channel(chat_identifier):
    """ Revokes access, using wildcards to ignore emojis! """
    conn = _connect()
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
    return False

def revoke_access(chat_id):
    """ Silently revokes access for a user or channel (handles 403 Forbidden). """
    conn = _connect()
    cursor = conn.cursor()
    chat_id = str(chat_id)
    # Update channels
    cursor.execute("UPDATE channels SET status = 'EXPIRED' WHERE channel_id = ?", (chat_id,))
    # Update users
    cursor.execute("UPDATE users SET status = 'EXPIRED' WHERE user_id = ?", (chat_id,))
    conn.commit()
    conn.close()
    print(f"🔇 Access revoked silently for ID: {chat_id}")

def renew_channel(chat_identifier):
    conn = _connect()
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
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM channels WHERE channel_name LIKE ? OR channel_id = ?", (f"%{chat_identifier}%", chat_identifier))
    row = cursor.fetchone()
    conn.close()
    
    if row: return row[0]
    return None

def get_active_channels():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM channels WHERE status = 'ACTIVE'")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_active_users():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE status = 'ACTIVE'")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_all_channels():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, channel_name, status FROM channels ORDER BY channel_name COLLATE NOCASE")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, status FROM users ORDER BY full_name COLLATE NOCASE")
    rows = cursor.fetchall()
    conn.close()
    return rows

def log_alert(alert_type, symbol, content, timestamp):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO alerts_log (alert_type, symbol, content, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (alert_type, symbol, content, timestamp))
    conn.commit()
    conn.close()

def log_liquidation(symbol, side, value, timestamp):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO liquidations (symbol, side, value, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (symbol, side, value, timestamp))
    conn.commit()
    conn.close()

def get_24h_liquidation_stats(iso_cutoff):
    conn = _connect()
    cursor = conn.cursor()
    # stats: Total Value, Long Value (BUY order), Short Value (SELL order)
    # Side 'BUY' in forceOrder means a SHORT was liquidated.
    # Side 'SELL' in forceOrder means a LONG was liquidated.
    cursor.execute('''
        SELECT 
            SUM(value) as total,
            SUM(CASE WHEN side = 'SELL' THEN value ELSE 0 END) as longs,
            SUM(CASE WHEN side = 'BUY' THEN value ELSE 0 END) as shorts
        FROM liquidations
        WHERE timestamp >= ?
    ''', (iso_cutoff,))
    row = cursor.fetchone()
    conn.close()
    
    if row and row[0]:
        return {"total": row[0], "longs": row[1], "shorts": row[2]}
    return {"total": 0.0, "longs": 0.0, "shorts": 0.0}
