import sqlite3
import os

DB_FILE = "subscribers.db"

def test_tiers():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, username,
               CASE
                   WHEN tier = 'pro' AND (pro_expiry IS NULL OR pro_expiry > CURRENT_TIMESTAMP) THEN 'pro'
                   ELSE 'free'
               END as effective_tier
        FROM users
        WHERE user_id = '1120431469'
    """)
    row = cursor.fetchone()
    print(f"Effective Tier Check: {row}")
    conn.close()

if __name__ == "__main__":
    test_tiers()
