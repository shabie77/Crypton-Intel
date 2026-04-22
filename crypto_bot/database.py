import os
import sqlite3

DB_FILE = os.getenv("DB_FILE", "subscribers.db")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "1120431469")
FORCE_ADMIN_PRO = True

def _connect():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db():
    print("🛠️ Initializing SQLite Database (Phase 2)...")
    parent_dir = os.path.dirname(DB_FILE)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                status      TEXT DEFAULT 'ACTIVE',
                tier        TEXT DEFAULT 'free',
                pro_since   TEXT,
                pro_expiry  TEXT
            )
            """
        )

        cursor.execute("PRAGMA table_info(users)")
        columns = [info[1] for info in cursor.fetchall()]
        if "full_name" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        if "status" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'ACTIVE'")
        if "tier" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
        if "pro_since" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN pro_since TEXT")
        if "pro_expiry" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN pro_expiry TEXT")

        # Backfill NULL values for legacy users
        cursor.execute("UPDATE users SET full_name = '' WHERE full_name IS NULL")
        cursor.execute("UPDATE users SET status = 'ACTIVE' WHERE status IS NULL")
        cursor.execute("UPDATE users SET tier = 'free' WHERE tier IS NULL")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_watchlist (
                user_id TEXT,
                coin    TEXT,
                PRIMARY KEY (user_id, coin)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS price_alerts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             TEXT,
                coin                TEXT,
                target              REAL,
                direction           TEXT,
                is_active           INTEGER DEFAULT 1,
                is_paused           INTEGER DEFAULT 0,
                last_triggered_at   TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_usage (
                user_id         TEXT PRIMARY KEY,
                date            TEXT,
                digest_count    INTEGER DEFAULT 0
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_cooldown (
                user_id      TEXT,
                coin         TEXT,
                alert_type   TEXT,
                last_sent_at TIMESTAMP,
                PRIMARY KEY (user_id, coin, alert_type)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS news_seen (
                hash    TEXT PRIMARY KEY,
                seen_at TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_data (
                user_id             TEXT,
                week_start          DATE,
                coin                TEXT,
                open_price          REAL,
                close_price         REAL,
                alerts_triggered    INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, week_start, coin)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT,
                status TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT,
                symbol TEXT,
                content TEXT,
                timestamp TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS liquidations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                value REAL,
                timestamp TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                payment_id      TEXT PRIMARY KEY,
                user_id         TEXT,
                event_type      TEXT,
                amount          REAL,
                currency        TEXT,
                processed_at    TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_actions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id        TEXT,
                target_user_id  TEXT,
                action          TEXT,
                timestamp       TEXT
            )
            """
        )

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_liq_timestamp ON liquidations(timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts_log(timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_coin ON price_alerts(coin);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts(is_active);")

        if FORCE_ADMIN_PRO and ADMIN_USER_ID:
            cursor.execute(
                """
                INSERT INTO users (user_id, username, full_name, status, tier)
                VALUES (?, 'admin', 'Pes_Store (Admin)', 'ACTIVE', 'pro')
                ON CONFLICT(user_id) DO UPDATE SET 
                    tier = 'pro',
                    status = 'ACTIVE'
                """,
                (str(ADMIN_USER_ID),)
            )
        
        conn.commit()
    finally:
        conn.close()
    print("✅ Database ready.")


def add_channel(channel_id, channel_name):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM channels WHERE channel_id = ?", (str(channel_id),))
        existing_channel = cursor.fetchone()

        if existing_channel:
            status = existing_channel[0]
            if status in {"BANNED", "EXPIRED"}:
                print(f"Banned channel {channel_name} tried to re-add the bot. Access denied.")
                return False
            return True

        cursor.execute(
            """
            INSERT INTO channels (channel_id, channel_name, status)
            VALUES (?, ?, 'ACTIVE')
            """,
            (str(channel_id), channel_name),
        )
        print(f"New channel registered: {channel_name} ({channel_id})")
        conn.commit()
        return True
    finally:
        conn.close()


def ban_channel(chat_identifier):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE channels SET status = 'EXPIRED'
            WHERE channel_name LIKE ? OR channel_id = ?
            """,
            (f"%{chat_identifier}%", chat_identifier),
        )
        rows_affected = cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    if rows_affected > 0:
        print(f"Channel revoked: {chat_identifier}")
        return True
    return False


def add_user(user_id, username, full_name):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM users WHERE user_id = ?", (str(user_id),))
        existing_user = cursor.fetchone()

        if existing_user:
            cursor.execute(
                """
                UPDATE users
                SET username = ?, full_name = ?, status = 'ACTIVE'
                WHERE user_id = ?
                """,
                (username, full_name, str(user_id)),
            )
        else:
            cursor.execute(
                """
                INSERT INTO users (user_id, username, full_name, status, tier)
                VALUES (?, ?, ?, 'ACTIVE', 'free')
                """,
                (str(user_id), username, full_name),
            )
        conn.commit()
    finally:
        conn.close()


def get_user_tier(user_id):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT tier FROM users WHERE user_id = ?", (str(user_id),))
        row = cursor.fetchone()
    finally:
        conn.close()
    return row[0] if row else "free"


def save_watchlist(user_id, coins):
    conn = _connect()
    try:
        cursor = conn.cursor()
        user_id = str(user_id)
        cursor.execute("DELETE FROM user_watchlist WHERE user_id = ?", (user_id,))
        for coin in coins:
            cursor.execute(
                "INSERT INTO user_watchlist (user_id, coin) VALUES (?, ?)",
                (user_id, coin.upper()),
            )
        conn.commit()
    finally:
        conn.close()


def get_watchlist(user_id):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT coin FROM user_watchlist WHERE user_id = ?", (str(user_id),))
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def clear_watchlist(user_id):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_watchlist WHERE user_id = ?", (str(user_id),))
        conn.commit()
    finally:
        conn.close()


def revoke_access(chat_id):
    conn = _connect()
    try:
        cursor = conn.cursor()
        chat_id = str(chat_id)
        cursor.execute("UPDATE channels SET status = 'EXPIRED' WHERE channel_id = ?", (chat_id,))
        cursor.execute("UPDATE users SET status = 'EXPIRED' WHERE user_id = ?", (chat_id,))
        conn.commit()
    finally:
        conn.close()
    print(f"Access revoked silently for ID: {chat_id}")


def renew_channel(chat_identifier):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE channels SET status = 'ACTIVE'
            WHERE channel_name LIKE ? OR channel_id = ?
            """,
            (f"%{chat_identifier}%", chat_identifier),
        )
        rows_affected = cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    if rows_affected > 0:
        print(f"Channel renewed: {chat_identifier}")
        return True
    return False


def get_channel_id_by_name(chat_identifier):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT channel_id FROM channels WHERE channel_name LIKE ? OR channel_id = ?",
            (f"%{chat_identifier}%", chat_identifier),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def get_active_channels():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id FROM channels WHERE status = 'ACTIVE'")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def get_active_users():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE status = 'ACTIVE'")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def get_all_channels():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT channel_id, channel_name, status FROM channels ORDER BY channel_name COLLATE NOCASE"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return rows


def get_all_users():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username, full_name, status FROM users ORDER BY full_name COLLATE NOCASE"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()
    return rows


def log_alert(alert_type, symbol, content, timestamp):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO alerts_log (alert_type, symbol, content, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (alert_type, symbol, content, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def log_liquidation(symbol, side, value, timestamp):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO liquidations (symbol, side, value, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, side, value, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def get_24h_liquidation_stats(iso_cutoff):
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                SUM(value) as total,
                SUM(CASE WHEN side = 'SELL' THEN value ELSE 0 END) as longs,
                SUM(CASE WHEN side = 'BUY' THEN value ELSE 0 END) as shorts
            FROM liquidations
            WHERE timestamp >= ?
            """,
            (iso_cutoff,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    if row and row[0]:
        return {"total": row[0], "longs": row[1], "shorts": row[2]}
    return {"total": 0.0, "longs": 0.0, "shorts": 0.0}


async def cleanup_old_data():
    import asyncio

    def _run_delete():
        conn = _connect()
        try:
            conn.execute("DELETE FROM liquidations WHERE timestamp < datetime('now', '-7 days')")
            conn.execute("DELETE FROM alerts_log WHERE timestamp < datetime('now', '-7 days')")
            conn.commit()
            print("Database cleanup complete: old records purged.")
        except Exception as exc:
            print(f"Database cleanup failed: {exc}")
        finally:
            conn.close()

    await asyncio.to_thread(_run_delete)


def upgrade_user(user_id, plan_id, amount, currency):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    pro_since = now.strftime("%Y-%m-%d %H:%M:%S")
    pro_expiry = (now + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO users (user_id, username, full_name, status, tier, pro_since, pro_expiry)
            VALUES (?, '', '', 'ACTIVE', 'pro', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tier = 'pro',
                pro_since = excluded.pro_since,
                pro_expiry = excluded.pro_expiry
            """,
            (str(user_id), pro_since, pro_expiry),
        )
        conn.execute(
            """
            UPDATE price_alerts SET is_paused = 0
            WHERE user_id = ? AND is_active = 1
            """,
            (str(user_id),),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def record_payment(payment_id, user_id, event_type, amount, currency):
    from datetime import datetime, timezone

    processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO payments (payment_id, user_id, event_type, amount, currency, processed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(payment_id), str(user_id), event_type, amount, currency, processed_at),
        )
        inserted = cursor.rowcount > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def set_user_tier(admin_id, user_id, tier):
    """Admin-only function to force set a user's tier with audit logging."""
    from datetime import datetime, timezone, timedelta
    
    conn = _connect()
    try:
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        user_id = str(user_id)
        
        if tier == "pro":
            expiry = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            # UPSERT user as Pro
            cursor.execute(
                """
                INSERT INTO users (user_id, username, full_name, status, tier, pro_since, pro_expiry)
                VALUES (?, '', '', 'ACTIVE', 'pro', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    tier='pro',
                    status='ACTIVE',
                    pro_since=excluded.pro_since,
                    pro_expiry=excluded.pro_expiry
                """,
                (user_id, now, expiry),
            )
            # Restore all alerts
            cursor.execute("UPDATE price_alerts SET is_paused = 0 WHERE user_id = ?", (user_id,))
            action = "set_pro"
        else:
            # Downgrade to Free
            cursor.execute("UPDATE users SET tier='free' WHERE user_id=?", (user_id,))
            # Pause alerts above free limit (3)
            cursor.execute(
                """
                UPDATE price_alerts SET is_paused = 1
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM price_alerts
                    WHERE user_id = ?
                    ORDER BY id ASC LIMIT 3
                )
                """,
                (user_id, user_id),
            )
            action = "set_free"

        # Audit log
        cursor.execute(
            "INSERT INTO admin_actions (admin_id, target_user_id, action, timestamp) VALUES (?, ?, ?, ?)",
            (str(admin_id), user_id, action, now),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_all_users_with_tiers():
    """Returns user_id, username, tier, pro_expiry for all users."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT user_id, username, tier, pro_expiry 
            FROM users 
            ORDER BY 
                CASE WHEN tier='pro' THEN 0 ELSE 1 END,
                COALESCE(username, user_id) COLLATE NOCASE
            """
        )
        return cursor.fetchall()
    finally:
        conn.close()

