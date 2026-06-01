import asyncio
from datetime import datetime, timezone

async def fetch_with_backoff(session, url, params=None, max_retries=3):
    """Fetch from API with exponential backoff."""
    for i in range(max_retries):
        try:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429 or response.status >= 500:
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
    if not data:
        return 0.0
    closes = [float(c[4]) for c in data]
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period


async def fetch_coin_liquidations(session, symbol):
    # Stub for coinglass or other API
    return {"total_liq": 0, "long_liq": 0, "short_liq": 0}


# ── Binance pair validator ────────────────────────────────────────────────────

_VALID_PAIRS_CACHE: set = set()
_VALID_PAIRS_FETCHED_AT: datetime | None = None
_VALID_PAIRS_TTL_SECONDS = 3600  # refresh every hour


async def get_valid_binance_pairs(session) -> set:
    """
    Returns a set of base assets that have an active USDT pair on Binance.
    e.g. {"BTC", "ETH", "SOL", "XRP", ...}
    Cached for 1 hour to avoid repeated API calls.
    """
    global _VALID_PAIRS_CACHE, _VALID_PAIRS_FETCHED_AT
    now = datetime.now(timezone.utc)

    if _VALID_PAIRS_CACHE and _VALID_PAIRS_FETCHED_AT:
        age = (now - _VALID_PAIRS_FETCHED_AT).total_seconds()
        if age < _VALID_PAIRS_TTL_SECONDS:
            return _VALID_PAIRS_CACHE

    url = "https://api.binance.com/api/v3/exchangeInfo"
    try:
        data = await fetch_with_backoff(session, url)
        if data and "symbols" in data:
            pairs = {
                s["baseAsset"]
                for s in data["symbols"]
                if s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
            }
            _VALID_PAIRS_CACHE = pairs
            _VALID_PAIRS_FETCHED_AT = now
            return pairs
    except Exception as e:
        print(f"⚠️ get_valid_binance_pairs error: {e}")

    return _VALID_PAIRS_CACHE  # return stale cache on failure


# ── Multi-timeframe snapshot ──────────────────────────────────────────────────

async def fetch_symbol_snapshot(session, symbol, config):
    from utils.state import (
        _IN_FLIGHT_FETCHES,
        _IN_FLIGHT_FETCHES_LOCK,
        _MARKET_CACHE_LOCK,
        _MARKET_SNAPSHOT_CACHE,
        MARKET_SNAPSHOT_TTL_SECONDS,
    )
    from indicators.rsi import calculate_rsi
    from indicators.ema import calculate_ema

    now = datetime.now(timezone.utc)
    async with _MARKET_CACHE_LOCK:
        if symbol in _MARKET_SNAPSHOT_CACHE:
            val, ts = _MARKET_SNAPSHOT_CACHE[symbol]
            if (now - ts).total_seconds() < MARKET_SNAPSHOT_TTL_SECONDS:
                return val

    async def _do_fetch():
        pair = symbol_to_pair(symbol)
        url = "https://api.binance.com/api/v3/klines"

        # Fetch all three timeframes in parallel
        params_4h = {"symbol": pair, "interval": "4h", "limit": 220}
        params_1d = {"symbol": pair, "interval": "1d", "limit": 100}
        params_1w = {"symbol": pair, "interval": "1w", "limit": 100}

        candles_4h, candles_1d, candles_1w = await asyncio.gather(
            fetch_with_backoff(session, url, params_4h),
            fetch_with_backoff(session, url, params_1d),
            fetch_with_backoff(session, url, params_1w),
            return_exceptions=True
        )

        # 4H and 1D are required — return None if either fails
        if isinstance(candles_4h, Exception) or not candles_4h:
            return None
        if isinstance(candles_1d, Exception) or not candles_1d:
            return None
        # 1W is optional — Pro uses it but Free Digest will not fail without it
        if isinstance(candles_1w, Exception):
            candles_1w = []

        # ── 4H calculations ───────────────────────────────────────────────────────
        closes_4h  = [float(c[4]) for c in candles_4h]
        highs_4h   = [float(c[2]) for c in candles_4h]
        lows_4h    = [float(c[3]) for c in candles_4h]
        volumes_4h = [float(c[5]) for c in candles_4h]

        rsi_4h     = calculate_rsi(closes_4h)
        ema_21_4h  = calculate_ema(closes_4h, 21)
        ema_50_4h  = calculate_ema(closes_4h, 50)
        ema_200_4h = calculate_ema(closes_4h, 200)

        sma_50_4h = sum(closes_4h[-50:]) / min(50, len(closes_4h))
        trend_4h  = "BULL" if closes_4h[-1] > sma_50_4h else "BEAR"

        # ── 1D calculations ───────────────────────────────────────────────────────
        closes_1d  = [float(c[4]) for c in candles_1d]
        highs_1d   = [float(c[2]) for c in candles_1d]
        lows_1d    = [float(c[3]) for c in candles_1d]
        volumes_1d = [float(c[5]) for c in candles_1d]

        rsi_1d = calculate_rsi(closes_1d)

        current_price = closes_1d[-1]
        prev_price    = closes_1d[-2] if len(closes_1d) > 1 else current_price
        change_24h    = ((current_price - prev_price) / prev_price * 100) if prev_price else 0.0

        current_vol = volumes_1d[-1]
        avg_vol_20  = (
            sum(volumes_1d[-21:-1]) / 20
            if len(volumes_1d) >= 21
            else (
                sum(volumes_1d[:-1]) / len(volumes_1d[:-1])
                if len(volumes_1d) > 1
                else current_vol
            )
        )
        vol_ratio = current_vol / avg_vol_20 if avg_vol_20 else 1.0

        sma_50_1d = sum(closes_1d[-50:]) / min(50, len(closes_1d))
        trend_1d  = "BULL" if current_price > sma_50_1d else "BEAR"

        # ── 1W calculations ───────────────────────────────────────────────────────
        if candles_1w:
            closes_1w  = [float(c[4]) for c in candles_1w]
            highs_1w   = [float(c[2]) for c in candles_1w]
            lows_1w    = [float(c[3]) for c in candles_1w]
            volumes_1w = [float(c[5]) for c in candles_1w]
            rsi_1w    = calculate_rsi(closes_1w)
            sma_50_1w = sum(closes_1w[-50:]) / min(50, len(closes_1w))
            trend_1w  = "BULL" if closes_1w[-1] > sma_50_1w else "BEAR"
        else:
            closes_1w  = []
            highs_1w   = []
            lows_1w    = []
            volumes_1w = []
            rsi_1w    = 50.0
            trend_1w  = "NEUTRAL"

        trend_emoji = "🟢" if trend_1d == "BULL" else "🔴"

        snapshot = {
            # ── Core keys (Free Digest backward compatible) ──
            "symbol":      symbol,
            "price":       current_price,
            "rsi":         rsi_4h,        # Free Digest reads this single RSI value
            "vol_ratio":   vol_ratio,
            "change_24h":  change_24h,
            "trend_emoji": trend_emoji,
            "sma50":       sma_50_4h,
            "trend_4h":    trend_4h,
            "trend_1d":    trend_1d,
            "closes":      closes_4h,     # Free Digest backward compatibility
            "highs":       highs_4h,      # Free Digest backward compatibility
            "lows":        lows_4h,       # Free Digest backward compatibility

            # ── Multi-TF RSI (Pro Digest) ──
            "rsi_4h": rsi_4h,
            "rsi_1d": rsi_1d,
            "rsi_1w": rsi_1w,

            # ── EMA values (Pro Digest structure) ──
            "ema_21_4h":  ema_21_4h,
            "ema_50_4h":  ema_50_4h,
            "ema_200_4h": ema_200_4h,

            # ── 1D OHLCV (Pro structure + levels) ──
            "closes_1d": closes_1d,
            "highs_1d":  highs_1d,
            "lows_1d":   lows_1d,

            # ── 1W OHLCV (Pro weekly structure) ──
            "closes_1w":  closes_1w,
            "highs_1w":   highs_1w,
            "lows_1w":    lows_1w,
            "trend_1w":   trend_1w,

            # ── Volume arrays per TF (divergence engine) ──
            "volumes_4h": volumes_4h,
            "volumes_1d": volumes_1d,
            "volumes_1w": volumes_1w,
        }

        async with _MARKET_CACHE_LOCK:
            _MARKET_SNAPSHOT_CACHE[symbol] = (snapshot, datetime.now(timezone.utc))

        return snapshot

    async with _IN_FLIGHT_FETCHES_LOCK:
        task = _IN_FLIGHT_FETCHES.get(symbol)
        if task is None:
            task = asyncio.create_task(_do_fetch())
            _IN_FLIGHT_FETCHES[symbol] = task

    try:
        result = await task
    finally:
        async with _IN_FLIGHT_FETCHES_LOCK:
            if _IN_FLIGHT_FETCHES.get(symbol) is task:
                _IN_FLIGHT_FETCHES.pop(symbol, None)
    return result
