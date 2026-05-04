import html
import json
import os
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

def html_safe(value):
    return html.escape(str(value), quote=False)

def symbol_to_pair(symbol):
    return symbol if symbol.endswith("USDT") else f"{symbol}USDT"

def pair_to_tag(symbol):
    return symbol.replace("USDT", "")

def pair_to_symbol(pair):
    return pair.replace("USDT", "")

def cooldown_complete(last_alert_at, cooldown_hours):
    if not last_alert_at:
        return True
    try:
        alert_time = datetime.fromisoformat(last_alert_at)
    except ValueError:
        return True
    return utc_now() - alert_time >= timedelta(hours=cooldown_hours)

def deep_merge(base, update):
    if not isinstance(base, dict) or not isinstance(update, dict):
        return update
    for k, v in update.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = deep_merge(base[k], v)
        else:
            base[k] = v
    return base

def read_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(default, dict) and isinstance(data, dict):
            return deep_merge(default, data)
        return data
    except Exception as exc:
        print(f"Error reading JSON file {path}: {exc}")
        return default

def write_json_file(path, payload):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    os.replace(tmp_path, path)
