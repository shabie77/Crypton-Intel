#!/usr/bin/env python3
"""
Kimi K2.6 MCP Server for Antigravity
Escalation-layer debugger via NVIDIA NIM
Version: 1.0
"""

import sys
import os
import json
import traceback
from pathlib import Path

# Add client location to path
sys.path.insert(0, r"D:\TRADING BRAIN\PHASE 2")

try:
    from kimi_nim_client import KimiNIMClient  # Fix 1: correct filename
except ImportError:
    raise ImportError(
        "KimiNIMClient not found. Ensure kimi_nim_client.py "
        "exists in D:\\TRADING BRAIN\\PHASE 2"
    )

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "FastMCP not installed. Run: pip install mcp"
    )

# Initialize MCP server
mcp = FastMCP("kimi-nim-debugger")

# Venture whitelist for validation
VALID_VENTURES = {"crypton", "devicelytics", "career_dreamz", "harleve", "default"}

# Fix 3: Module-level client — instantiated ONCE on server startup, not per call
try:
    client = KimiNIMClient()
    print("KimiNIMClient initialized successfully.", file=sys.stderr)
except ValueError as e:
    print(f"[FATAL] Cannot start Kimi MCP Server: {e}", file=sys.stderr)
    sys.exit(1)


def _validate_inputs(code: str, venture: str, file_count: int) -> tuple:
    """
    Validate all inputs before calling Kimi.
    Returns (is_valid, error_message)
    """
    if not code or not code.strip():
        return False, "No code provided. Pass the code snippet or file content."

    if venture not in VALID_VENTURES:
        return False, (
            f"Invalid venture '{venture}'. "
            f"Must be one of: {', '.join(sorted(VALID_VENTURES))}"
        )

    if file_count > 3:
        return False, (
            f"Too many files ({file_count}). Max 3 per call to prevent "
            f"Antigravity diff-state corruption. Use sequential requests."
        )

    if len(code) > 800000:  # ~200K tokens rough limit
        return False, "Code too large. Trim to relevant functions only."

    return True, ""


@mcp.tool()
def debug_with_kimi(
    code: str,
    venture: str,  # Fix 2: required — must specify project (crypton/devicelytics/career_dreamz/harleve/default)
    error_message: str = "",
    file_name: str = "unknown",
    thinking: bool = True,
    temperature: float = 0.3
) -> str:
    """
    Send code and error to Kimi K2.6 for deep debugging analysis.

    Use this tool when:
    - Standard models (Gemini/Claude) failed to fix the bug after 2+ attempts
    - The bug involves complex async/concurrency logic
    - Deep architectural reasoning across multiple files is needed
    - Context window was exhausted during debug session

    Args:
        code: The code snippet or full file content to analyze
        error_message: The error message, stack trace, or bug description
        file_name: Name of the file being debugged (for context)
        venture: Which project this belongs to (crypton/devicelytics/career_dreamz/harleve/default)
        thinking: Enable deep reasoning mode (default True for debugging tasks)
        temperature: 0.3 for debugging (deterministic), 0.7 for brainstorming

    Returns:
        Kimi's analysis and suggested fix as formatted text
    """

    # Validate inputs
    is_valid, error = _validate_inputs(code, venture, file_count=1)
    if not is_valid:
        return f"[KIMI ERROR] {error}"

    try:  # Fix 3: uses module-level client

        # Build prompt with clear structure
        system_prompt = (
            "You are an expert Python debugger and software architect. "
            "Analyze the provided code and error carefully. "
            "Provide: (1) Root cause analysis, (2) Specific fix, (3) Explanation of why the fix works. "
            "Be concise but thorough. Do not hallucinate changes — only suggest what the code actually needs."
        )

        user_content = f"""File: {file_name}

Error/Stack Trace:
```
{error_message if error_message else "No explicit error — behavior is incorrect or unexpected"}
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
            thinking=thinking,
            temperature=temperature,
            max_tokens=4096,
            file_count=1
        )

        # Format response with metadata
        response_parts = ["=== KIMI K2.6 ANALYSIS ===\n"]

        if "reasoning" in result:
            response_parts.append(f"[Reasoning Mode: ON]\n{result['reasoning'][:500]}...\n")

        response_parts.append(result["content"])

        # Add usage info
        usage = result.get("usage", {})
        if usage:
            response_parts.append(
                f"\n\n--- Usage: {usage.get('prompt_tokens', '?')} prompt + "
                f"{usage.get('completion_tokens', '?')} completion tokens ---"
            )

        response_parts.append(
            f"\n--- Monthly requests used: {client.usage.get('total', '?')}/1000 ---"
        )

        return "\n".join(response_parts)

    except RuntimeError as e:
        if "monthly limit" in str(e).lower():
            return (
                f"[KIMI LIMIT REACHED] {e}\n\n"
                f"Revert to Antigravity native models (Gemini/Claude). "
                f"Kimi quota resets on the 1st of next month."
            )
        return f"[KIMI ERROR] {e}"

    except Exception as e:
        error_trace = traceback.format_exc()
        return f"[KIMI ERROR] Unexpected error: {str(e)}\n\n{error_trace}"


@mcp.tool()
def analyze_architecture_with_kimi(
    code_files: str,
    question: str,
    venture: str,  # Fix 2: required — must specify project
    thinking: bool = True
) -> str:
    """
    Ask Kimi K2.6 deep architectural questions about your codebase.

    Use this when:
    - You need to understand tradeoffs between design patterns
    - You're planning a refactor across multiple modules
    - You need to evaluate if a proposed architecture will scale

    Args:
        code_files: Multiple files concatenated with clear separators
        question: The specific architectural question
        venture: Project name (crypton/devicelytics/career_dreamz/harleve/default)
        thinking: Enable deep reasoning (default True)

    Returns:
        Architectural analysis and recommendations
    """

    # Count files by separator
    file_count = code_files.count("=== FILE:")
    if file_count == 0:
        file_count = 1  # Single file without separator

    is_valid, error = _validate_inputs(code_files, venture, file_count)
    if not is_valid:
        return f"[KIMI ERROR] {error}"

    try:  # Fix 3: uses module-level client

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
            thinking=thinking,
            temperature=0.3,
            max_tokens=4096,
            file_count=file_count
        )

        response = f"=== KIMI K2.6 ARCHITECTURE REVIEW ===\n\n{result['content']}"

        if "reasoning" in result:
            response += f"\n\n[Reasoning]: {result['reasoning'][:300]}..."

        response += f"\n\n--- Monthly requests: {client.usage.get('total', '?')}/1000 ---"

        return response

    except Exception as e:
        return f"[KIMI ERROR] {str(e)}"


@mcp.tool()
def get_kimi_usage() -> str:
    """
    Check current Kimi NIM API usage statistics.

    Returns:
        Formatted usage report including total requests and per-venture breakdown
    """
    try:  # Fix 3: uses module-level client
        usage = client.usage

        report = [
            "=== KIMI NIM USAGE REPORT ===",
            f"Month: {usage.get('month', 'unknown')}",
            f"Total requests: {usage.get('total', 0)}/1000",
            "",
            "Per-venture breakdown:"
        ]

        for venture, count in sorted(usage.get("by_venture", {}).items()):
            report.append(f"  {venture}: {count}")

        remaining = 1000 - usage.get("total", 0)
        report.extend([
            "",
            f"Remaining this month: {remaining}",
            f"Suggested daily budget: ~{remaining // 30} requests/day"
        ])

        return "\n".join(report)

    except Exception as e:
        return f"[KIMI ERROR] Could not load usage: {str(e)}"


if __name__ == "__main__":
    # Run the MCP server
    print("Starting Kimi K2.6 MCP Server...", file=sys.stderr)
    print("Tools registered: debug_with_kimi, analyze_architecture_with_kimi, get_kimi_usage", file=sys.stderr)
    mcp.run()
