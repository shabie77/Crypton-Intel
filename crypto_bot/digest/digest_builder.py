from datetime import datetime, timezone

async def generate_free_summary(data):
    date_str = datetime.now(timezone.utc).strftime('%B %d, %Y')
    msg = (
        f"📊 <b>DAILY MARKET DIGEST (FREE)</b>\n"
        f"📅 {date_str}\n\n"
        f"📈 <b>Market Pulse:</b> {data.get('pulse', 'N/A')}/100\n\n"
        "Upgrade to PRO for detailed coin levels and AI analysis.\n"
        "Use /start to view subscription options."
    )
    return msg

async def build_content(data):
    date_str = datetime.now(timezone.utc).strftime('%B %d, %Y')
    msg = (
        f"📊 <b>DAILY MARKET INTELLIGENCE (PRO)</b>\n"
        f"📅 {date_str}\n\n"
        f"📈 <b>Market Pulse:</b> {data.get('pulse', 'N/A')}/100\n"
        f"🧭 <b>Overall Trend:</b> {data.get('trend', 'Neutral')}\n\n"
        "📍 <b>Key Asset Status:</b>\n"
    )
    for coin in data.get('coins', []):
        msg += f"• <b>#{coin['symbol']}:</b> ${coin['price']:,.2f} | RSI: {coin['rsi']:.1f} | {coin['state']}\n"
    msg += (
        f"\n🤖 <b>AI Insights:</b>\n"
        f"{data.get('ai_insights', 'No insights available.')}\n\n"
        "<i>Trade carefully. Market conditions can change rapidly.</i>"
    )
    return msg

async def build_digest_message(data, is_pro=True):
    if is_pro:
        return await build_content(data)
    else:
        return await generate_free_summary(data)
