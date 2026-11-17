"""Gemini as the reasoning model (alternative to Claude)."""

from __future__ import annotations

from google import genai
from google.genai import errors, types

from flowcode_agent.config import require_env
from flowcode_agent.providers.llm_base import BaseLLM
from flowcode_agent.retry import is_http_retryable, with_backoff


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, errors.APIError) and is_http_retryable(getattr(exc, "code", None))


class GeminiLLM(BaseLLM):
    name = "gemini"

    def __init__(self, max_tokens: int = 16000):
        super().__init__()
        self.client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
        self.max_tokens = max_tokens

    def _raw(self, system: str, messages: list[dict[str, str]], model: str) -> tuple[str, int, int]:
        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json", max_output_tokens=self.max_tokens
        )
        response = with_backoff(
            lambda: self.client.models.generate_content(model=model, contents=contents, config=config), _retryable
        )
        usage = response.usage_metadata
        tin = (usage.prompt_token_count or 0) if usage else 0
        tout = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0
        return response.text or "", tin, tout
