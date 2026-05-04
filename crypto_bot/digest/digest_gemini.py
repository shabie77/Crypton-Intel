import asyncio
import aiohttp
from utils.config import GEMINI_API_KEY, GROQ_API_KEY

async def call_gemini_rest(session, prompt):
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500}
    }
    try:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini API Error: {e}")
    return None

async def call_groq_rest(session, prompt):
    if not GROQ_API_KEY:
        return None
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    try:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"Groq API Error: {e}")
    return None

async def get_ai_digest_insights(session, data):
    prompt = f"Act as a professional crypto analyst. Summarize this market data into a 3-sentence morning brief. Avoid hype:\n{data}"
    return await call_gemini_rest(session, prompt) or await call_groq_rest(session, prompt) or "Market analysis currently unavailable."

async def get_ai_market_summary(session, data):
    prompt = f"Provide a one-sentence market trend summary based on: {data}"
    return await call_gemini_rest(session, prompt) or await call_groq_rest(session, prompt) or "Mixed market conditions."
