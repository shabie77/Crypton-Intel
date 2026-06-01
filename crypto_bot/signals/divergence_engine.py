"""
signals/divergence_engine.py

RSI Divergence Alert Engine — 3-Tier Alert System
Scans 4H (primary), 1D (primary), 1W (rare) timeframes every 30 minutes.
Routes Level 1/2/3 alerts to Admin + Pro users via their existing watchlists.

Tier logic:
    Level 1 — RSI divergence detected
    Level 2 — RSI divergence + price at key S/R level
    Level 3 — RSI divergence + key S/R level + volume declining at 2nd swing (exhaustion)

Cooldowns (DB-backed, survives restarts):
    4H  →  8 hours per level (L1/L2/L3 each tracked separately)
    1D  →  48 hours per level
    1W  →  No cooldown — naturally rate-limited by weekly candle; 7-day dedup via DB
"""
import asyncio
import traceback
from datetime import datetime, timedelta, timezone

import database
from indicators.rsi import calculate_rsi_series, detect_rsi_divergence
from indicators.bos import find_swing_low_indices, find_swing_high_indices
from indicators.levels import calculate_swing_levels, calculate_zone_buffer
from signals.divergence_formatter import format_divergence_alert, dispatch_divergence_alert
from utils.binance_api import fetch_symbol_snapshot
from utils.config import load_config, ADMIN_USER_ID


# ── Cooldown map (minutes per level per timeframe) ────────────────────────────
_COOLDOWN_MINUTES = {
    "4h": {1: 480,  2: 480,  3: 480},    # 8 hours
    "1d": {1: 2880, 2: 2880, 3: 2880},   # 48 hours
    "1w": {1: 0,    2: 0,    3: 0},       # No cooldown (7-day dedup via DB)
}

# ── Minimum swing % per timeframe (larger = fewer but more significant swings) ─
_MIN_PCT = {
    "4h": 0.008,   # 0.8%
    "1d": 0.015,   # 1.5%
    "1w": 0.025,   # 2.5%
}

_SCAN_INTERVAL_MINUTES = 30


def _next_half_hour_boundary(now: datetime) -> datetime:
    minute_slot = 30 if now.minute < 30 else 60
    if minute_slot == 60:
        base = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        base = now.replace(minute=30, second=0, microsecond=0)
    if base <= now:
        return base + timedelta(minutes=_SCAN_INTERVAL_MINUTES)
    return base


# ── Entry Point ───────────────────────────────────────────────────────────────

async def start_divergence_loop(session):
    """
    Entry point called from main.py as a background asyncio task.
    Waits 15s on startup so other services can initialise first.
    """
    print("📡 RSI Divergence Scanner starting...")
    await asyncio.sleep(15)
    scheduled_for = datetime.now(timezone.utc)

    while True:
        cycle_started_at = datetime.now(timezone.utc)
        alerts_fired = 0
        symbols_scanned = 0
        overran = False
        try:
            config = await load_config()
            symbols_scanned, alerts_fired = await _run_scan_cycle(session, config)
        except Exception as e:
            print(f"❌ Divergence scanner outer error: {e}")
            traceback.print_exc()

        cycle_finished_at = datetime.now(timezone.utc)
        duration = (cycle_finished_at - cycle_started_at).total_seconds()
        next_slot = _next_half_hour_boundary(scheduled_for)
        while next_slot <= cycle_finished_at:
            next_slot += timedelta(minutes=_SCAN_INTERVAL_MINUTES)
            overran = True
        sleep_for = max(0.0, (next_slot - cycle_finished_at).total_seconds())
        print(
            "📡 Divergence cycle complete | "
            f"start={cycle_started_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | "
            f"end={cycle_finished_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | "
            f"duration={duration:.1f}s | symbols={symbols_scanned} | alerts={alerts_fired} | "
            f"overran={'yes' if overran else 'no'} | next={next_slot.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        scheduled_for = next_slot
        await asyncio.sleep(sleep_for)


# ── Scan Cycle ────────────────────────────────────────────────────────────────

async def _run_scan_cycle(session, config):
    """One full scan: collects watchlists, deduplicates symbols, scans each."""

    # Cleanup stale signal records
    await asyncio.to_thread(database.cleanup_expired_divergence_signals)

    # Get all Pro users + Admin with their existing watchlist coins
    all_watchlists = await asyncio.to_thread(database.get_pro_users_with_watchlists)

    # Always ensure admin has coins to scan even if watchlist is empty
    admin_str = str(ADMIN_USER_ID)
    if not all_watchlists.get(admin_str):
        default_coins = config.get("tracked_coins", ["BTC", "ETH", "SOL"])
        all_watchlists[admin_str] = default_coins

    # Unique symbol set across all users
    unique_symbols: set = set()
    for coins in all_watchlists.values():
        unique_symbols.update(c.upper() for c in coins)

    print(f"🔍 Divergence scan cycle — {len(unique_symbols)} unique symbol(s)")
    alerts_fired = 0

    for symbol in sorted(unique_symbols):
        try:
            alerts_fired += await _scan_symbol(session, symbol, config, all_watchlists)
            await asyncio.sleep(2)   # Spacing between symbols
        except Exception as e:
            print(f"⚠️ Divergence scan error [{symbol}]: {e}")
            continue
    return len(unique_symbols), alerts_fired


# ── Symbol Scanner ────────────────────────────────────────────────────────────

async def _scan_symbol(session, symbol: str, config: dict, all_watchlists: dict):
    """Fetch snapshot (cache-first) and scan each timeframe."""

    snapshot = await fetch_symbol_snapshot(session, symbol, config)
    if not snapshot:
        return 0

    # ── Timeframe definitions ─────────────────────────────────────────────────
    # 4H: uses backward-compat snapshot keys "closes"/"highs"/"lows"
    tf_configs = [
        {
            "tf":      "4h",
            "closes":  snapshot.get("closes", []),
            "highs":   snapshot.get("highs", []),
            "lows":    snapshot.get("lows", []),
            "volumes": snapshot.get("volumes_4h", []),
        },
        {
            "tf":      "1d",
            "closes":  snapshot.get("closes_1d", []),
            "highs":   snapshot.get("highs_1d", []),
            "lows":    snapshot.get("lows_1d", []),
            "volumes": snapshot.get("volumes_1d", []),
        },
        {
            "tf":      "1w",
            "closes":  snapshot.get("closes_1w", []),
            "highs":   snapshot.get("highs_1w", []),
            "lows":    snapshot.get("lows_1w", []),
            "volumes": snapshot.get("volumes_1w", []),
        },
    ]

    alerts_fired = 0
    for tf_cfg in tf_configs:
        tf      = tf_cfg["tf"]
        closes  = tf_cfg["closes"]
        highs   = tf_cfg["highs"]
        lows    = tf_cfg["lows"]
        volumes = tf_cfg["volumes"]

        if len(closes) < 30 or len(highs) < 30 or len(lows) < 30:
            continue   # Insufficient data for this timeframe

        try:
            alerts_fired += await _scan_timeframe(
                session=session, symbol=symbol, tf=tf,
                closes=closes, highs=highs, lows=lows,
                volumes=volumes, all_watchlists=all_watchlists,
            )
        except Exception as e:
            print(f"⚠️ TF scan error [{symbol} {tf.upper()}]: {e}")
    return alerts_fired


# ── Timeframe Scanner ─────────────────────────────────────────────────────────

async def _scan_timeframe(
    session, symbol: str, tf: str,
    closes: list, highs: list, lows: list,
    volumes: list, all_watchlists: dict,
):
    """Run divergence detection for one symbol/timeframe and fire alerts."""

    # RSI series aligned to price indices
    rsi_series = calculate_rsi_series(closes, period=14)

    # Swing pivot indices
    swing_lows  = find_swing_low_indices(lows)
    swing_highs = find_swing_high_indices(highs)

    if len(swing_lows) < 2 and len(swing_highs) < 2:
        return 0   # Not enough structure to compare

    # Divergence detection
    min_pct = _MIN_PCT.get(tf, 0.01)
    div = detect_rsi_divergence(
        highs=highs, lows=lows,
        rsi_series=rsi_series,
        swing_low_indices=swing_lows,
        swing_high_indices=swing_highs,
        min_gap=5,
        min_pct=min_pct,
    )

    if not div:
        return 0

    current_price = closes[-1]
    direction     = div["type"]

    # ── Determine Alert Level ─────────────────────────────────────────────────
    # Level 2: Is price within ATR-based proximity of a swing S/R level?
    levels    = calculate_swing_levels(highs, lows)
    support   = levels.get("support", 0.0)
    resistance = levels.get("resistance", 0.0)
    candidates = [l for l in [support, resistance] if l > 0]

    buffer = calculate_zone_buffer(highs, lows, closes)

    nearest_level = None
    at_key_level  = False
    for kl in candidates:
        if abs(current_price - kl) <= buffer:
            at_key_level  = True
            nearest_level = kl
            break

    level = 2 if at_key_level else 1

    # Level 3: Volume declining at 2nd swing = exhaustion
    volume_confirmed = False
    if level == 2 and volumes:
        i1, i2 = div["swing1_idx"], div["swing2_idx"]
        if i1 < len(volumes) and i2 < len(volumes):
            if volumes[i2] < volumes[i1]:
                level            = 3
                volume_confirmed = True

    # ── Fire Alerts ───────────────────────────────────────────────────────────
    alerts_fired = 0
    for L in range(1, level + 1):
        alerts_fired += await _try_fire_alert(
            session=session, symbol=symbol, tf=tf,
            direction=direction, level=L,
            price=current_price, swing_data=div,
            key_level=nearest_level if L >= 2 else None,
            volume_confirmed=(L == 3 and volume_confirmed),
            all_watchlists=all_watchlists,
        )
    return alerts_fired


# ── Alert Dispatcher ──────────────────────────────────────────────────────────

async def _try_fire_alert(
    session, symbol: str, tf: str, direction: str, level: int,
    price: float, swing_data: dict, key_level, volume_confirmed: bool,
    all_watchlists: dict,
):
    """
    Check cooldown (DB-backed) + deduplication, log signal, and dispatch.
    Each (symbol, tf, direction, level) combination has its own cooldown slot
    so levels escalate independently without blocking each other.
    """
    cooldown_min = _COOLDOWN_MINUTES.get(tf, {}).get(level, 480)
    alert_type = f"DIV_{tf.upper()}_{direction}_L{level}"

    # Deduplication: check divergence_signals table
    # 1W uses 168h (7-day) window even though cooldown is skipped
    dedup_hours = (cooldown_min // 60) if cooldown_min > 0 else 0  # 0 → 168h in DB func
    recent = await asyncio.to_thread(
        database.get_recent_divergence_signal,
        symbol, tf, direction, level, dedup_hours,
    )
    if recent:
        return 0   # Same alert already fired within the dedup window

    # Determine recipients: users who have this symbol in their watchlist
    symbol_upper = symbol.upper()
    matching_pro_users = [
        uid for uid, coins in all_watchlists.items()
        if uid != str(ADMIN_USER_ID)
        if symbol_upper in [c.upper() for c in coins]
    ]
    recipients = [str(ADMIN_USER_ID), *matching_pro_users]

    # Format and send
    message = format_divergence_alert(
        symbol=symbol_upper, tf=tf, direction=direction, level=level,
        price=price, swing_data=swing_data,
        key_level=key_level, volume_confirmed=volume_confirmed,
    )

    sent_count = await dispatch_divergence_alert(session, recipients, message)
    if sent_count <= 0:
        print(
            f"⚠️ DIV ALERT DELIVERY FAILED | {symbol_upper} {tf.upper()} {direction} "
            f"L{level} → no recipients confirmed"
        )
        return 0

    signal_id = await asyncio.to_thread(
        database.create_divergence_signal,
        symbol, tf, direction, level, price, key_level, volume_confirmed,
    )
    if signal_id is None:
        return 0

    if cooldown_min > 0:
        can_fire = await asyncio.to_thread(
            database.claim_cooldown_for_user,
            "GLOBAL_DIV",
            symbol,
            alert_type,
            cooldown_min,
        )
        if not can_fire:
            print(
                f"⚠️ Cooldown sync race after send for {symbol_upper} {tf.upper()} "
                f"{direction} L{level}"
            )

    print(
        f"✅ DIV ALERT | {symbol_upper} {tf.upper()} {direction} "
        f"L{level} → {sent_count}/{len(recipients)} delivered"
    )
    return 1
