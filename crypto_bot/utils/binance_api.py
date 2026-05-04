import aiohttp
import asyncio
from datetime import datetime, timezone

async def fetch_with_backoff(session, url, params=None, max_retries=3):
    """Fetch from API with exponential backoff."""
    for i in range(max_retries):
        try:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    wait = 2 ** (i + 1)
                    await asyncio.sleep(wait)
                else:
                    return None
        except Exception:
            await asyncio.sleep(2 ** i)
    return None

def symbol_to_pair(symbol):
    return f"{symbol.upper()}USDT"

async def fetch_sma_trend(session, symbol, interval, period):
    pair = symbol_to_pair(symbol)
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": pair, "interval": interval, "limit": period + 1}
    data = await fetch_with_backoff(session, url, params)
    if not data: return 0.0
    closes = [float(c[4]) for c in data]
    if len(closes) < period: return 0.0
    return sum(closes[-period:]) / period

async def fetch_coin_liquidations(session, symbol):
    # Stub for coinglass or other API
    return {"total_liq": 0, "long_liq": 0, "short_liq": 0}

async def fetch_symbol_snapshot(session, symbol, config):
    from utils.state import _MARKET_SNAPSHOT_CACHE, _MARKET_CACHE_LOCK
    now = datetime.now(timezone.utc)
    async with _MARKET_CACHE_LOCK:
        if symbol in _MARKET_SNAPSHOT_CACHE:
            val, ts = _MARKET_SNAPSHOT_CACHE[symbol]
            if (now - ts).total_seconds() < 30:
                return val

    pair = symbol_to_pair(symbol)
    url = "https://api.binance.com/api/v3/klines"
    
    # Fetch 1D candles for daily trend context
    params_1d = {"symbol": pair, "interval": "1d", "limit": 50}
    candles_1d = await fetch_with_backoff(session, url, params_1d)
    
    # Fetch 4H candles for BOS structure and primary trend
    params_4h = {"symbol": pair, "interval": "4h", "limit": 55}
    candles_4h = await fetch_with_backoff(session, url, params_4h)
    
    if not candles_1d or not candles_4h: return None
    
    close_prices_1d = [float(c[4]) for c in candles_1d]
    volumes_1d = [float(c[5]) for c in candles_1d]
    current_price = close_prices_1d[-1]
    prev_price = close_prices_1d[-2] if len(close_prices_1d) > 1 else current_price
    
    sma_50_1d = sum(close_prices_1d[-50:]) / min(50, len(close_prices_1d))
    trend_1d = "BULL" if current_price > sma_50_1d else "BEAR"
    
    current_vol = volumes_1d[-1]
    avg_vol_20 = sum(volumes_1d[-21:-1]) / 20 if len(volumes_1d) >= 21 else (sum(volumes_1d[:-1]) / len(volumes_1d[:-1]) if len(volumes_1d) > 1 else current_vol)
    vol_ratio = current_vol / avg_vol_20 if avg_vol_20 else 1.0
    
    change_24h = ((current_price - prev_price) / prev_price * 100) if prev_price else 0.0
    
    closes_4h = [float(c[4]) for c in candles_4h]
    highs_4h = [float(c[2]) for c in candles_4h]
    lows_4h = [float(c[3]) for c in candles_4h]
    
    sma_50_4h = sum(closes_4h[-50:]) / min(50, len(closes_4h))
    trend_4h = "BULL" if current_price > sma_50_4h else "BEAR"
    
    from indicators.rsi import calculate_rsi
    rsi = calculate_rsi(closes_4h)
    
    trend_emoji = "🟢" if current_price > sma_50_1d else "🔴"
    
    snapshot = {
        "symbol": symbol,
        "price": current_price,
        "rsi": rsi,
        "vol_ratio": vol_ratio,
        "change_24h": change_24h,
        "trend_emoji": trend_emoji,
        "sma50": sma_50_4h,
        "trend_4h": trend_4h,
        "trend_1d": trend_1d,
        "closes": closes_4h,
        "highs": highs_4h,
        "lows": lows_4h
    }
    
    async with _MARKET_CACHE_LOCK:
        _MARKET_SNAPSHOT_CACHE[symbol] = (snapshot, now)
    
    return snapshot
