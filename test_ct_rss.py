import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

async def test_cointelegraph():
    async with aiohttp.ClientSession() as session:
        url = "https://cointelegraph.com/rss"
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            text = await response.text()
            root = ET.fromstring(text)
            
            # RSS items are inside <channel>
            channel = root.find('channel')
            if not channel:
                print("No channel found")
                return
                
            for item in channel.findall('item')[:5]:
                title = item.find('title').text
                pubDate = item.find('pubDate').text
                dt = parsedate_to_datetime(pubDate)
                print(f"Title: {title[:50]} | Date: {dt}")

if __name__ == "__main__":
    asyncio.run(test_cointelegraph())
