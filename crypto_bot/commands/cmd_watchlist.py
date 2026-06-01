"""
commands/cmd_watchlist.py

/watchlist — Manages the user's scan watchlist.
Pro + Admin only.
Usage:
  /watchlist (view current watchlist)
  /watchlist <coin1> <coin2> ... (set watchlist)
  /watchlist clear (clear watchlist)
"""
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import ContextTypes

import database
from utils.config import ADMIN_USER_ID
from utils.binance_api import get_valid_binance_pairs


async def handle_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /watchlist command."""
    user_id = update.effective_user.id
    is_admin = str(user_id) == str(ADMIN_USER_ID)

    # Gate: Pro + Admin only
    tier = await asyncio.to_thread(database.get_user_tier, user_id)
    if not is_admin and tier != "pro":
        await update.message.reply_text(
            "💼 <b>Watchlist Manager</b>\n\n"
            "This feature is available for <b>Pro subscribers</b> only.\n"
            "Use /start to view upgrade options.",
            parse_mode="HTML",
        )
        return

    args = context.args

    # ── CASE 1: View Watchlist ──────────────────────────────────────────────
    if not args:
        watchlist = await asyncio.to_thread(database.get_watchlist, user_id)
        if not watchlist:
            admin_note = (
                "\n\n<i>Admin Fallback: The divergence engine will scan default coins "
                "(BTC, ETH, SOL, XRP, ADA) because your watchlist is empty.</i>"
                if is_admin else ""
            )
            await update.message.reply_text(
                "💼 <b>Your Watchlist is empty!</b>\n\n"
                "Use <code>/watchlist &lt;coin1&gt; &lt;coin2&gt; ...</code> to add coins to your scanner list."
                f"{admin_note}\n\n"
                "<b>Example:</b>\n"
                "<code>/watchlist BTC ETH SOL XRP</code>",
                parse_mode="HTML",
            )
            return

        coins_lines = [f"• <b>{c}</b>" for c in sorted(watchlist)]
        coins_display = "\n".join(coins_lines)
        limit_note = (
            "<i>Admin: unlimited watchlist coins</i>"
            if is_admin
            else f"<i>Limit: {len(watchlist)}/5 coins</i>"
        )

        await update.message.reply_text(
            f"💼 <b>Your Watchlist:</b>\n\n"
            f"{coins_display}\n\n"
            f"{limit_note}\n\n"
            f"<b>Commands:</b>\n"
            f"Update list: <code>/watchlist &lt;coins&gt;</code>\n"
            f"Clear all: <code>/watchlist clear</code>",
            parse_mode="HTML",
        )
        return

    # ── CASE 2: Clear Watchlist ─────────────────────────────────────────────
    if len(args) == 1 and args[0].lower() == "clear":
        await asyncio.to_thread(database.clear_watchlist, user_id)
        admin_note = (
            "\n\n<i>Admin Note: Scanner will now use default coins.</i>"
            if is_admin else ""
        )
        await update.message.reply_text(
            "💼 <b>Watchlist Cleared</b>\n\n"
            "Your watchlist is now empty. The divergence scanner and personalized digest "
            "will no longer monitor custom coins."
            f"{admin_note}",
            parse_mode="HTML",
        )
        return

    # ── CASE 3: Add / Update Watchlist ──────────────────────────────────────
    # Clean input list (uppercase, unique, remove common punctuation like commas)
    raw_coins = []
    for arg in args:
        cleaned = arg.replace(",", "").replace(";", "").upper().strip()
        if cleaned:
            raw_coins.append(cleaned)

    # Deduplicate while preserving input order
    seen = set()
    coins = []
    for c in raw_coins:
        if c not in seen:
            seen.add(c)
            coins.append(c)

    if not coins:
        await update.message.reply_text(
            "⚠️ Please specify at least one valid coin symbol.\n"
            "<b>Example:</b> <code>/watchlist BTC ETH</code>",
            parse_mode="HTML",
        )
        return

    # Validate against active Binance quote pairs
    await update.message.reply_text("🔍 Validating symbols against Binance markets...")
    async with aiohttp.ClientSession() as session:
        valid_pairs = await get_valid_binance_pairs(session)

    valid_coins = []
    invalid_coins = []
    for c in coins:
        if c in valid_pairs:
            valid_coins.append(c)
        else:
            invalid_coins.append(c)

    if invalid_coins:
        invalid_str = ", ".join(invalid_coins)
        await update.message.reply_text(
            f"❌ <b>Invalid Coin Symbol(s):</b> <code>{invalid_str}</code>\n\n"
            f"Please verify these coins have active USDT markets on Binance.",
            parse_mode="HTML",
        )
        return

    # Save to SQLite database
    try:
        await asyncio.to_thread(database.save_watchlist, user_id, valid_coins)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to save watchlist: {e}")
        return

    coins_lines = [f"• <b>{c}</b>" for c in sorted(valid_coins)]
    coins_display = "\n".join(coins_lines)
    limit_note = (
        "<i>Admin: unlimited watchlist coins</i>"
        if is_admin
        else f"<i>Limit: {len(valid_coins)}/5 coins</i>"
    )

    await update.message.reply_text(
        f"💼 <b>Watchlist Updated!</b>\n\n"
        f"Custom tracking is now enabled for:\n"
        f"{coins_display}\n\n"
        f"{limit_note}\n"
        f"🕐 Divergence scanner and digests will update on the next 30-minute cycle.",
        parse_mode="HTML",
    )
