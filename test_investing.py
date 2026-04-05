import asyncio
import aiohttp
from bs4 import BeautifulSoup
import json
import datetime

async def fetch():
    url = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    # US (5), EU (72), UK (4), Ger (17), Jpn (12), Aus (25), Can (6), Swz (12)
    payload = "country[]=5&country[]=72&country[]=4&country[]=17&country[]=12&country[]=25&country[]=6&timeZone=8&timeFilter=timeOnly&currentTab=nextWeek&submitFilters=1&limit_from=0&importance=3"
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=payload) as resp:
            raw_text = await resp.text()
            try:
                data = json.loads(raw_text)
                html = data.get("data", "")
            except Exception as e:
                print("Failed to parse JSON:", e)
                html = raw_text
                
            soup = BeautifulSoup(html, "html.parser")
            events = []
            
            for tr in soup.find_all("tr", class_="js-event-item"):
                try:
                    datetime_str = tr.get("data-event-datetime") 
                    title_elem = tr.find("td", class_="event")
                    title = title_elem.text.strip() if title_elem else "Unknown"
                    country_td = tr.find("td", class_="flagCur")
                    country = country_td.text.strip() if country_td else "Unknown"

                    sentiment_td = tr.find("td", class_="sentiment")
                    if sentiment_td and sentiment_td.get("data-img_key") == "bull3":
                        events.append({"time": datetime_str, "title": title, "country": country})
                except Exception as e:
                    print(e)
            print(f"Extracted {len(events)} High-Impact events from Investing.com")
            for e in events:
                print(e)

asyncio.run(fetch())
