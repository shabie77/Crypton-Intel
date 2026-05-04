from datetime import datetime, timezone, timedelta

def utc_now():
    return datetime.now(timezone.utc)

def ist_now():
    ist = utc_now() + timedelta(hours=5, minutes=30)
    return ist.strftime("%Y-%m-%d %I:%M:%S %p IST")

def utc_to_ist(utc_time_str):
    try:
        time_part = utc_time_str.split(" ")[0]
        h, m = map(int, time_part.split(":"))
        m += 30
        h += 5 + (m // 60)
        m %= 60
        h %= 24
        period = "AM" if h < 12 else "PM"
        h_display = h if 0 < h <= 12 else (h - 12 if h > 12 else 12)
        if h_display == 0:
            h_display = 12
        return f"{h_display:02d}:{m:02d} {period} IST"
    except Exception:
        return utc_time_str

def iso_now():
    return datetime.now(timezone.utc).isoformat()
