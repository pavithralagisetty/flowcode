"""Claude's server-side web search tool, used only as a link finder."""

from __future__ import annotations

import logging

import anthropic

from flowcode_agent.config import require_env
from flowcode_agent.models import SearchResult

log = logging.getLogger(__name__)

PROMPT = (
    "Use web search to find the most recent relevant web pages for: {query}\n\n"
    "Prefer news articles, press releases, and official company pages from the last few months. "
    "Search once or twice, then reply with just the list of page URLs."
)
MAX_CONTINUATIONS = 3


class ClaudeSearchProvider:
    name = "claude"

    def __init__(self, model: str, max_uses: int = 3):
        require_env("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(max_retries=5)
        self.model = model
        self.max_uses = max_uses
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        """Return the URLs from web_search_tool_result blocks. Claude's written answer is ignored."""
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": self.max_uses}]
        messages = [{"role": "user", "content": PROMPT.format(query=query)}]
        results: list[SearchResult] = []
        seen: set[str] = set()
        for _ in range(MAX_CONTINUATIONS):
            response = self.client.messages.create(model=self.model, max_tokens=4000, tools=tools,
                                                   messages=messages)
            server_use = getattr(response.usage, "server_tool_use", None)
            self.requests += (server_use.web_search_requests if server_use else 0) or 0
            self.input_tokens += response.usage.input_tokens
            self.output_tokens += response.usage.output_tokens
            for block in response.content:
                if block.type != "web_search_tool_result":
                    continue
                if not isinstance(block.content, list):  # an error object, e.g. max_uses_exceeded
                    log.info("Web search error for %r: %s", query, getattr(block.content, "error_code", "?"))
                    continue
                for item in block.content:
                    if item.url not in seen:
                        seen.add(item.url)
                        results.append(SearchResult(url=item.url, title=item.title, snippet=item.page_age,
                                                    query=query, provider=self.name))
            if response.stop_reason != "pause_turn":
                break
            # Server-side loop paused; resend so it resumes where it left off.
            messages = [messages[0], {"role": "assistant", "content": response.content}]
        return results[:max_results]
