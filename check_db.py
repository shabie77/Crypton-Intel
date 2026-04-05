import sqlite3
import os
from datetime import datetime, timezone

DB_FILE = "c:/TRADING BRAIN/crypto_bot/subscribers.db"

def check():
    if not os.path.exists(DB_FILE):
        print(f"Error: {DB_FILE} does not exist.")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Check total liquidations today
    today_str = "2026-04-04"
    cursor.execute("SELECT COUNT(*), SUM(value) FROM liquidations WHERE timestamp LIKE ?", (f"{today_str}%",))
    count, total_value = cursor.fetchone()
    print(f"Total liquidations logged for {today_str}: {count} events, Total Value: ${total_value if total_value else 0:,.2f}")

    # Check high-value liquidations (> 50,000) for tracked coins
    tracked_coins = ["BTC", "ETH", "SOL", "XRP", "ADA"]
    for coin in tracked_coins:
        cursor.execute("SELECT COUNT(*), MAX(value) FROM liquidations WHERE symbol = ? AND timestamp LIKE ?", (coin, f"{today_str}%"))
        c_count, max_v = cursor.fetchone()
        print(f" - {coin}: {c_count} events, Max Value: ${max_v if max_v else 0:,.2f}")

    # Check alerts log
    cursor.execute("SELECT COUNT(*) FROM alerts_log WHERE alert_type = 'liquidation' AND timestamp LIKE ?", (f"{today_str}%",))
    alert_count = cursor.fetchone()[0]
    print(f"Total liquidation alerts sent today: {alert_count}")

    # Show some recent liquidations to verify it's logging ANYTHING
    print("\nRecent 5 liquidations (any symbol):")
    cursor.execute("SELECT symbol, side, value, timestamp FROM liquidations ORDER BY id DESC LIMIT 5")
    for row in cursor.fetchall():
        print(row)

    conn.close()

if __name__ == "__main__":
    check()
