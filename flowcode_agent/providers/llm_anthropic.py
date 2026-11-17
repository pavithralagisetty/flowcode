"""Claude through the Anthropic SDK."""

from __future__ import annotations

import anthropic

from flowcode_agent.config import require_env
from flowcode_agent.providers.llm_base import BaseLLM, LLMError

# Models that accept the server-side refusal fallback ("default" form routes by refusal category).
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}


class AnthropicLLM(BaseLLM):
    name = "anthropic"

    def __init__(self, max_tokens: int = 16000):
        super().__init__()
        require_env("ANTHROPIC_API_KEY")
        # The SDK retries 429, 5xx, and connection errors with backoff.
        self.client = anthropic.Anthropic(max_retries=5)
        self.max_tokens = max_tokens

    def _raw(self, system: str, messages: list[dict[str, str]], model: str) -> tuple[str, int, int]:
        kwargs = dict(model=model, max_tokens=self.max_tokens, system=system, messages=messages)
        if model in FALLBACK_MODELS:
            response = self.client.beta.messages.create(
                **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
            )
        else:
            response = self.client.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            raise LLMError(f"{model} declined the request")
        text = "".join(b.text for b in response.content if b.type == "text")
        return text, response.usage.input_tokens, response.usage.output_tokens
