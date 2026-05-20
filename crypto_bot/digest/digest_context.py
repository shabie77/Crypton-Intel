"""
digest/digest_context.py

Single Groq call per digest cycle.
Generates ONLY narrative text. All facts come from market_logic.py.
AI is presentation-only. Never makes TA decisions.
"""
from utils.config import GROQ_ROTATOR, GEMINI_API_KEY
import aiohttp

# ─── Fallback Texts ──────────────────────────────────────────────────────────

FALLBACK_CONTEXT = (
    "Major coins continue holding structure, "
    "though participation remains weaker than average."
)
FALLBACK_RISK = (
    "Lower volume conditions may increase the chance of sudden volatility."
)
FALLBACK_TAKEAWAY = (
    "Trend structure remains intact across majors, "
    "though momentum has slowed compared to earlier sessions."
)
FALLBACK_READ = {
    "BULLISH_CONFIRMED": "Trend remains bullish with volume supporting the move. Momentum is intact.",
    "BULLISH_WEAKENING": "Bullish structure holds, but buyers are showing less strength. Watch for cooling.",
    "BEARISH_CONFIRMED": "Trend is bearish with volume confirming the move. No recovery signal yet.",
    "BEARISH_WEAKENING": "Bearish structure present, but selling pressure is easing near lows.",
    "RECOVERING": "Price is recovering from recent lows. Momentum is building but unconfirmed.",
    "NEUTRAL": "Market is consolidating. No clear directional bias at current levels.",
}

BANNED_PHRASES = [
    "mixed signals", "neutral momentum", "accumulation phase",
    "market unclear", "analysis pending", "watch for trend continuation",
    "traders should stay cautious", "market remains uncertain"
]


def _strip_banned(text: str) -> str:
    """Remove banned phrases from AI output."""
    for phrase in BANNED_PHRASES:
        text = text.replace(phrase, "").replace(phrase.capitalize(), "")
    return text.strip()


# ─── Groq Call ───────────────────────────────────────────────────────────────

async def _call_groq(session, prompt: str) -> str | None:
    idx, api_key = await GROQ_ROTATOR.reserve_key()
    if not api_key:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
        "max_tokens": 600
    }
    _timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with session.post(url, headers=headers, json=payload, timeout=_timeout) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
            elif resp.status == 429:
                await GROQ_ROTATOR.advance_after_rate_limit(idx)
                print(f"⚠️ Groq Rate Limit Hit (429). Rotated key.")
                return None
            else:
                error = await resp.text()
                print(f"❌ Groq Error ({resp.status}): {error}")
    except Exception as e:
        print(f"❌ Groq Connection Error: {e}")
    return None


async def _call_gemini(session, prompt: str) -> str | None:
    if not GEMINI_API_KEY:
        return None
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 600}
    }
    _timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with session.post(url, json=payload, timeout=_timeout) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                error = await resp.text()
                print(f"❌ Gemini Error ({resp.status}): {error}")
    except Exception as e:
        print(f"❌ Gemini Connection Error: {e}")
    return None


# ─── Main Context Generator ───────────────────────────────────────────────────

async def generate_digest_context(session, coin_summaries: list[dict], pulse_label: str) -> dict:
    """
    Single AI call per digest cycle.
    coin_summaries: list of {symbol, price, change_24h, rsi, rsi_label, vol_label, trend, bias}
    Returns dict with context, risk, takeaway, reads (per coin).
    """
    coins_text = "\n".join([
        f"#{c['symbol']}: ${c['price']:,.2f} ({c['change_24h']:+.2f}%) "
        f"RSI:{c['rsi']:.1f} ({c['rsi_label']}) "
        f"Vol:{c['vol_label']} Trend:{c['trend']} Bias:{c['bias']}"
        for c in coin_summaries
    ])

    prompt = f"""You are a professional crypto market analyst writing a premium daily brief.

Market Pulse: {pulse_label}
Coin Data:
{coins_text}

Write the following sections. Be concise and direct. Use plain English. No markdown. No headers.

BANNED PHRASES (never use): mixed signals, neutral momentum, accumulation phase, market unclear, watch for trend continuation, traders should stay cautious.

OUTPUT FORMAT (use these exact labels, one per line):
CONTEXT: [2-3 sentences max about overall market condition and why it matters]
RISK: [1-2 sentences about the key risk this session]
TAKEAWAY: [2-3 sentences summarizing trend, momentum, and what to mentally expect]
{chr(10).join([f"READ_{c['symbol']}: [2 sentences about #{c['symbol']} specifically, plain English, based on its bias: {c['bias']}]" for c in coin_summaries])}"""

    raw = await _call_groq(session, prompt)
    if raw:
        provider = "groq"
    else:
        raw = await _call_gemini(session, prompt)
        provider = "gemini" if raw else "fallback"

    result = {
        "context": FALLBACK_CONTEXT,
        "risk": FALLBACK_RISK,
        "takeaway": FALLBACK_TAKEAWAY,
        "reads": {c["symbol"]: FALLBACK_READ.get(c["bias"], FALLBACK_READ["NEUTRAL"]) for c in coin_summaries},
        "provider": provider
    }

    if not raw:
        return result

    raw = _strip_banned(raw)
    lines = raw.strip().splitlines()

    for line in lines:
        if line.startswith("CONTEXT:"):
            result["context"] = line[len("CONTEXT:"):].strip()
        elif line.startswith("RISK:"):
            result["risk"] = line[len("RISK:"):].strip()
        elif line.startswith("TAKEAWAY:"):
            result["takeaway"] = line[len("TAKEAWAY:"):].strip()
        else:
            for c in coin_summaries:
                prefix = f"READ_{c['symbol']}:"
                if line.startswith(prefix):
                    result["reads"][c["symbol"]] = line[len(prefix):].strip()

    return result


# ─── Pro Context Generator ────────────────────────────────────────────────────

# Pro-specific fallback texts
_PRO_FALLBACK_DIRECTION = (
    "The overall market is holding structure near current levels. "
    "Buyers and sellers are both active near key zones."
)
_PRO_FALLBACK_OUTLOOK = (
    "No single dominant narrative is driving the market right now. "
    "Trend structure remains intact across majors, but momentum has slowed. "
    "Patience near key levels is the safer approach this session."
)
_PRO_FALLBACK_TRADER_READ = {
    "BULLISH_CONFIRMED": "Structure remains bullish with volume supporting the move. Current momentum is intact across key timeframes.",
    "BULLISH_WEAKENING": "Bullish structure holds, but buying pressure is fading. Watch for lower volume on the next push higher.",
    "BEARISH_CONFIRMED": "Bearish structure is active with volume confirming the move. No recovery signal has appeared yet.",
    "BEARISH_WEAKENING": "Selling pressure is easing near key lows. Structure remains bearish but risk of a bounce is increasing.",
    "RECOVERING":        "Price is recovering from recent lows with improving momentum. Full confirmation still needed.",
    "NEUTRAL":           "No clear directional bias at current levels. Price is consolidating near a key decision zone.",
}
_PRO_FALLBACK_MATTERS = {
    "BULLISH_CONFIRMED": "Holding above 4H support keeps the bullish structure valid. A break above resistance opens the next target zone.",
    "BULLISH_WEAKENING": "Watch whether buyers defend the 4H support zone. Losing it would signal a deeper pullback.",
    "BEARISH_CONFIRMED": "Staying below 4H resistance keeps sellers in control. A close above that level would signal potential recovery.",
    "BEARISH_WEAKENING": "A bounce from current lows needs volume confirmation. Without it, the bearish structure remains active.",
    "RECOVERING":        "A clean close above 4H resistance is needed to confirm recovery. Watch volume carefully on the next push.",
    "NEUTRAL":           "Watch for a decisive break in either direction. Both resistance and support need monitoring.",
}


def build_pro_fallback_context(coin_data_list: list[dict]) -> dict:
    return {
        "direction": _PRO_FALLBACK_DIRECTION,
        "outlook": _PRO_FALLBACK_OUTLOOK,
        "reads": {
            c["symbol"]: _PRO_FALLBACK_TRADER_READ.get(c["bias"], _PRO_FALLBACK_TRADER_READ["NEUTRAL"])
            for c in coin_data_list
        },
        "matters": {
            c["symbol"]: _PRO_FALLBACK_MATTERS.get(c["bias"], _PRO_FALLBACK_MATTERS["NEUTRAL"])
            for c in coin_data_list
        },
    }


async def generate_pro_context(
    session,
    coin_data_list: list[dict],
    pulse_label: str,
) -> dict:
    """
    Single AI call per Pro user per cycle.
    Generates TRADER_READ_ and MATTERS_ per coin, plus DIRECTION and OUTLOOK.
    Groq primary → Gemini fallback → deterministic fallback.
    coin_data_list: list of full coin data dicts from pro_engine._build_coin_data()
    """
    # Build a rich coin summary for the AI prompt
    coin_lines = []
    for c in coin_data_list:
        coin_lines.append(
            f"#{c['symbol']}: Price={c['price']:.2f} Change={c['change_24h']:+.2f}% "
            f"4H_RSI={c['rsi_4h']:.1f} 1D_RSI={c['rsi_1d']:.1f} "
            f"Vol={c['vol_label']}({c['vol_delta']:+d}%) "
            f"Structure4H={c['structure_4h']} Structure1D={c['structure_1d']} "
            f"Streak={c['streak_display']} Bias={c['bias']}"
        )
    coins_text = "\n".join(coin_lines)

    read_lines = "\n".join(
        [f"TRADER_READ_{c['symbol']}: [3-4 sentences about {c['symbol']} specifically. "
         f"Reference its RSI, structure, and streak. Based on bias: {c['bias']}]"
         for c in coin_data_list]
    )
    matters_lines = "\n".join(
        [f"MATTERS_{c['symbol']}: [2-3 sentences about what to watch now for {c['symbol']}. "
         f"Reference the key level at {c['resistance_4h']:.2f} or {c['support_4h']:.2f}. Actionable.]"
         for c in coin_data_list]
    )

    prompt = f"""You are a senior crypto intelligence analyst writing a personalized Pro daily brief.

Market Pulse: {pulse_label}
Coin Data:
{coins_text}

Rules:
- Write in plain English only. No markdown. No bullet points. No asterisks.
- Be direct, specific, and reference real numbers from the data above.
- Never say: mixed signals, accumulation phase, market unclear, analysis pending, exercise caution.
- Keep each section to the exact length requested.

OUTPUT FORMAT (use these exact labels, one per line):
DIRECTION: [2-3 sentences about the overall market direction and what is driving it right now]
OUTLOOK: [2-3 sentences about what traders should mentally prepare for this session]
{read_lines}
{matters_lines}"""

    raw = await _call_groq(session, prompt)
    if not raw:
        raw = await _call_gemini(session, prompt)

    # Build result with deterministic fallbacks pre-filled
    result = build_pro_fallback_context(coin_data_list)

    if not raw:
        return result

    raw = _strip_banned(raw)
    lines = raw.strip().splitlines()

    for line in lines:
        if line.startswith("DIRECTION:"):
            value = line[len("DIRECTION:"):].strip()
            if value:
                result["direction"] = value
        elif line.startswith("OUTLOOK:"):
            value = line[len("OUTLOOK:"):].strip()
            if value:
                result["outlook"] = value
        else:
            for c in coin_data_list:
                sym = c["symbol"]
                if line.startswith(f"TRADER_READ_{sym}:"):
                    value = line[len(f"TRADER_READ_{sym}:"):].strip()
                    if value:
                        result["reads"][sym] = value
                elif line.startswith(f"MATTERS_{sym}:"):
                    value = line[len(f"MATTERS_{sym}:"):].strip()
                    if value:
                        result["matters"][sym] = value

    return result
