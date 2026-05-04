import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import os

source = r'd:\TRADING BRAIN\crypto_bot\database.py'
with open(source, 'r', encoding='utf-8') as f:
    content = f.read()

supabase_start = content.find('def save_bot_state(')
supabase_end = content.find('def get_seen_news(')

# The shared lock comes before save_bot_state
lock_start = content.rfind('# Shared lock', 0, supabase_start)
if lock_start != -1:
    supabase_start = lock_start

supabase_funcs = content[supabase_start:supabase_end]

supabase_setup = '''try:
    from supabase import create_client
except ImportError:
    create_client = None
    print("WARNING: supabase package not installed — Supabase features disabled")

import os
import json
import threading
import tempfile
from datetime import datetime, timezone

DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.path.join(os.getcwd(), "data"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = None

if SUPABASE_URL and SUPABASE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("☁️ Supabase Cloud Persistence initialized.")
    except Exception as e:
        print(f"⚠️ Supabase initialization failed: {e}")

'''

with open(r'd:\TRADING BRAIN\crypto_bot\database\db_supabase.py', 'w', encoding='utf-8') as f:
    f.write(supabase_setup + supabase_funcs)

users_content = content[:supabase_start] + content[supabase_end:]
users_content = users_content.replace('def init_db():', 'def initialise_database():')
users_content = users_content.replace('print("✅ Database ready.")', 'print("✅ Database ready.")\n    return _connect()')

with open(r'd:\TRADING BRAIN\crypto_bot\database\db_users.py', 'w', encoding='utf-8') as f:
    f.write(users_content)
