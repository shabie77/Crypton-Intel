import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database
from indicators.bos import find_swing_high_indices, find_swing_low_indices
from indicators.rsi import calculate_rsi_series


class DivergenceHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = database.db_users.DB_FILE
        self.original_admin = database.db_users.ADMIN_USER_ID
        database.db_users.DB_FILE = os.path.join(self.temp_dir.name, "test_subscribers.db")
        database.db_users.ADMIN_USER_ID = 999999
        database.initialise_database()

    def tearDown(self):
        database.db_users.DB_FILE = self.original_db
        database.db_users.ADMIN_USER_ID = self.original_admin
        self.temp_dir.cleanup()

    def test_rsi_series_alignment(self):
        prices = list(range(1, 31))
        series = calculate_rsi_series(prices, period=14)
        self.assertEqual(len(series), len(prices))
        self.assertEqual(series[:13], [None] * 13)
        self.assertIsNone(series[13])
        self.assertIsNotNone(series[-1])

    def test_swing_functions_return_indices(self):
        lows = [10, 9, 7, 9, 10, 11, 8, 10, 12]
        highs = [10, 11, 14, 12, 10, 13, 16, 14, 13]
        low_indices = find_swing_low_indices(lows)
        high_indices = find_swing_high_indices(highs)
        self.assertTrue(all(isinstance(idx, int) for idx in low_indices))
        self.assertTrue(all(isinstance(idx, int) for idx in high_indices))
        self.assertEqual(low_indices, [2, 6])
        self.assertEqual(high_indices, [2, 6])

    def test_get_pro_users_with_watchlists_includes_admin_and_active_pros(self):
        now = datetime.now(timezone.utc)
        future = (now + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        past = (now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

        database.add_user(111, "pro_user", "Pro User")
        database.add_user(222, "expired_user", "Expired User")
        database.add_user(333, "free_user", "Free User")
        database.save_watchlist(999999, ["BTC"])
        database.save_watchlist(111, ["ETH", "SOL"])
        database.save_watchlist(222, ["XRP"])
        database.save_watchlist(333, ["ADA"])

        conn = database.db_users._connect()
        try:
            conn.execute("UPDATE users SET tier = 'pro', pro_expiry = ? WHERE user_id = ?", (future, "111"))
            conn.execute("UPDATE users SET tier = 'pro', pro_expiry = ? WHERE user_id = ?", (past, "222"))
            conn.execute("UPDATE users SET tier = 'free' WHERE user_id = ?", ("333",))
            conn.commit()
        finally:
            conn.close()

        result = database.get_pro_users_with_watchlists()
        self.assertEqual(result["999999"], ["BTC"])
        self.assertEqual(result["111"], ["ETH", "SOL"])
        self.assertNotIn("222", result)
        self.assertNotIn("333", result)

    def test_weekly_divergence_uses_168h_dedup(self):
        database.create_divergence_signal("BTC", "1w", "BULLISH", 1, 100.0, None, False)
        recent = database.get_recent_divergence_signal("BTC", "1w", "BULLISH", 1, 0)
        self.assertIsNotNone(recent)


if __name__ == "__main__":
    unittest.main()
