from datetime import datetime, timezone, timedelta
import database
import asyncio

def get_ist_time_str():
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    return now_ist.strftime('%I:%M %p IST')

async def build_free_digest(data, market_view, pro_count):
    time_ist = get_ist_time_str()
    pulse = data.get("pulse", {})
    fg_label = pulse.get("label", "NEUTRAL")
    fg_score = pulse.get("score", 50)
    
    msg = f"📊 <b>CRYPTON INTEL — Market Update</b> [{time_ist}]\n\n"
    msg += f"Fear & Greed: {fg_label} ({fg_score})\n\n"
    
    for coin in data.get("coins", []):
        sym = coin['symbol']
        price = coin['price']
        change = coin.get('change_24h', 0.0)
        rsi = coin.get('rsi', 50.0)
        vol_ratio = coin.get('vol_ratio', 1.0)
        
        change_emoji = "🔼" if change >= 0 else "🔽"
        change_sign = "+" if change >= 0 else ""
        vol_state = "HIGH 🟢" if vol_ratio > 1.2 else "NORM ⚪" if vol_ratio > 0.8 else "LOW 🔴"
        
        msg += f"#{sym}  ${price:,.2f} | {change_emoji} {change_sign}{change:.2f}% | RSI {rsi:.1f} | Vol {vol_state}\n"
        
    liq = data.get("liquidations", {})
    total_liq = liq.get("total", 0) / 1_000_000
    long_pct = (liq.get("longs", 0) / liq.get("total", 1)) * 100 if liq.get("total", 0) > 0 else 50
    short_pct = 100 - long_pct
    
    msg += f"\n24H Liquidations: ${total_liq:.1f}M ({long_pct:.0f}% Longs | {short_pct:.0f}% Shorts)\n\n"
    msg += f"<b>Market View:</b>\n{market_view}\n\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💎 {pro_count} traders on Pro are receiving personalized\n"
    msg += f"analysis for their exact coins right now.\n"
    msg += f"Join them → /upgrade"
    
    return msg

async def build_pro_digest_header(user_info, watchlist):
    time_ist = get_ist_time_str()
    coins_str = " • ".join(watchlist) if watchlist else "No coins tracked"
    msg = f"📊 <b>YOUR INTEL</b> — [{time_ist}]\n"
    msg += f"Your Coins: {coins_str}\n"
    return msg

async def build_pro_digest_footer(uid):
    from utils.config import ADMIN_USER_ID
    pro_info = await asyncio.to_thread(database.get_user_pro_info, uid)
    pro_since = pro_info[1] if pro_info else None
    
    date_str = "Unknown"
    if pro_since:
        try:
            dt = datetime.strptime(pro_since, "%Y-%m-%d %H:%M:%S")
            date_str = dt.strftime("%b %d, %Y")
        except Exception:
            date_str = pro_since[:10]
    elif int(uid) == ADMIN_USER_ID:
        date_str = "Admin Preview Mode"
        
    return f"\n✅ Crypton Intel Pro — Active since {date_str}"
