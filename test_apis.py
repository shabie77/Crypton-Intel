import asyncio
import aiohttp
from datetime import datetime, timezone

async def test_news():
    async with aiohttp.ClientSession() as session:
        url = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=latest"
        async with session.get(url) as response:
            data = await response.json()
            if not data or data.get("Type") != 100:
                print(f"Failed CryptoCompare. Response: {data}")
                return
            
            print("CryptoCompare Success.")

async def test_macro():
    async with aiohttp.ClientSession() as session:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/xml,application/xml"
        }
        # Try both URLs: ff_calendar_thisweek.xml and API format
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
        async with session.get(url, headers=headers) as response:
            xml_data = await response.text()
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_data)
            
            for item in root.findall('event'):
                impact = item.find('impact').text
                title = item.find('title').text
                if impact != 'High':
                    continue
                date_str = item.find('date').text
                time_str = item.find('time').text
                
                print(f"High Impact Event: {title} | date='{date_str}' time='{time_str}'")
                
                try:
                    dt_str = f"{date_str.strip()} {time_str.strip()}"
                    event_dt = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc)
                    print(f"  -> Parsed successfully: {event_dt}")
                except Exception as e:
                    print(f"  -> Failed to parse: {dt_str} Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_news())
    asyncio.run(test_macro())
