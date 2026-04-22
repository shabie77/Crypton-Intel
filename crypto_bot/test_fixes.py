import database
import os

database.init_db()
print("DB initialized")

user_id = "test_user_id"
payment_id = "pay_test123"

# Test 1: Record payment (should be True)
inserted1 = database.record_payment(payment_id, user_id, "payment.captured", 249, "INR")
print(f"First insert: {inserted1}")

# Test 2: Record payment again (should be False)
inserted2 = database.record_payment(payment_id, user_id, "payment.captured", 249, "INR")
print(f"Second insert: {inserted2}")

# Test 3: Upgrade user (should unpause alerts)
# Setup: Add user, add paused alert
conn = database._connect()
conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
conn.execute("INSERT INTO price_alerts (user_id, coin, target, direction, is_active, is_paused) VALUES (?, 'BTCUSDT', 50000, 'ABOVE', 1, 1)", (user_id,))
conn.commit()
conn.close()

database.upgrade_user(user_id, "pro_monthly", 249, "INR")

conn = database._connect()
cursor = conn.cursor()
cursor.execute("SELECT is_paused FROM price_alerts WHERE user_id = ?", (user_id,))
is_paused = cursor.fetchone()[0]
conn.close()
print(f"Alert is_paused after upgrade: {is_paused}")
