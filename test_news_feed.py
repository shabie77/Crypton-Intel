import asyncio
import aiohttp

async def test_news_apis():
    async with aiohttp.ClientSession() as session:
        # Test 1: CryptoCompare without sorting
        url1 = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
        async with session.get(url1) as response1:
            data = await response1.json()
            print(f"CryptoCompare Base URL: {data.get('Message', 'Success')}")
            
        # Test 2: CoinTelegraph RSS
        url2 = "https://cointelegraph.com/rss"
        try:
            async with session.get(url2, headers={"User-Agent": "Mozilla/5.0"}) as response2:
                text = await response2.text()
                print(f"CoinTelegraph RSS Status: {response2.status}")
                if response2.status == 200:
                    print(f"CoinTelegraph Sample: {text[:200]}")
        except Exception as e:
            print(f"CoinTelegraph failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_news_apis())
