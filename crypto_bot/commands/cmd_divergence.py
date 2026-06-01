"""
commands/cmd_divergence.py

/divergence — RSI Divergence Scanner status command.
Pro + Admin only. Shows which coins are being scanned (from existing /watchlist)
and any divergence alerts fired in the last 7 days.
Watchlist is managed via the existing /watchlist command — no separate add/remove needed.
"""
import asyncio

from telegram import Update
from telegram.ext import ContextTypes

import database
from utils.config import ADMIN_USER_ID


async def handle_divergence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /divergence command."""
    user_id = update.effective_user.id
    is_admin = str(user_id) == str(ADMIN_USER_ID)

    # Gate: Pro + Admin only
    tier = await asyncio.to_thread(database.get_user_tier, user_id)
    if not is_admin and tier != "pro":
        await update.message.reply_text(
            "📊 <b>RSI Divergence Scanner</b>\n\n"
            "This feature is available for <b>Pro subscribers</b> only.\n"
            "Use /start to view upgrade options.",
            parse_mode="HTML",
        )
        return

    # Fetch existing watchlist (same coins the scanner uses)
    watchlist = await asyncio.to_thread(database.get_watchlist, user_id)
    effective_watchlist = list(watchlist)

    if is_admin and not effective_watchlist:
        config = await __import__("utils.config", fromlist=["load_config"]).load_config()
        effective_watchlist = config.get("tracked_coins", ["BTC", "ETH", "SOL"])

    if not effective_watchlist:
        await update.message.reply_text(
            "📊 <b>RSI Divergence Scanner — Active</b>\n\n"
            "You have no coins in your watchlist yet.\n\n"
            "Use /watchlist to add coins and divergence scanning will\n"
            "start automatically on the next 30-minute cycle.",
            parse_mode="HTML",
        )
        return

    # Pull recent signals for all their coins in one DB query (7 days)
    recent_signals = await asyncio.to_thread(
        database.get_recent_signals_for_symbols, effective_watchlist, 168
    )

    # Deduplicate: keep only the highest level per (symbol, timeframe, direction)
    best: dict = {}
    for sig in recent_signals:
        key = (sig["symbol"], sig["timeframe"], sig["direction"])
        if key not in best or sig["level"] > best[key]["level"]:
            best[key] = sig

    # Build coins display
    coins_lines = [f"• {c} — 4H ✅  1D ✅  1W ✅" for c in effective_watchlist]
    coins_display = "\n".join(coins_lines)

    # Admin note about no limit
    limit_note = (
        "\n<i>Admin: unlimited watchlist coins</i>"
        if is_admin
        else f"\n<i>Manage watchlist with /watchlist (max 5 coins)</i>"
    )
    if is_admin and not watchlist:
        limit_note += "\n<i>Using fallback scan list from tracked_coins because your watchlist is empty</i>"

    # Build recent alerts display
    if best:
        tier_label = {1: "Watch", 2: "Setup", 3: "Confirmed"}
        dir_emoji  = {"BULLISH": "🟢", "BEARISH": "🔴"}
        alert_lines = []
        for sig in sorted(best.values(), key=lambda x: x["fired_at"], reverse=True)[:8]:
            em  = dir_emoji.get(sig["direction"], "⚪")
            lbl = tier_label.get(sig["level"], "")
            date_str = sig["fired_at"][:10]
            alert_lines.append(
                f"{em} {sig['symbol']} {sig['timeframe'].upper()} "
                f"{sig['direction'].capitalize()} — L{sig['level']} {lbl} "
                f"({date_str})"
            )
        recent_display = "\n".join(alert_lines)
    else:
        recent_display = "No alerts in the last 7 days."

    message = (
        f"📊 <b>RSI Divergence Scanner — Active</b>\n\n"
        f"<b>Coins being scanned:</b>{limit_note}\n"
        f"{coins_display}\n\n"
        f"<b>Recent alerts (7 days):</b>\n"
        f"{recent_display}\n\n"
        f"🕐 <b>Scan interval:</b> every 30 minutes\n"
        f"👥 <b>Recipients:</b> Admin always + Pro users watching that symbol\n\n"
        f"<b>Signal tiers:</b>\n"
        f"  L1 📊 Divergence only\n"
        f"  L2 🎯 + Key S/R level\n"
        f"  L3 ✅ + Volume exhaustion (highest conviction)"
    )

    await update.message.reply_text(message, parse_mode="HTML")
