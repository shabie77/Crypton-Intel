"""
digest/macro_engine.py

Fetches high-impact macro events from ForexFactory.
Silent degradation: returns None on any failure.
Formatter must omit section if None is returned.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from utils.config import DATA_DIR

CACHE_FILE = os.path.join(DATA_DIR, "macro_cache.json")
CACHE_TTL_SECONDS = 14400  # 4 hours

HIGH_IMPACT_KEYWORDS = [
    "cpi", "fomc", "fed", "interest rate", "gdp", "nfp",
    "non-farm", "inflation", "rate decision", "powell",
    "ecb", "unemployment", "retail sales"
]


def _load_cache() -> dict | None:
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return data.get("event", None)
    except Exception as e:
        print(f"⚠️ Macro load_cache error: {e}")
    return None


def _save_cache(event: dict | None):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "event": event
        }, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CACHE_FILE)


async def fetch_macro_event(session) -> dict | None:
    """
    Returns the next high-impact macro event or None.
    Silent degradation on scrape failure.
    """
    cached = await asyncio.to_thread(_load_cache)
    if cached is not None:
        return cached if cached else None

    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        async with session.get(url, timeout=5) as resp:
            if resp.status != 200:
                await asyncio.to_thread(_save_cache, None)
                return None
            events = await resp.json(content_type=None)

        now = datetime.now(timezone.utc)
        for ev in events:
            impact = ev.get("impact", "").lower()
            title = ev.get("title", "").lower()
            date_str = ev.get("date", "")
            currency = ev.get("country", "").upper()

            # Only USD high-impact events
            if impact != "high" or currency != "USD":
                continue

            # Check if it's a relevant macro event
            if not any(kw in title for kw in HIGH_IMPACT_KEYWORDS):
                continue

            # Parse the event time
            try:
                ev_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                continue

            # Only events in the next 8 hours
            hours_away = (ev_time - now).total_seconds() / 3600
            if -12 <= hours_away <= 8:
                if hours_away < 0:
                    timing = f"fired {abs(hours_away):.1f}h ago ({ev_time.strftime('%H:%M UTC')})"
                    note = "High-impact event already released. Monitor ongoing market reaction."
                else:
                    timing = f"in {hours_away:.1f}h ({ev_time.strftime('%H:%M UTC')})"
                    note = "Expect volatility. Secure open positions before release."

                result = {
                    "title": ev.get("title", "High-impact event"),
                    "time": timing,
                    "hours_away": round(hours_away, 1),
                    "note": note
                }
                await asyncio.to_thread(_save_cache, result)
                return result

        await asyncio.to_thread(_save_cache, None)
        return None

    except Exception as e:
        # Silent degradation — never crash digest for macro
        print(f"⚠️ Macro fetch error: {e}")
        return None
