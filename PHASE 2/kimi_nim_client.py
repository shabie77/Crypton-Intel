#!/usr/bin/env python3
"""
Kimi K2.6 NVIDIA NIM Client
Lean, budget-first, Antigravity-native
Version: 1.1 (with Sonnet gap fixes)
"""

import requests
import json
import hashlib
import os
import time
from pathlib import Path
from datetime import datetime, timedelta

class KimiNIMClient:
    # Sonnet fix: Explicit venture whitelist
    VALID_VENTURES = {"crypton", "devicelytics", "career_dreamz", "harleve", "default"}

    def __init__(self, api_key=None, cache_dir=".kimi_cache", cache_ttl_days=7):
        self.invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
        self.api_key = api_key or os.getenv("NVIDIA_NIM_API_KEY")
        if not self.api_key:
            raise ValueError("NVIDIA_NIM_API_KEY not provided")

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.cache_ttl_days = cache_ttl_days
        self.request_log = self.cache_dir / "requests.json"
        self.usage = self._load_usage()

        # Sonnet fix: Auto-cleanup old cache files on init
        self._cleanup_cache()

    def _load_usage(self):
        if self.request_log.exists():
            with open(self.request_log) as f:
                data = json.load(f)
            # Auto-reset on month change
            current_month = datetime.now().strftime("%Y-%m")
            if data.get("month") != current_month:
                return {"total": 0, "month": current_month, "by_venture": {}}
            return data
        return {"total": 0, "month": datetime.now().strftime("%Y-%m"), "by_venture": {}}

    def _save_usage(self):
        with open(self.request_log, "w") as f:
            json.dump(self.usage, f, indent=2)

    def _cleanup_cache(self):
        """Remove cache files older than cache_ttl_days."""
        cutoff = datetime.now() - timedelta(days=self.cache_ttl_days)
        for cache_file in self.cache_dir.glob("*.json"):
            if cache_file.name == "requests.json":
                continue
            try:
                mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
                if mtime < cutoff:
                    cache_file.unlink()
            except OSError:
                pass

    def _get_cache_key(self, messages, model_params):
        content = json.dumps({"messages": messages, "params": model_params}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _check_cache(self, cache_key):
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            with open(cache_file) as f:
                return json.load(f)
        return None

    def _save_cache(self, cache_key, response_data):
        cache_file = self.cache_dir / f"{cache_key}.json"
        with open(cache_file, "w") as f:
            json.dump(response_data, f)

    def _count_tokens_approx(self, text):
        return len(text) // 4

    def call(self, messages, venture, temperature=0.3, thinking=False, 
             max_tokens=4096, file_count=1, use_cache=True, force_refresh=False):

        # Sonnet fix: Validate venture name
        if venture not in self.VALID_VENTURES:
            raise ValueError(
                f"Invalid venture '{venture}'. Must be one of: {', '.join(sorted(self.VALID_VENTURES))}"
            )

        # Rate limit check
        if self.usage["total"] >= 1000:
            raise RuntimeError(
                "Kimi K2.6 monthly limit reached (1000/1000). "
                "Revert to Antigravity native models (Gemini/Claude)."
            )

        # Sonnet fix: Explicit file_count parameter (no regex guessing)
        if file_count > 3:
            raise ValueError(
                f"Too many files ({file_count}). Max 3 per call to prevent "
                f"Antigravity diff-state corruption. Use sequential single-file requests."
            )

        # Cache check
        params = {"temperature": temperature, "thinking": thinking, "max_tokens": max_tokens}
        cache_key = self._get_cache_key(messages, params)

        if use_cache and not force_refresh:
            cached = self._check_cache(cache_key)
            if cached:
                return cached

        # Context budget check
        total_text = "".join([m.get("content", "") for m in messages])
        input_tokens = self._count_tokens_approx(total_text)

        if input_tokens > 200000:
            raise ValueError(f"Input too large: ~{input_tokens} tokens. Max 200K. Trim context.")

        # Adjustable timeout for thinking mode
        request_timeout = 120 if thinking else 60

        payload = {
            "model": "moonshotai/kimi-k2.6",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": False,
            "chat_template_kwargs": {"thinking": thinking}
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # Retry on 429, 502, 503
        for attempt in range(3):
            try:
                response = requests.post(
                    self.invoke_url, 
                    headers=headers, 
                    json=payload, 
                    timeout=request_timeout
                )
                response.raise_for_status()
                data = response.json()
                break
            except requests.exceptions.HTTPError as e:
                if response.status_code in (429, 502, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

        # Extract thinking — tries multiple possible keys
        msg_data = data["choices"][0]["message"]
        result = {
            "content": msg_data["content"],
            "usage": data.get("usage", {}),
            "thinking_enabled": thinking,
            "venture": venture,
            "cache_key": cache_key
        }

        reasoning_keys = ["reasoning_content", "reasoning", "thought"]
        for key in reasoning_keys:
            if key in msg_data:
                result["reasoning"] = msg_data[key]
                break

        # Update usage tracker
        self.usage["total"] += 1
        self.usage["by_venture"][venture] = self.usage["by_venture"].get(venture, 0) + 1
        self._save_usage()

        if use_cache:
            self._save_cache(cache_key, result)

        return result


# Quick test harness
if __name__ == "__main__":
    client = KimiNIMClient()

    print("Testing thinking mode response format...")
    try:
        result = client.call(
            messages=[{"role": "user", "content": "Explain why this code has a bug: x = 1/0"}],
            venture="test",  # This will fail — "test" not in whitelist, proving validation works
            thinking=True,
            temperature=0.3,
            max_tokens=2048,
            file_count=1
        )
    except ValueError as e:
        print(f"Venture validation works: {e}")
        # Retry with valid venture
        result = client.call(
            messages=[{"role": "user", "content": "Explain why this code has a bug: x = 1/0"}],
            venture="default",
            thinking=True,
            temperature=0.3,
            max_tokens=2048,
            file_count=1
        )
        print("Response keys:", list(result.keys()))
        print("Has reasoning:", "reasoning" in result)
        if "reasoning" in result:
            print("Reasoning preview:", result["reasoning"][:200] + "...")
        print("Usage:", result.get("usage"))
        print("Total requests this month:", client.usage["total"])
