import asyncio
from datetime import datetime, timezone, timedelta
import database as database  # resolves to database/ package via __init__.py
from utils.state import active_signal_states
from utils.config import load_config, ADMIN_USER_ID
import aiohttp

from digest.digest_gemini import call_gemini_rest
from utils.helpers import html_safe
from utils.telegram import send_telegram_message
from utils.time_utils import iso_now

async def build_weekly_report(session) -> str:
    """
    Builds a premium weekly intelligence report.
    Includes: total signals, average score, top signals, most active coin,
    current lifecycle status, and an AI commentary.
    """
    
    summary = await asyncio.to_thread(database.get_weekly_signal_summary, 7)
    if not summary or summary.get("total", 0) == 0:
        return (
            "📊 <b>WEEKLY INTELLIGENCE REPORT</b>\n"
            f"📅 Week ending: {datetime.now(timezone.utc).strftime('%B %d, %Y')}\n\n"
            "No signals were generated this week.\n"
            "The market remained in neutral territory across all tracked assets.\n\n"
            "<i>Market analysis only. Not financial advice.</i>"
        )

    total = summary["total"]
    avg_score = summary["avg_score"]
    most_active = summary["most_active"]
    best_signals = summary.get("best_signals", [])

    # Build best signals section
    best_lines = []
    for sig in best_signals[:5]:
        sym, direction, score, _ = sig[0], sig[1], sig[2], sig[3]
        emoji = "🟢" if direction == "LONG" else "🔴"
        best_lines.append(f"  {emoji} #{sym} {direction} — Score: {score}%")

    # Current lifecycle status snapshot
    lifecycle_lines = []
    config = await load_config()
    for sym in config.get("tracked_coins", []):
        ls = active_signal_states.get(sym, {})
        state = ls.get("state", "NEUTRAL")
        icons = {
            "NEUTRAL": "⏸️",
            "WATCH": "👁️",
            "EARLY": "⚡",
            "CONFIRMED": "✅",
            "EXPIRED": "❌",
        }
        lifecycle_lines.append(f"  {icons.get(state, '⏸️')} #{sym}: {state}")

    # AI Summary (optional, with fallback)
    ai_summary = "Market conditions were mixed this week. Stay disciplined and follow your levels."
    try:
        prompt = (
            f"You are a crypto market analyst. Summarize this week's activity in 2-3 sentences:\n"
            f"- Total signals generated: {total}\n"
            f"- Average signal confidence: {avg_score}%\n"
            f"- Most active asset: {most_active}\n"
            f"Be concise, professional, and avoid hype."
        )
        ai_raw = await call_gemini_rest(session, prompt)
        if ai_raw and len(ai_raw.strip()) > 20:
            ai_summary = html_safe(ai_raw.strip())
    except Exception as e:
        print(f"⚠️ Weekly AI summary error: {e}")

    report = (
        f"📊 <b>WEEKLY INTELLIGENCE REPORT</b>\n"
        f"📅 Week ending: {datetime.now(timezone.utc).strftime('%B %d, %Y')}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"📈 <b>Week Summary</b>\n"
        f"  Total Signals: {total}\n"
        f"  Avg Confidence: {avg_score}%\n"
        f"  Most Active: #{most_active}\n\n"
        f"🏆 <b>Top Signals This Week</b>\n"
        f"{chr(10).join(best_lines) if best_lines else '  No qualifying signals'}\n\n"
        f"📍 <b>Current Lifecycle Status</b>\n"
        f"{chr(10).join(lifecycle_lines)}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🤖 <b>AI Analysis</b>\n"
        f"{ai_summary}\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"<i>Market analysis only. Not financial advice.</i>"
    )

    return report

async def weekly_report_loop(session):
    """
    Sends weekly report to Pro users every Sunday at 03:30 UTC (9:00 AM IST).
    """
    print("📊 Weekly Report Scheduler is Live...")
    while True:
        try:
            now = datetime.now(timezone.utc)
            week_str = now.strftime("%Y-%W")

            # Calculate seconds until next Sunday 03:30 UTC
            days_until_sunday = (6 - now.weekday()) % 7
            if days_until_sunday == 0 and (
                now.hour > 3 or (now.hour == 3 and now.minute >= 30)
            ):
                days_until_sunday = 7

            next_report = now.replace(
                hour=3, minute=30, second=0, microsecond=0
            ) + timedelta(days=days_until_sunday)
            wait_seconds = (next_report - now).total_seconds()

            print(f"⏳ Next weekly report in {wait_seconds / 3600:.1f} hours")
            await asyncio.sleep(wait_seconds)

            # Check if already sent for this week
            if await asyncio.to_thread(database.check_weekly_sent, week_str):
                continue

            # Build and send report
            report = await build_weekly_report(session)

            # Send to Pro users only
            users_with_tiers = await asyncio.to_thread(
                database.get_all_users_with_tiers
            )
            sent = 0
            for row in users_with_tiers:
                uid, tier = row[0], row[2]
                if tier == "pro":
                    if await send_telegram_message(session, uid, report):
                        sent += 1
                    await asyncio.sleep(0.1)  # Prevent flood

            await asyncio.to_thread(database.mark_weekly_sent, week_str)
            await asyncio.to_thread(
                database.log_alert, "weekly_report", "PRO", report, iso_now()
            )
            print(
                f"✅ Weekly Intelligence Report dispatched to {sent} Pro users for week {week_str}"
            )

        except Exception as exc:
            print(f"Weekly Report Error: {exc}")
            await asyncio.sleep(3600)

async def handle_weekly(update, context):
    """Manual trigger for weekly report."""
    user_id = update.effective_user.id
    if str(user_id) != str(ADMIN_USER_ID):
        return

    await update.message.reply_text("🚨 Triggering Manual Weekly Intelligence broadcast...")
    
    async with aiohttp.ClientSession() as session:
        report = await build_weekly_report(session)
        
        users_with_tiers = await asyncio.to_thread(database.get_all_users_with_tiers)
        sent = 0
        for row in users_with_tiers:
            uid, tier = row[0], row[2]
            if tier == "pro":
                if await send_telegram_message(session, uid, report):
                    sent += 1
                await asyncio.sleep(0.1)
        
        now = datetime.now(timezone.utc)
        await asyncio.to_thread(database.log_alert, "weekly_report", "PRO", report, iso_now())
    
    await update.message.reply_text(f"✅ Weekly Intelligence broadcasted to {sent} Pro users.")
