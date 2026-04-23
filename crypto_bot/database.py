import os
import sqlite3
import asyncio
from datetime import datetime, timezone, timedelta

# Persistence Settings
DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.path.join(os.getcwd(), "data"))
DB_FILE = os.getenv("DB_FILE", os.path.join(DATA_DIR, "subscribers.db"))

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     TEXT PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                status      TEXT DEFAULT 'ACTIVE',
                tier        TEXT DEFAULT 'free',
                pro_since   TEXT,
                pro_expiry  TEXT
            )
            """)

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_watchlist (
                user_id TEXT,
                coin    TEXT,
                PRIMARY KEY (user_id, coin)
            )
            """)
        cursor.execute("""
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
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
                user_id         TEXT PRIMARY KEY,
                date            TEXT,
                digest_count    INTEGER DEFAULT 0
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_cooldown (
                user_id      TEXT,
                coin         TEXT,
                alert_type   TEXT,
                last_sent_at TIMESTAMP,
                PRIMARY KEY (user_id, coin, alert_type)
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news_seen (
                hash    TEXT PRIMARY KEY,
                seen_at TIMESTAMP
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_data (
                user_id             TEXT,
                week_start          DATE,
                coin                TEXT,
                open_price          REAL,
                close_price         REAL,
                alerts_triggered    INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, week_start, coin)
            )
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT,
                status TEXT
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT,
                symbol TEXT,
                content TEXT,
                timestamp TEXT
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS liquidations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                value REAL,
                timestamp TEXT
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol              TEXT,
                signal_type         TEXT,
                direction           TEXT,
                score               INTEGER,
                trend_score         INTEGER,
                volume_score        INTEGER,
                rsi_score           INTEGER,
                liq_score           INTEGER,
                pulse_score         INTEGER,
                alignment_score     INTEGER,
                strength            TEXT,
                rationale           TEXT,
                market_pulse        INTEGER,
                timeframe           TEXT,
                price_at_signal     REAL,
                price_after_1h      REAL,
                price_after_4h      REAL,
                outcome             TEXT,
                timestamp           TEXT,
                raw_rsi             REAL,
                raw_vol_ratio       REAL,
                raw_long_liq        REAL,
                raw_short_liq       REAL
            )
            """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_history_symbol ON signal_history(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_signal_history_timestamp ON signal_history(timestamp)")

        # Migration for signal_history
        cursor.execute("PRAGMA table_info(signal_history)")
        sig_cols = [info[1] for info in cursor.fetchall()]
        for col in ["raw_rsi", "raw_vol_ratio", "raw_long_liq", "raw_short_liq"]:
            if col not in sig_cols:
                cursor.execute(f"ALTER TABLE signal_history ADD COLUMN {col} REAL")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                key     TEXT PRIMARY KEY,
                value   TEXT
            )
            """)
        cursor.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('signal_sensitivity', '55')")

        # alert_cooldown migration
        cursor.execute("PRAGMA table_info(alert_cooldown)")
        cooldown_cols = [info[1] for info in cursor.fetchall()]
        if "cooldown_minutes" not in cooldown_cols:
            cursor.execute("ALTER TABLE alert_cooldown ADD COLUMN cooldown_minutes INTEGER DEFAULT 15")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                payment_id      TEXT PRIMARY KEY,
                user_id         TEXT,
                event_type      TEXT,
                amount          REAL,
                currency        TEXT,
                processed_at    TEXT
            )
            """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_actions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id        TEXT,
                target_user_id  TEXT,
                action          TEXT,
                timestamp       TEXT
            )
            """)

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
                (str(ADMIN_USER_ID),),
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
        cursor.execute("SELECT channel_id, channel_name, status FROM channels ORDER BY channel_name COLLATE NOCASE")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return rows


def get_all_users():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, full_name, status FROM users ORDER BY full_name COLLATE NOCASE")
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


def log_signal(
    symbol,
    signal_type,
    direction,
    score,
    trend_score,
    volume_score,
    rsi_score,
    liq_score,
    pulse_score,
    alignment_score,
    strength,
    rationale,
    market_pulse,
    timeframe,
    price_at_signal,
    raw_rsi,
    raw_vol_ratio,
    raw_long_liq,
    raw_short_liq,
):
    """Logs a generated signal to signal_history including raw evidence for backtesting."""
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO signal_history (
                symbol, signal_type, direction, score,
                trend_score, volume_score, rsi_score, liq_score,
                pulse_score, alignment_score, strength, rationale,
                market_pulse, timeframe, price_at_signal, timestamp,
                raw_rsi, raw_vol_ratio, raw_long_liq, raw_short_liq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                symbol,
                signal_type,
                direction,
                score,
                trend_score,
                volume_score,
                rsi_score,
                liq_score,
                pulse_score,
                alignment_score,
                strength,
                rationale,
                market_pulse,
                timeframe,
                price_at_signal,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                raw_rsi,
                raw_vol_ratio,
                raw_long_liq,
                raw_short_liq,
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"❌ log_signal error: {e}")
    finally:
        conn.close()


def get_sensitivity():
    """Returns the current signal broadcast threshold. Default 55."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_config WHERE key = 'signal_sensitivity'")
        row = cursor.fetchone()
        return int(row[0]) if row else 55
    except Exception:
        return 55
    finally:
        conn.close()


def set_sensitivity(value):
    """Updates the signal broadcast threshold. Value must be 40–90."""
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO bot_config (key, value) VALUES ('signal_sensitivity', ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
        """,
            (str(value), str(value)),
        )
        conn.commit()
    except Exception as e:
        print(f"❌ set_sensitivity error: {e}")
    finally:
        conn.close()


def get_coin_liquidations_30min(symbol, side):
    """Returns per-coin liquidation data filtered to last 30 minutes."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S UTC")
    conn = _connect()
    try:
        cursor = conn.cursor()
        # Fix: Using LIKE to handle symbol vs pair mismatch (e.g. BTC vs BTCUSDT)
        cursor.execute(
            """
            SELECT SUM(value) FROM liquidations
            WHERE symbol LIKE ? AND side = ? AND timestamp >= ?
        """,
            (f"%{symbol}%", side, cutoff),
        )
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception as e:
        print(f"❌ get_coin_liquidations_30min error: {e}")
        return 0.0
    finally:
        conn.close()


def claim_cooldown_for_user(user_id, coin, alert_type, cooldown_minutes=15):
    """
    Atomically checks if a user is in cooldown, and if not, updates the timestamp.
    Returns True if the alert should be sent, False if it is blocked by cooldown.
    """
    now_dt = datetime.now(timezone.utc)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")  # Start write transaction for atomicity

        cursor.execute(
            """
            SELECT last_sent_at FROM alert_cooldown
            WHERE user_id = ? AND coin = ? AND alert_type = ?
        """,
            (str(user_id), coin, alert_type),
        )

        row = cursor.fetchone()
        if row:
            last_sent_str = row[0]
            try:
                # Handle both timestamp formats just in case
                if "UTC" in last_sent_str:
                    last_sent = datetime.strptime(last_sent_str, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
                else:
                    last_sent = datetime.strptime(last_sent_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

                if now_dt - last_sent < timedelta(minutes=cooldown_minutes):
                    conn.commit()
                    return False  # Still in cooldown
            except (ValueError, TypeError):
                pass  # Parse error, assume cooldown is over

        # Update/Insert new cooldown timestamp
        cursor.execute(
            """
            INSERT INTO alert_cooldown (user_id, coin, alert_type, last_sent_at, cooldown_minutes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, coin, alert_type)
            DO UPDATE SET last_sent_at = ?, cooldown_minutes = ?
        """,
            (str(user_id), coin, alert_type, now_str, cooldown_minutes, now_str, cooldown_minutes),
        )

        conn.commit()
        return True  # Successfully claimed
    except Exception as e:
        conn.rollback()
        print(f"❌ claim_cooldown_for_user error: {e}")
        return False
    finally:
        conn.close()


def get_user_pro_info(user_id):
    """Returns (tier, pro_since) tuple for a user."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT tier, pro_since FROM users WHERE user_id = ?", (str(user_id),))
        row = cursor.fetchone()
        return row if row else ("free", None)
    except Exception:
        return "free", None
    finally:
        conn.close()


def get_all_users_with_tiers():
    """Returns user_id, username, effective_tier, pro_expiry for all ACTIVE users."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username,
                   CASE
                       WHEN tier = 'pro' AND (pro_expiry IS NULL OR pro_expiry > CURRENT_TIMESTAMP) THEN 'pro'
                       ELSE 'free'
                   END as effective_tier,
                   pro_expiry
            FROM users
            WHERE status = 'ACTIVE'
            ORDER BY
                CASE WHEN effective_tier='pro' THEN 0 ELSE 1 END,
                COALESCE(username, user_id) COLLATE NOCASE
            """)
        return cursor.fetchall()
    finally:
        conn.close()


def reset_cooldown_for_user(user_id, coin, alert_type):
    """Resets the cooldown for a specific user and coin if a send fails."""
    conn = _connect()
    try:
        conn.execute(
            "DELETE FROM alert_cooldown WHERE user_id = ? AND coin = ? AND alert_type = ?",
            (str(user_id), str(coin), alert_type),
        )
        conn.commit()
    finally:
        conn.close()
