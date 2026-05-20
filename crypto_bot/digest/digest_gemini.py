from utils.config import GEMINI_API_KEY, GROQ_ROTATOR

async def call_gemini_rest(session, prompt):
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500}
    }
    try:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['candidates'][0]['content']['parts'][0]['text']
            else:
                error_text = await resp.text()
                print(f"❌ Gemini API Error (Status {resp.status}): {error_text}")
    except Exception as e:
        print(f"❌ Gemini Connection Error: {e}")
    return None

async def call_groq_rest(session, prompt):
    idx, api_key = await GROQ_ROTATOR.reserve_key()
    if not api_key:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['choices'][0]['message']['content']
            elif resp.status == 429:
                await GROQ_ROTATOR.advance_after_rate_limit(idx)
                print(f"⚠️ Groq Rate Limit Hit (Status 429). Rotated key.")
                return None
            else:
                error_text = await resp.text()
                print(f"❌ Groq API Error (Status {resp.status}): {error_text}")
    except Exception as e:
        print(f"❌ Groq Connection Error: {e}")
    return None

async def get_ai_digest_insights(session, data):
    prompt = f"Act as a professional crypto analyst. Summarize this market data into a 3-sentence morning brief. Avoid hype:\n{data}"
    return await call_groq_rest(session, prompt) or await call_gemini_rest(session, prompt) or "Market analysis currently unavailable."

async def get_ai_market_summary(session, data):
    prompt = f"Provide a one-sentence market trend summary based on: {data}"
    return await call_groq_rest(session, prompt) or await call_gemini_rest(session, prompt) or "Mixed market conditions."

async def get_free_market_view(session, data_str):
    prompt = f"Act as a professional crypto analyst. Data: {data_str}. Write a 2-sentence 'Market View' about bulls/bears and RSI extremes. Output ONLY the 2 sentences. No headers. No pulse scores."
    return await call_groq_rest(session, prompt) or await call_gemini_rest(session, prompt) or "Market conditions are mixed. Trade with caution."

async def get_pro_personal_digest(session, watchlist, data_str, macro_event):
    prompt = f"""Act as a premium crypto analyst. Generate personalized analysis for: {', '.join(watchlist)}.
    Data Context: {data_str}
    Macro: {macro_event}

    STRICT RULES:
    1. For EACH coin, start IMMEDIATELY with the separator: ━━━━━━━━━━━━━━━
    2. Format: #SYMBOL $Price | Change%
    3. Do NOT add titles like '**BTC Update**' or 'Analysis'.
    4. If Macro is 'None', do NOT include a macro section.
    
    EXACT FORMAT PER COIN:
    ━━━━━━━━━━━━━━━
    #SYMBOL $Price | Change%

    Market State: [State]
    RSI: [Value] — [Status]
    Volume: [Status]

    Key Levels:
      Resistance: [L1] | [L2]
      Support:    [L1] | [L2]

    Risk:
    [Analysis]

    Focus:
    [Action]
    """
    return await call_groq_rest(session, prompt) or await call_gemini_rest(session, prompt) or "Analysis unavailable."
