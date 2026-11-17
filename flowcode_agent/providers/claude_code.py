"""Claude Code as the AI and the search engine, through the `claude` CLI in headless mode.

Runs on the Claude Code login (for example a claude.ai plan) instead of API credits: the
ANTHROPIC_API_KEY variable is removed from the subprocess environment so the CLI cannot
fall back to the key. Calls run in an empty temp folder so no project CLAUDE.md is loaded.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from flowcode_agent.models import SearchResult
from flowcode_agent.providers.llm_base import BaseLLM, LLMError

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 600
SEARCH_PROMPT = (
    "Use the WebSearch tool to find the most recent relevant web pages for: {query}\n"
    "Prefer news articles, press releases and official company pages from the last few months. "
    "Search once or twice, then reply DONE."
)
SEARCH_SYSTEM = "You find web pages with the WebSearch tool. Do not answer from memory."


def claude_available() -> bool:
    return shutil.which("claude") is not None


def _run(prompt: str, system: str, model: str, tools: str, stream: bool) -> list[dict[str, Any]]:
    """Run one headless Claude Code call and return its JSON output events."""
    if not claude_available():
        raise LLMError("The `claude` command is not installed or not on PATH")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cmd = ["claude", "-p", "--model", model, "--system-prompt", system, "--tools", tools,
           "--no-session-persistence", "--output-format", "stream-json" if stream else "json"]
    if stream:
        cmd.append("--verbose")
    if tools:
        cmd += ["--allowedTools", tools]
    with tempfile.TemporaryDirectory(prefix="flowcode-cc-") as cwd:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, cwd=cwd,
                              timeout=TIMEOUT_SECONDS)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if proc.returncode != 0 or result is None or result.get("is_error"):
        detail = (result or {}).get("result") or proc.stderr.strip() or proc.stdout.strip()
        raise LLMError(f"Claude Code call failed: {str(detail)[:300]}")
    return events


def _usage(result: dict[str, Any]) -> tuple[int, int, float]:
    """(input tokens incl. cache, output tokens, list-price cost the CLI reports)."""
    u = result.get("usage") or {}
    tokens_in = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                 + u.get("cache_read_input_tokens", 0))
    return tokens_in, u.get("output_tokens", 0), result.get("total_cost_usd") or 0.0


class ClaudeCodeLLM(BaseLLM):
    """Structured AI calls answered by headless Claude Code (no tools)."""

    name = "claude_code"

    def _raw(self, system: str, messages: list[dict[str, str]], model: str) -> tuple[str, int, int]:
        if len(messages) == 1:
            prompt = messages[0]["content"]
        else:  # a retry after invalid JSON: replay the exchange as one prompt
            prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        result = _run(prompt, system, model, tools="", stream=False)[-1]
        tokens_in, tokens_out, cost = _usage(result)
        self.last_call_info = {"list_price_usd": cost, "billing": "claude_code_login"}
        return result.get("result") or "", tokens_in, tokens_out


class ClaudeCodeSearch:
    """Web search through Claude Code's WebSearch tool. Only the tool's result links are used."""

    name = "claude_code"

    def __init__(self, model: str):
        if not claude_available():
            raise RuntimeError("Claude Code mode needs the `claude` command installed and logged in")
        self.model = model
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0  # list-price equivalent the CLI reports; not billed to API credits

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        events = _run(SEARCH_PROMPT.format(query=query), SEARCH_SYSTEM, self.model, tools="WebSearch", stream=True)
        results: list[SearchResult] = []
        seen: set[str] = set()
        for event in events:
            tool_result = event.get("tool_use_result")
            if not isinstance(tool_result, dict):
                continue
            self.requests += tool_result.get("searchCount", 0) or 0
            for block in tool_result.get("results") or []:
                for item in block.get("content", []) if isinstance(block, dict) else []:
                    url = item.get("url")
                    if url and url not in seen:
                        seen.add(url)
                        results.append(SearchResult(url=url, title=item.get("title"), query=query,
                                                    provider=self.name))
        result = next(e for e in reversed(events) if e.get("type") == "result")
        tokens_in, tokens_out, cost = _usage(result)
        self.cost_usd += cost
        self.input_tokens += tokens_in
        self.output_tokens += tokens_out
        return results[:max_results]
