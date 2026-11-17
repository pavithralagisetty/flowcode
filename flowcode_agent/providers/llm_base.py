"""LLM interface plus the shared JSON validate-and-retry logic."""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)


class LLM(Protocol):
    input_tokens: int
    output_tokens: int

    def complete_json(self, system: str, user: str, schema: type[M], model: str) -> M: ...


class LLMError(RuntimeError):
    """The model did not return valid JSON after one retry, or refused."""


def schema_instructions(schema: type[BaseModel]) -> str:
    return (
        "\n\nReturn only a JSON object matching this JSON Schema. No other text.\n"
        f"```json\n{json.dumps(schema.model_json_schema(), indent=1)}\n```"
    )


def extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may have fences or stray text around it."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


class BaseLLM:
    """Subclasses implement `_raw`; this class handles schema, validation, retry, and token counts."""

    name = "base"

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def _raw(self, system: str, messages: list[dict[str, str]], model: str) -> tuple[str, int, int]:
        """Return (text, input_tokens, output_tokens). `messages` alternate user/assistant."""
        raise NotImplementedError

    def complete_json(self, system: str, user: str, schema: type[M], model: str) -> M:
        """Ask for JSON matching `schema`; on a validation error retry once with the error included."""
        system_full = system + schema_instructions(schema)
        messages = [{"role": "user", "content": user}]
        last_error: Exception | None = None
        for attempt in (1, 2):
            text, tin, tout = self._raw(system_full, messages, model)
            self.input_tokens += tin
            self.output_tokens += tout
            log.info("LLM %s/%s %s: %d in, %d out tokens", self.name, model, schema.__name__, tin, tout)
            try:
                return schema.model_validate_json(extract_json(text))
            except (ValidationError, ValueError) as exc:
                last_error = exc
                log.warning("Invalid JSON for %s (attempt %d): %s", schema.__name__, attempt, str(exc)[:300])
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That JSON did not validate:\n{str(exc)[:2000]}\n"
                                                "Return the corrected JSON only."},
                ]
        raise LLMError(f"{schema.__name__}: invalid JSON after retry: {last_error}")
