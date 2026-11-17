"""Provider factories. Adding a provider means one new file plus one line here."""

from __future__ import annotations

from flowcode_agent.config import Config
from flowcode_agent.providers.llm_base import LLM
from flowcode_agent.providers.search_base import SearchProvider


def get_search_provider(config: Config) -> SearchProvider:
    """Pick the search provider named in config."""
    name = config.search.provider
    if name == "gemini":
        from flowcode_agent.providers.search_gemini import GeminiSearchProvider

        return GeminiSearchProvider(model=config.search.gemini_model, timeout=config.research.fetch_timeout_seconds)
    if name == "claude":
        from flowcode_agent.providers.search_claude import ClaudeSearchProvider

        return ClaudeSearchProvider(model=config.search.claude_model)
    if name == "handoff":
        from flowcode_agent.providers.handoff import HandoffSearch

        return HandoffSearch(config.path("handoff"))
    if name == "fake":
        from flowcode_agent.providers.search_fake import FakeSearchProvider

        return FakeSearchProvider({})
    raise ValueError(f"Unknown search provider: {name}")


def get_llm(config: Config) -> LLM:
    """Pick the LLM provider named in config."""
    name = config.llm.provider
    if name == "anthropic":
        from flowcode_agent.providers.llm_anthropic import AnthropicLLM

        return AnthropicLLM(max_tokens=config.llm.max_tokens)
    if name == "gemini":
        from flowcode_agent.providers.llm_gemini import GeminiLLM

        return GeminiLLM(max_tokens=config.llm.max_tokens)
    if name == "handoff":
        from flowcode_agent.providers.handoff import HandoffLLM

        return HandoffLLM(config.path("handoff"))
    raise ValueError(f"Unknown LLM provider: {name}")
