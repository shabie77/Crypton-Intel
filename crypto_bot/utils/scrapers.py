import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import aiohttp
import logging

logger = logging.getLogger("Scrapers")

try:
    from bs4 import BeautifulSoup
    HAS_BEAUTIFULSOUP = True
except ImportError:
    HAS_BEAUTIFULSOUP = False

def classify_news_importance(title):
    """Returns a dictionary with level, emoji, and a brief impact explanation."""
    title_lower = title.lower()
    critical_kws = (
        "hack", "exploit", "stolen", "ban", "banned", "arrest", 
        "sec sues", "sec charges", "etf approved", "etf rejected",
        "collapse", "insolvent", "emergency", "halted", "lawsuit", "bankruptcy",
    )
    moderate_kws = (
        "binance", "blackrock", "coinbase", "etf", "regulation", 
        "fed rate", "interest rate", "inflation", "cpi", "powell",
        "grayscale", "whale", "ath", "crash", "dump", "pump", "bullish", "bearish",
    )

    if any(kw in title_lower for kw in critical_kws):
        return {
            "level": "CRITICAL",
            "emoji": "🔴",
            "impact": "Major regulatory development, security breach, or systemic shift.",
        }
    if any(kw in title_lower for kw in moderate_kws):
        return {
            "level": "MODERATE",
            "emoji": "🟠",
            "impact": "Significant market shift, institutional activity, or macro update.",
        }
    return {"level": "LOW", "emoji": "🟡", "impact": "General ecosystem update or standard market movement."}

async def fetch_top_news(session):
    """Fetch headlines from the last 24 hours via CoinTelegraph RSS."""
    try:
        url = "https://cointelegraph.com/rss"
        headers = {"User-Agent": "Mozilla/5.0"}
        async with session.get(url, headers=headers, timeout=10) as response:
            if response.status != 200:
                return []
            xml_data = await response.text()
            root = ET.fromstring(xml_data)
            channel = root.find("channel")
            if channel is None: return []

            cutoff = datetime.now(timezone.utc).timestamp() - 86400
            critical_high = []
            general = []

            for item in channel.findall("item")[:40]:
                pubDate = item.find("pubDate")
                if pubDate is None: continue
                try:
                    dt = parsedate_to_datetime(pubDate.text)
                    if dt.timestamp() < cutoff: continue
                except: continue

                title = item.find("title")
                title_text = title.text if title is not None else ""
                importance = classify_news_importance(title_text)
                news_item = {
                    "title": title_text,
                    "level": importance["level"],
                    "emoji": importance["emoji"],
                    "impact": importance["impact"],
                }
                if importance["level"] in ("CRITICAL", "MODERATE"):
                    critical_high.append(news_item)
                else:
                    general.append(news_item)
                if len(critical_high) >= 3: break

            SEVERITY_RANK = {"CRITICAL": 3, "MODERATE": 2, "LOW": 1}
            final_news = sorted(critical_high + general[:3], key=lambda x: SEVERITY_RANK.get(x["level"], 0), reverse=True)
            return final_news[:3]
    except Exception as e:
        logger.error(f"fetch_top_news error: {e}")
        return []

async def fetch_investing_macro_events(session, timeframe_tab):
    """Fetches high-impact macro events from Investing.com."""
    if not HAS_BEAUTIFULSOUP:
        logger.error("BeautifulSoup4 not installed. Cannot fetch Investing.com data.")
        return False, []

    url = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    payload = (
        f"country[]=5&country[]=72&country[]=4&country[]=17&country[]=35&country[]=25&country[]=6&country[]=12"
        f"&country[]=43&timeZone=8&timeFilter=timeOnly&currentTab={timeframe_tab}&submitFilters=1&limit_from=0"
        "&importance=3"
    )

    try:
        async with session.post(url, headers=headers, data=payload, timeout=15) as resp:
            if resp.status != 200: return False, []
            raw_text = await resp.text()
            try:
                data = json.loads(raw_text)
                html_content = data.get("data", "")
            except:
                html_content = raw_text

            if not html_content or len(html_content.strip()) < 50: return False, []
            
            soup = BeautifulSoup(html_content, "html.parser")
            rows = soup.find_all("tr", class_="js-event-item")
            if not rows: return True, []

            events = []
            for tr in rows:
                try:
                    dt_str = tr.get("data-event-datetime")
                    if not dt_str: continue
                    title = tr.find("td", class_="event").text.strip().replace("  ", " ")
                    country = tr.find("td", class_="flagCur").text.strip()[-3:]
                    sentiment = tr.find("td", class_="sentiment")
                    if sentiment and sentiment.get("data-img_key") == "bull3":
                        dt = datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S")
                        events.append({
                            "title": title,
                            "country": country,
                            "date": dt.strftime("%m-%d"),
                            "time": dt.strftime("%H:%M UTC"),
                            "timestamp": int(dt.replace(tzinfo=timezone.utc).timestamp()),
                        })
                except: continue
            return True, events
    except Exception as e:
        logger.error(f"Investing Fetch Error: {e}")
        return False, []

async def fetch_macro_events(session, horizon_days=1):
    """Consolidated helper to fetch High Impact events."""
    now_ts = datetime.now(timezone.utc).timestamp()
    limit_ts = now_ts + (86400 * horizon_days)
    all_events = []
    seen_titles = set()
    headers = {"User-Agent": "Mozilla/5.0"}

    # ForexFactory for This Week
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    try:
        async with session.get(url, headers=headers, timeout=10) as response:
            if response.status == 200:
                xml_data = await response.text()
                root = ET.fromstring(xml_data)
                for item in root.findall("event"):
                    impact = item.find("impact")
                    if impact is not None and impact.text.strip() == "High":
                        title = item.find("title").text.strip()
                        country = item.find("country").text.strip()
                        date_str = item.find("date").text.strip()
                        time_str = item.find("time").text.strip()
                        try:
                            event_dt = datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
                            event_ts = event_dt.timestamp()
                            if now_ts <= event_ts <= limit_ts:
                                all_events.append({
                                    "title": title, "country": country, 
                                    "date": event_dt.strftime("%m-%d"), "time": event_dt.strftime("%H:%M UTC"),
                                    "timestamp": event_ts
                                })
                                seen_titles.add(f"{title}_{event_ts}")
                        except: continue
    except Exception as e:
        logger.error(f"ForexFactory Fetch Error: {e}")

    # Investing.com for Next Week if horizon > 1
    if horizon_days > 1:
        success, next_week = await fetch_investing_macro_events(session, "nextWeek")
        if success:
            for ev in next_week:
                uid = f"{ev['title']}_{ev['timestamp']}"
                if ev['timestamp'] <= limit_ts and uid not in seen_titles:
                    all_events.append(ev)

    return {
        "success": len(all_events) > 0,
        "events": sorted(all_events, key=lambda x: x["timestamp"])
    }
