#!/usr/bin/env python3
"""
GLM-5.1 MCP Server for Antigravity
Primary debugging & architecture model via NVIDIA NIM
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

mcp = FastMCP("glm-5-debugger")

VALID_VENTURES = {"crypton", "devicelytics", "career_dreamz", "harleve", "default"}


class GLM5Client:
    """Primary client for GLM-5.1 on NVIDIA NIM."""

    def __init__(self, api_key=None, cache_dir=".glm_cache"):
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

    def call(self, messages, venture="default", temperature=0.3, max_tokens=4096, timeout=240):
        import requests
        import time

        if venture not in VALID_VENTURES:
            raise ValueError(f"Invalid venture '{venture}'")

        if self.usage["total"] >= 1000:
            raise RuntimeError("GLM-5.1 monthly limit reached (1000/1000)")

        payload = {
            "model": "z-ai/glm-5.1",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False}
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
            except requests.exceptions.Timeout:
                if attempt < 2:
                    time.sleep(5)
                    continue
                raise RuntimeError(
                    f"GLM-5.1 timed out after {timeout}s on attempt {attempt + 1}. "
                    f"Fallback to Kimi K2.6 or Nemotron 3 Super 120B."
                )
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


client = GLM5Client()


def _validate_inputs(code: str, venture: str, file_count: int = 1) -> tuple:
    if not code or not code.strip():
        return False, "No code provided."
    if venture not in VALID_VENTURES:
        return False, f"Invalid venture '{venture}'. Must be one of: {', '.join(sorted(VALID_VENTURES))}"
    if file_count > 3:
        return False, f"Too many files ({file_count}). Max 3 per call."
    return True, ""


@mcp.tool()
def debug_with_glm(
    code: str,
    error_message: str = "",
    file_name: str = "unknown",
    venture: str = "default",
    temperature: float = 0.3
) -> str:
    """
    Send code and error to GLM-5.1 for deep debugging analysis.
    PRIMARY MODEL. Use this first for all complex debugging tasks.
    """

    is_valid, error = _validate_inputs(code, venture)
    if not is_valid:
        return f"[GLM ERROR] {error}"

    try:
        system_prompt = (
            "You are an expert Python debugger. Analyze the provided code and error carefully. "
            "Provide: (1) Root cause, (2) Exact fix with code, (3) Explanation of why the fix works, "
            "(4) Any other bugs or design flaws you notice. Be concise but thorough."
        )

        user_content = f"""File: {file_name}

Error/Stack Trace:
```
{error_message if error_message else "No explicit error — behavior is incorrect"}
```

Code:
```python
{code}
```

Please analyze and provide the fix."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        result = client.call(
            messages=messages,
            venture=venture,
            temperature=temperature,
            max_tokens=4096,
            timeout=240
        )

        response_parts = ["=== GLM-5.1 ANALYSIS ===\n"]
        response_parts.append(result["content"])

        if "reasoning" in result:
            response_parts.append(f"\n\n[Thinking]: {result['reasoning'][:300]}...")

        usage = result.get("usage", {})
        if usage:
            response_parts.append(
                f"\n\n--- Usage: {usage.get('prompt_tokens', '?')} prompt + "
                f"{usage.get('completion_tokens', '?')} completion tokens ---"
            )

        response_parts.append(
            f"\n--- Monthly requests: {client.usage.get('total', '?')}/1000 ---"
        )

        return "\n".join(response_parts)

    except RuntimeError as e:
        if "timed out" in str(e).lower() or "monthly limit" in str(e).lower():
            return (
                f"[GLM FALLBACK TRIGGERED] {e}\n\n"
                f"Use Kimi K2.6: 'Use Kimi to debug this'\n"
                f"Or Nemotron: 'Use Nemotron for quick check'"
            )
        return f"[GLM ERROR] {e}"

    except Exception as e:
        error_trace = traceback.format_exc()
        return f"[GLM ERROR] {str(e)}\n\n{error_trace}"


@mcp.tool()
def analyze_architecture_with_glm(
    code_files: str,
    question: str,
    venture: str = "default",
    temperature: float = 0.3
) -> str:
    """
    Ask GLM-5.1 deep architectural questions about your codebase.
    Use for refactor planning, design tradeoffs, and scalability evaluation.

    Args:
        code_files: Multiple files concatenated with === FILE: separators
        question: The specific architectural question
        venture: Project name
        temperature: 0.3 for analysis

    Returns:
        Architectural analysis and recommendations
    """

    file_count = code_files.count("=== FILE:")
    if file_count == 0:
        file_count = 1

    is_valid, error = _validate_inputs(code_files, venture, file_count)
    if not is_valid:
        return f"[GLM ERROR] {error}"

    try:
        system_prompt = (
            "You are a senior software architect. Analyze the provided codebase "
            "and answer the specific architectural question. Consider: "
            "scalability, maintainability, testability, and Python best practices. "
            "Be opinionated but justify your reasoning."
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
            timeout=240
        )

        response = f"=== GLM-5.1 ARCHITECTURE REVIEW ===\n\n{result['content']}"

        if "reasoning" in result:
            response += f"\n\n[Thinking]: {result['reasoning'][:300]}..."

        usage = result.get("usage", {})
        if usage:
            response += f"\n\n--- Usage: {usage.get('prompt_tokens', '?')} prompt + {usage.get('completion_tokens', '?')} completion ---"

        response += f"\n\n--- Monthly requests: {client.usage.get('total', '?')}/1000 ---"

        return response

    except RuntimeError as e:
        if "timed out" in str(e).lower():
            return f"[GLM FALLBACK TRIGGERED] {e}\n\nUse Kimi for architecture review."
        return f"[GLM ERROR] {e}"

    except Exception as e:
        return f"[GLM ERROR] {str(e)}"


@mcp.tool()
def get_glm_usage() -> str:
    """Check current GLM-5.1 usage statistics."""
    try:
        usage = client.usage
        report = [
            "=== GLM-5.1 USAGE REPORT ===",
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
        return f"[GLM ERROR] {str(e)}"


if __name__ == "__main__":
    print("Starting GLM-5.1 MCP Server (Primary)...", file=sys.stderr)
    print("Tools: debug_with_glm, analyze_architecture_with_glm, get_glm_usage", file=sys.stderr)
    mcp.run()
