import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from utils.app_state import get_db, get_bot_app

import ast
import os

source = r'd:\TRADING BRAIN\crypto_bot\main.py'
with open(source, 'r', encoding='utf-8') as f:
    code = f.read()

tree = ast.parse(code)
funcs = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        funcs[node.name] = ast.get_source_segment(code, node)

def write_funcs(path, func_names):
    content = ""
    for name in func_names:
        if name in funcs:
            content += funcs[name] + "\n\n"
        else:
            content += f"# TODO: {name} function missing\n\n"
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

# Step 5: Digest
write_funcs(r'd:\TRADING BRAIN\crypto_bot\digest\digest_gemini.py', ['call_gemini_rest', 'call_groq_rest', 'get_ai_digest_insights', 'get_ai_market_summary'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\digest\digest_builder.py', ['build_digest_message', 'generate_free_summary', 'build_content'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\digest\digest_scheduler.py', ['daily_digest_loop', 'send_daily_digest', 'has_sent_digest_today', 'mark_digest_sent'])

# Step 6: Signals
write_funcs(r'd:\TRADING BRAIN\crypto_bot\signals\signal_engine.py', ['calculate_confluence_score', 'scan_coin_logic', 'rsi_scanner_loop'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\signals\signal_formatter.py', ['dispatch_signal_alert'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\signals\signal_cooldown.py', ['parse_timeframe_state'])

# Step 7: Lifecycle
write_funcs(r'd:\TRADING BRAIN\crypto_bot\lifecycle\lifecycle_engine.py', ['evaluate_signal_lifecycle', 'send_invalidation_notice'])

# Step 8: Alerts
write_funcs(r'd:\TRADING BRAIN\crypto_bot\alerts\watch_zone.py', ['dispatch_watch_alert'])

# Step 9: Commands
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_digest.py', [])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_signal.py', [])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_lifecycle.py', [])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_explain.py', ['get_symbol_explanation'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_levels.py', ['get_symbol_levels'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_bias.py', ['get_symbol_bias'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_weekly.py', ['build_weekly_report', 'weekly_report_loop'])
write_funcs(r'd:\TRADING BRAIN\crypto_bot\commands\cmd_status.py', ['build_status_message'])

print("Files generated.")
