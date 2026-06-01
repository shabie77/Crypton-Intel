import os
import sqlite3
import asyncio
import json
import threading
import tempfile
from datetime import datetime, timezone, timedelta

try:
    from supabase import create_client
except ImportError:
    create_client = None

from utils.config import DATA_DIR, ADMIN_USER_ID

# Persistence Settings
DB_FILE = os.path.abspath(os.getenv("DB_FILE", os.path.join(DATA_DIR, "subscribers.db")))

FORCE_ADMIN_PRO = True

# Supabase Cloud Persistence (Phase 2)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = None

if SUPABASE_URL and SUPABASE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("☁️ Supabase Cloud Persistence initialized.")
    except Exception as e:
        print(f"⚠️ Supabase initialization failed: {e}")


def _connect():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def initialise_database():
    print("[db] Initializing SQLite Database (Phase 2)...")
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_streaks (
                symbol TEXT PRIMARY KEY,
                trend TEXT,
                last_trend TEXT,
                streak_days INTEGER,
                last_updated TEXT
            )
            """)

        # ── Divergence Signals (RSI divergence alert history) ──────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS divergence_signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT    NOT NULL,
                timeframe       TEXT    NOT NULL,
                direction       TEXT    NOT NULL,
                level           INTEGER NOT NULL,
                price_at_signal REAL,
                key_level       REAL,
                volume_confirmed INTEGER DEFAULT 0,
                fired_at        TEXT    NOT NULL,
                expires_at      TEXT    NOT NULL
            )
            """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_div_signals_lookup "
            "ON divergence_signals(symbol, timeframe, direction, level, fired_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_div_signals_expires "
            "ON divergence_signals(expires_at)"
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
                (str(ADMIN_USER_ID),),
            )

        conn.commit()
    finally:
        conn.close()
    print("[db] Database ready.")


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

        # Supabase Sync
        if supabase:
            try:
                supabase.table("channels").upsert({
                    "channel_id": str(channel_id),
                    "channel_name": channel_name,
                    "status": "ACTIVE"
                }).execute()
            except Exception as e:
                print(f"⚠️ Supabase sync error (add_channel): {e}")

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
        
        # Supabase Sync
        if supabase:
            try:
                supabase.table("users").upsert({
                    "user_id": str(user_id),
                    "username": username,
                    "full_name": full_name,
                    "status": "ACTIVE",
                    "tier": existing_user[0] if existing_user else "free"
                }).execute()
            except Exception as e:
                print(f"⚠️ Supabase sync error (add_user): {e}")

        conn.commit()
    finally:
        conn.close()


def get_user_tier(user_id):
    if str(user_id) == str(ADMIN_USER_ID):
        return "pro"
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT tier FROM users WHERE user_id = ?", (str(user_id),))
        row = cursor.fetchone()
        
        if row:
            return row[0]
            
        # Supabase Fallback
        if supabase:
            try:
                res = supabase.table("users").select("tier").eq("user_id", str(user_id)).maybe_single().execute()
                if res and res.data:
                    return res.data.get("tier", "free")
            except Exception as e:
                print(f"⚠️ Supabase fallback error in get_user_tier (uid={user_id}): {e}")

    finally:
        conn.close()
    return "free"


MAX_PRO_WATCHLIST = 5


def save_watchlist(user_id, coins):
    # Admin has no coin limit — all other users capped at MAX_PRO_WATCHLIST
    if len(coins) > MAX_PRO_WATCHLIST and str(user_id) != str(ADMIN_USER_ID):
        raise ValueError(
            f"⚠️ Maximum {MAX_PRO_WATCHLIST} coins allowed on your Pro watchlist.\n"
            f"Please remove a coin before adding a new one."
        )
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


def get_pro_user_count():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE tier = 'pro'")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"⚠️ get_pro_user_count error: {e}")
        return 0
    finally:
        conn.close()


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


# DELETED duplicated cleanup_old_data (moved and merged at line 870)


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
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
    except Exception as e:
        print(f"⚠️ get_sensitivity error: {e}")
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
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
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
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

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


def run_maintenance():
    """Runs SQLite maintenance commands to ensure long-term performance."""
    conn = _connect()
    try:
        conn.execute("PRAGMA optimize;")
        conn.execute("ANALYZE;")
        conn.commit()
        print("🔧 Database maintenance complete (Optimize/Analyze).")
    except Exception as e:
        print(f"❌ Database maintenance error: {e}")
    finally:
        conn.close()


def get_weekly_signal_summary(days=7):
    """Aggregates signal history for the past 7 days."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        
        # Total signals
        cursor.execute("SELECT COUNT(*) FROM signal_history WHERE timestamp >= ?", (cutoff,))
        total_signals = cursor.fetchone()[0] or 0
        
        # Most frequent symbol
        cursor.execute("""
            SELECT symbol, COUNT(*) as cnt 
            FROM signal_history 
            WHERE timestamp >= ? 
            GROUP BY symbol 
            ORDER BY cnt DESC LIMIT 1
        """, (cutoff,))
        freq_row = cursor.fetchone()
        most_active = freq_row[0] if freq_row else "None"
        
        # Average score
        cursor.execute("SELECT AVG(score) FROM signal_history WHERE timestamp >= ?", (cutoff,))
        avg_score = cursor.fetchone()[0] or 0
        
        # Best performers (highest score signals)
        cursor.execute("""
            SELECT symbol, direction, score, timestamp 
            FROM signal_history 
            WHERE timestamp >= ? 
            ORDER BY score DESC LIMIT 5
        """, (cutoff,))
        best_signals = cursor.fetchall()
        
        return {
            "total": total_signals,
            "most_active": most_active,
            "avg_score": round(avg_score, 1),
            "best_signals": best_signals
        }
    except Exception as e:
        print(f"Error getting weekly summary: {e}")
        return None
    finally:
        conn.close()


def cleanup_old_data(days=30):
    """Deletes logs and history older than specified days to save space."""
    conn = _connect()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        # Purge signal history, alerts, and liquidations
        conn.execute("DELETE FROM signal_history WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM alerts_log WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM liquidations WHERE timestamp < ?", (cutoff,))
        conn.commit()
        print(f"🧹 Database cleanup: Removed data older than {days} days.")
    except Exception as e:
        print(f"❌ Database cleanup error: {e}")
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
    except Exception as e:
        print(f"⚠️ get_user_pro_info error: {e}")
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
                       WHEN user_id = ? THEN 'pro'
                       WHEN tier = 'pro' AND (pro_expiry IS NULL OR pro_expiry > CURRENT_TIMESTAMP) THEN 'pro'
                       ELSE 'free'
                   END as effective_tier,
                   pro_expiry
            FROM users
            WHERE status = 'ACTIVE'
            ORDER BY
                CASE WHEN effective_tier='pro' THEN 0 ELSE 1 END,
                COALESCE(username, user_id) COLLATE NOCASE
            """, (str(ADMIN_USER_ID),))
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


def check_weekly_sent(week_str):
    """Checks if weekly report was already sent for this week."""
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM weekly_data WHERE week_start = ?", (str(week_str),))
        return cursor.fetchone() is not None
    except Exception as exc:
        print(f"Error checking weekly sent: {exc}")
        return False
    finally:
        conn.close()

def mark_weekly_sent(week_str):
    """Marks weekly report as sent."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO weekly_data (user_id, week_start, coin) VALUES ('GLOBAL', ?, 'NONE')",
            (str(week_str),),
        )
        conn.commit()
    except Exception as exc:
        print(f"Error marking weekly sent: {exc}")
    finally:
        conn.close()


def get_seen_news():
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT hash FROM news_seen")
        rows = cursor.fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()

def mark_news_seen(news_hash):
    conn = _connect()
    try:
        cursor = conn.cursor()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT OR IGNORE INTO news_seen (hash, seen_at) VALUES (?, ?)",
            (news_hash, now_str)
        )
        conn.commit()
    finally:
        conn.close()

def update_market_streak_atomic(symbol: str, today_trend: str) -> int:
    conn = _connect()
    try:
        cursor = conn.cursor()
        now_str = datetime.now(timezone.utc).isoformat()

        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT trend, last_trend, streak_days FROM market_streaks WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()

        today_bull = "Bullish" in today_trend
        today_bear = "Bearish" in today_trend

        if row:
            last_trend = row[1]
            streak = row[2]
            last_bull  = "Bullish" in last_trend if last_trend else False
            last_bear  = "Bearish" in last_trend if last_trend else False

            if today_bull and last_bull:
                streak = streak + 1 if streak > 0 else 1
            elif today_bear and last_bear:
                streak = streak - 1 if streak < 0 else -1
            else:
                streak = 1 if today_bull else (-1 if today_bear else 0)
        else:
            streak = 1 if today_bull else (-1 if today_bear else 0)

        cursor.execute("""
            INSERT INTO market_streaks (symbol, trend, last_trend, streak_days, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                trend=excluded.trend,
                last_trend=excluded.last_trend,
                streak_days=excluded.streak_days,
                last_updated=excluded.last_updated
        """, (symbol, today_trend, today_trend, streak, now_str))

        conn.commit()
        return streak
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_market_streaks(symbols: list[str]) -> dict:
    if not symbols:
        return {}

    norm_symbols = [str(symbol).upper() for symbol in symbols]
    placeholders = ", ".join("?" for _ in norm_symbols)

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT symbol, trend, last_trend, streak_days FROM market_streaks WHERE symbol IN ({placeholders})",
            norm_symbols,
        )
        rows = cursor.fetchall()
        return {
            row[0]: {
                "trend": row[1],
                "last_trend": row[2],
                "streak_days": row[3]
            } for row in rows
        }
    finally:
        conn.close()


# ══ Divergence Signal Functions ══════════════════════════════════════════════════════════════════════════════════

_DIV_EXPIRY_HOURS = {"4h": 12, "1d": 72, "1w": 504}  # 3× timeframe period


def create_divergence_signal(
    symbol: str, timeframe: str, direction: str, level: int,
    price: float, key_level, volume_confirmed: bool
) -> int | None:
    """Log a fired divergence alert to the DB. Returns new row ID or None on error."""
    expiry_h = _DIV_EXPIRY_HOURS.get(timeframe.lower(), 12)
    now      = datetime.now(timezone.utc)
    fired_at  = now.strftime("%Y-%m-%d %H:%M:%S")
    expires_at = (now + timedelta(hours=expiry_h)).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO divergence_signals
                (symbol, timeframe, direction, level, price_at_signal,
                 key_level, volume_confirmed, fired_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(), timeframe.lower(), direction, level, price,
                key_level, 1 if volume_confirmed else 0, fired_at, expires_at,
            ),
        )
        signal_id = cursor.lastrowid
        conn.commit()
        return signal_id
    except Exception as e:
        print(f"❌ create_divergence_signal error: {e}")
        return None
    finally:
        conn.close()


def get_recent_divergence_signal(
    symbol: str, timeframe: str, direction: str, level: int, within_hours: int
) -> dict | None:
    """
    Returns the most recent divergence signal matching the criteria if it
    was fired within `within_hours`. Returns None if none found.
    Pass within_hours=0 to use a 168-hour (7-day) dedup window (for 1W).
    """
    lookback = within_hours if within_hours > 0 else 168  # 7-day dedup for 1W
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=lookback)
    ).strftime("%Y-%m-%d %H:%M:%S")

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, symbol, timeframe, direction, level,
                   price_at_signal, key_level, volume_confirmed, fired_at
            FROM divergence_signals
            WHERE symbol = ? AND timeframe = ? AND direction = ?
              AND level = ? AND fired_at >= ?
            ORDER BY fired_at DESC
            LIMIT 1
            """,
            (symbol.upper(), timeframe.lower(), direction, level, cutoff),
        )
        row = cursor.fetchone()
        if row:
            return {
                "id": row[0], "symbol": row[1], "timeframe": row[2],
                "direction": row[3], "level": row[4], "price": row[5],
                "key_level": row[6], "volume_confirmed": bool(row[7]),
                "fired_at": row[8],
            }
        return None
    except Exception as e:
        print(f"⚠️ get_recent_divergence_signal error: {e}")
        return None
    finally:
        conn.close()


def get_recent_signals_for_symbols(symbols: list, within_hours: int) -> list:
    """
    Returns all divergence signals for a list of symbols within the given window.
    Used by /divergence command to build status report in one query.
    """
    if not symbols:
        return []
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=within_hours)
    ).strftime("%Y-%m-%d %H:%M:%S")
    upper = [s.upper() for s in symbols]
    placeholders = ",".join("?" * len(upper))

    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT symbol, timeframe, direction, level, price_at_signal, fired_at
            FROM divergence_signals
            WHERE symbol IN ({placeholders})
              AND fired_at >= ?
            ORDER BY fired_at DESC
            """,
            (*upper, cutoff),
        )
        return [
            {
                "symbol": r[0], "timeframe": r[1], "direction": r[2],
                "level": r[3], "price": r[4], "fired_at": r[5],
            }
            for r in cursor.fetchall()
        ]
    except Exception as e:
        print(f"⚠️ get_recent_signals_for_symbols error: {e}")
        return []
    finally:
        conn.close()


def cleanup_expired_divergence_signals() -> int:
    """Delete divergence_signals rows past their expires_at. Called each scan cycle."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM divergence_signals WHERE expires_at < ?", (now_str,))
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            print(f"🧹 Divergence cleanup: {deleted} expired records removed.")
        return deleted
    except Exception as e:
        print(f"⚠️ cleanup_expired_divergence_signals error: {e}")
        return 0
    finally:
        conn.close()


def get_pro_users_with_watchlists() -> dict:
    """
    Returns {user_id_str: [coin, ...]} for Admin + all active Pro users
    who have at least one coin in their watchlist.
    Single-query, no N+1 DB calls.
    """
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.user_id, w.coin
            FROM users u
            LEFT JOIN user_watchlist w ON u.user_id = w.user_id
            WHERE u.status = 'ACTIVE'
              AND (
                  u.user_id = ?
                  OR (
                      u.tier = 'pro'
                      AND (u.pro_expiry IS NULL OR u.pro_expiry > CURRENT_TIMESTAMP)
                  )
              )
            ORDER BY u.user_id, w.coin
            """,
            (str(ADMIN_USER_ID),),
        )
        result: dict = {}
        for user_id, coin in cursor.fetchall():
            coins = result.setdefault(user_id, [])
            if coin:
                coins.append(coin)
        return result
    except Exception as e:
        print(f"⚠️ get_pro_users_with_watchlists error: {e}")
        return {}
    finally:
        conn.close()
