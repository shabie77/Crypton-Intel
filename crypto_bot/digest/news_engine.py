"""
digest/news_engine.py

Fetches top crypto news from RSS feeds.
Classifies headlines using deterministic keyword matching.
No AI dependency.
"""
import asyncio
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from utils.config import DATA_DIR

CACHE_FILE = os.path.join(DATA_DIR, "news_cache.json")
CACHE_TTL_SECONDS = 7200  # 2 hours

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://cryptonews.com/news/feed/",
]

CRITICAL_KEYWORDS = [
    "hack", "exploit", "sec", "ban", "crash", "depeg",
    "collapsed", "insolvent", "frozen", "arrested", "lawsuit filed",
    "emergency", "delisted", "shutdown", "seizure"
]

MODERATE_KEYWORDS = [
    "etf", "inflows", "outflows", "whale", "liquidated $",
    "fed", "cpi", "rate cut", "fomc", "inflation",
    "earnings", "partnership broken", "investigation"
]

LOW_KEYWORDS = [
    "partnership", "listing", "upgrade", "integration",
    "roadmap", "update", "expansion", "milestone"
]


def classify_headline(headline: str) -> tuple[str, str]:
    """Returns (tier, emoji). Deterministic keyword matching."""
    h = headline.lower()
    for kw in CRITICAL_KEYWORDS:
        if kw in h:
            return "CRITICAL", "🔴"
    for kw in MODERATE_KEYWORDS:
        if kw in h:
            return "MODERATE", "🟠"
    return "LOW", "🟢"


def _load_cache() -> dict | None:
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return data.get("headlines", [])
    except Exception as e:
        print(f"⚠️ News load_cache error: {e}")
    return None


def _save_cache(headlines: list):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "headlines": headlines
        }, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CACHE_FILE)


async def fetch_news(session) -> list[dict]:
    """
    Returns up to 3 classified headlines.
    Falls back to cache or empty list silently.
    """
    cached = await asyncio.to_thread(_load_cache)
    if cached is not None:
        return cached

    headlines = []
    for feed_url in RSS_FEEDS:
        try:
            async with session.get(feed_url, timeout=5) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                root = ET.fromstring(text)
                items = root.findall(".//item")
                for item in items[:5]:
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue
                    title = title_el.text.strip()
                    if len(title) < 10:
                        continue
                    tier, emoji = classify_headline(title)
                    headlines.append({
                        "title": title,
                        "tier": tier,
                        "emoji": emoji
                    })
                if headlines:
                    break  # Stop after first successful feed
        except Exception as e:
            print(f"⚠️ News feed error for {feed_url}: {e}")
            continue

    # Sort: CRITICAL first, then MODERATE, then LOW
    tier_order = {"CRITICAL": 0, "MODERATE": 1, "LOW": 2}
    headlines.sort(key=lambda x: tier_order.get(x["tier"], 3))

    # Ensure one of each tier if possible
    result = []
    seen_tiers = set()
    for h in headlines:
        if h["tier"] not in seen_tiers:
            result.append(h)
            seen_tiers.add(h["tier"])
        if len(result) == 3:
            break

    # Fill remaining slots if we don't have 3 unique tiers
    if len(result) < 3:
        for h in headlines:
            if h not in result:
                result.append(h)
            if len(result) == 3:
                break

    if result:
        await asyncio.to_thread(_save_cache, result)

    return result
