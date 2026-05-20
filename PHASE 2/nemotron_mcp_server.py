#!/usr/bin/env python3
"""
Nemotron 3 Super 120B MCP Server for Antigravity
Emergency fallback model via NVIDIA NIM
Version: 2.1 - Final with architecture tool
"""

import sys
import os
import json
import traceback
from pathlib import Path

sys.path.insert(0, r"D:\TRADING BRAIN\PHASE 2")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError("FastMCP not installed. Run: pip install mcp")

mcp = FastMCP("nemotron-debugger")

VALID_VENTURES = {"crypton", "devicelytics", "career_dreamz", "harleve", "default"}


class NemotronClient:
    """Emergency fallback client for Nemotron 3 Super 120B on NVIDIA NIM."""

    def __init__(self, api_key=None, cache_dir=".nemotron_cache"):
        self.invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
        self.api_key = api_key or os.getenv("NVIDIA_NIM_API_KEY")
        if not self.api_key:
            raise ValueError("NVIDIA_NIM_API_KEY not provided")

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.request_log = self.cache_dir / "requests.json"
        self.usage = self._load_usage()

    def _load_usage(self):
        if self.request_log.exists():
            with open(self.request_log) as f:
                data = json.load(f)
            from datetime import datetime
            current_month = datetime.now().strftime("%Y-%m")
            if data.get("month") != current_month:
                return {"total": 0, "month": current_month, "by_venture": {}}
            return data
        from datetime import datetime
        return {"total": 0, "month": datetime.now().strftime("%Y-%m"), "by_venture": {}}

    def _save_usage(self):
        with open(self.request_log, "w") as f:
            json.dump(self.usage, f, indent=2)

    def call(self, messages, venture="default", temperature=0.3, max_tokens=4096, timeout=60):
        import requests
        import time

        if venture not in VALID_VENTURES:
            raise ValueError(f"Invalid venture '{venture}'")

        if self.usage["total"] >= 1000:
            raise RuntimeError("Nemotron monthly limit reached (1000/1000)")

        payload = {
            "model": "nvidia/nemotron-3-super-120b-a12b",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_budget": 16384}
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        for attempt in range(3):
            try:
                response = requests.post(
                    self.invoke_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout
                )
                response.raise_for_status()
                data = response.json()
                break
            except requests.exceptions.HTTPError as e:
                if response.status_code in (429, 502, 503) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

        msg_data = data["choices"][0]["message"]
        result = {
            "content": msg_data["content"],
            "usage": data.get("usage", {}),
            "venture": venture
        }

        thinking_keys = ["reasoning_content", "reasoning", "thought"]
        for key in thinking_keys:
            if key in msg_data:
                result["reasoning"] = msg_data[key]
                break

        self.usage["total"] += 1
        self.usage["by_venture"][venture] = self.usage["by_venture"].get(venture, 0) + 1
        self._save_usage()

        return result


client = NemotronClient()


def _validate_inputs(code: str, venture: str, file_count: int = 1) -> tuple:
    if not code or not code.strip():
        return False, "No code provided."
    if venture not in VALID_VENTURES:
        return False, f"Invalid venture '{venture}'. Must be one of: {', '.join(sorted(VALID_VENTURES))}"
    if file_count > 3:
        return False, f"Too many files ({file_count}). Max 3 per call."
    return True, ""


@mcp.tool()
def debug_with_nemotron(
    code: str,
    error_message: str = "",
    file_name: str = "unknown",
    venture: str = "default",
    temperature: float = 0.3
) -> str:
    """
    Send code and error to Nemotron 3 Super 120B for quick debugging.
    EMERGENCY FALLBACK ONLY. Use when GLM-5.1 and Kimi K2.6 are unavailable.
    """

    is_valid, error = _validate_inputs(code, venture)
    if not is_valid:
        return f"[NEMOTRON ERROR] {error}"

    try:
        system_prompt = (
            "You are a Python debugging assistant. Analyze the code and error. "
            "Provide: (1) Root cause, (2) Fix, (3) Any other issues. Be concise."
        )

        user_content = f"""File: {file_name}

Error:
```
{error_message if error_message else "No explicit error"}
```

Code:
```python
{code}
```

Analyze and fix."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        result = client.call(
            messages=messages,
            venture=venture,
            temperature=temperature,
            max_tokens=4096,
            timeout=60
        )

        response_parts = ["=== NEMOTRON 3 SUPER 120B ANALYSIS ===\n"]
        response_parts.append(result["content"])

        if "reasoning" in result:
            response_parts.append(f"\n\n[Thinking]: {result['reasoning'][:300]}...")

        usage = result.get("usage", {})
        if usage:
            response_parts.append(
                f"\n\n--- Usage: {usage.get('prompt_tokens', '?')} prompt + "
                f"{usage.get('completion_tokens', '?')} completion ---"
            )

        response_parts.append(
            f"\n--- Monthly requests: {client.usage.get('total', '?')}/1000 ---"
        )

        return "\n".join(response_parts)

    except RuntimeError as e:
        if "monthly limit" in str(e).lower():
            return f"[NEMOTRON LIMIT REACHED] {e}\n\nAll NIM models exhausted."
        return f"[NEMOTRON ERROR] {e}"

    except Exception as e:
        error_trace = traceback.format_exc()
        return f"[NEMOTRON ERROR] {str(e)}\n\n{error_trace}"


@mcp.tool()
def analyze_architecture_with_nemotron(
    code_files: str,
    question: str,
    venture: str = "default",
    temperature: float = 0.3
) -> str:
    """
    Ask Nemotron 3 Super 120B architectural questions.
    EMERGENCY FALLBACK for architecture review when GLM/Kimi unavailable.

    Args:
        code_files: Multiple files with === FILE: separators
        question: Architectural question
        venture: Project name
        temperature: 0.3 for analysis

    Returns:
        Architectural analysis
    """

    file_count = code_files.count("=== FILE:")
    if file_count == 0:
        file_count = 1

    is_valid, error = _validate_inputs(code_files, venture, file_count)
    if not is_valid:
        return f"[NEMOTRON ERROR] {error}"

    try:
        system_prompt = (
            "You are a software architect. Review the codebase and answer the question. "
            "Be concise. Focus on practical recommendations."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Codebase:\n{code_files}\n\nQuestion: {question}"}
        ]

        result = client.call(
            messages=messages,
            venture=venture,
            temperature=temperature,
            max_tokens=4096,
            timeout=60
        )

        response = f"=== NEMOTRON ARCHITECTURE REVIEW ===\n\n{result['content']}"

        if "reasoning" in result:
            response += f"\n\n[Thinking]: {result['reasoning'][:300]}..."

        usage = result.get("usage", {})
        if usage:
            response += f"\n\n--- Usage: {usage.get('prompt_tokens', '?')} prompt + {usage.get('completion_tokens', '?')} completion ---"

        response += f"\n\n--- Monthly requests: {client.usage.get('total', '?')}/1000 ---"

        return response

    except RuntimeError as e:
        return f"[NEMOTRON ERROR] {e}"
    except Exception as e:
        return f"[NEMOTRON ERROR] {str(e)}"


@mcp.tool()
def get_nemotron_usage() -> str:
    """Check current Nemotron usage statistics."""
    try:
        usage = client.usage
        report = [
            "=== NEMOTRON 3 SUPER 120B USAGE REPORT ===",
            f"Month: {usage.get('month', 'unknown')}",
            f"Total requests: {usage.get('total', 0)}/1000",
            "",
            "Per-venture breakdown:"
        ]
        for venture, count in sorted(usage.get("by_venture", {}).items()):
            report.append(f"  {venture}: {count}")
        remaining = 1000 - usage.get("total", 0)
        report.extend(["", f"Remaining: {remaining}"])
        return "\n".join(report)
    except Exception as e:
        return f"[NEMOTRON ERROR] {str(e)}"


if __name__ == "__main__":
    print("Starting Nemotron 3 Super 120B MCP Server (Emergency Fallback)...", file=sys.stderr)
    print("Tools: debug_with_nemotron, analyze_architecture_with_nemotron, get_nemotron_usage", file=sys.stderr)
    mcp.run()
