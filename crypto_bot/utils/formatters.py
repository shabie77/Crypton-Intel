from utils.helpers import html_safe

def format_user_label(user_row):
    user_id, username, full_name, status = user_row
    handle = f"@{username}" if username else "no_username"
    return f"- {html_safe(full_name)} ({handle}) [{status}] — <code>{user_id}</code>"

def format_channel_label(channel_row):
    channel_id, channel_name, status = channel_row
    return f"- {html_safe(channel_name)} [{status}] — <code>{channel_id}</code>"

def build_help_message(is_admin):
    common_lines = [
        "<b>Available Commands</b>",
        "/start — Register for DM updates",
        "/mycoins — Manage your coin watchlist",
        "/upgrade — Upgrade to Pro tier",
        "/feedback &lt;text&gt; — Send feedback",
        "/help — Show command list",
    ]
    if not is_admin:
        return "\n".join(common_lines)

    admin_lines = [
        "<b>Admin Commands</b>",
        "/status — Bot health summary",
        "/channels — List active channels",
        "/listusers — Detailed user directory",
        "/prolist — List Pro users only",
        "/setpro &lt;id&gt; — Manual Pro upgrade",
        "/setfree &lt;id&gt; — Manual Pro downgrade",
        "/send &lt;message&gt; - Broadcast message",
        "/setsensitivity &lt;number&gt; - Set signal strictness",
        "/testsignal &lt;symbol&gt; - Private intelligence test",
        "/signal &lt;symbol&gt; - Manual broadcast to all users",
        "/digest - Broadcast digest now",
        "/test_digest — Private test digest",
        "/update_powell &lt;stance&gt; &lt;score&gt; &lt;note&gt;",
        "/update_etf &lt;summary&gt;",
        "/renew &lt;name/id&gt;",
        "/lifecycle — Signal lifecycle status",
    ]
    return "\n".join(common_lines + [""] + admin_lines)

async def build_status_message(config, uptime, data_dir, get_db):
    tracked = ", ".join(config["tracked_coins"])
    database = get_db()
    active_chans = await __import__('asyncio').to_thread(database.get_active_channels)
    active_users = await __import__('asyncio').to_thread(database.get_active_users)

    return (
        "<b>Bot Status</b>\n"
        f"- <b>Uptime:</b> {str(uptime).split('.')[0]}\n"
        f"- <b>Tracked Coins:</b> {html_safe(tracked)}\n"
        f"- <b>Channels:</b> {len(active_chans)}\n"
        f"- <b>Users:</b> {len(active_users)}\n"
        f"- <b>Data Dir:</b> <code>{html_safe(data_dir)}</code>"
    )
