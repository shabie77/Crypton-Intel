"""
digest/liquidation_engine.py

Fetches 24H liquidation data.
Currently: local SQLite fallback only.
Future: plug in CoinGlass or Binance liquidation feed here.
"""
import asyncio
import database
from datetime import datetime, timezone, timedelta


async def fetch_liquidation_data() -> dict:
    """
    Returns liquidation dict: {total, longs, shorts}
    Falls back to empty dict (DATA UNAVAILABLE) if no records in DB.
    """
    try:
        iso_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).strftime("%Y-%m-%d %H:%M:%S")

        liq_stats = await asyncio.to_thread(
            database.get_24h_liquidation_stats, iso_cutoff
        )

        # If stats are empty or zero, return empty dict
        if not liq_stats or liq_stats.get("total", 0) == 0:
            return {}

        return liq_stats

    except Exception as e:
        print(f"⚠️ Liquidation fetch failed: {e}")
        return {}
