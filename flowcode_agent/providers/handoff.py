"""Handoff providers: no API key. Each AI request or search is written to a file for an outside agent
(for example Claude Code) to answer. The run pauses, and resumes once the answer file exists.

Layout under `paths.handoff`:
  search.json            {"query": ["url", ...], ...}     answers to searches
  pending/<key>.md       an AI request waiting for an answer (system prompt, user message, schema)
  answers/<key>.json     the JSON answer to that request
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from flowcode_agent.models import SearchResult
from flowcode_agent.providers.llm_base import extract_json, schema_instructions

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)


class NeedsAnswer(Exception):
    """The run paused because a handoff request has no answer yet."""

    def __init__(self, path: Path, kind: str):
        super().__init__(f"Waiting for a {kind} answer: {path}")
        self.path = path
        self.kind = kind


def request_key(schema_name: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{schema_name}\n{system}\n{user}".encode()).hexdigest()[:16]


class HandoffSearch:
    name = "handoff"

    def __init__(self, folder: Path):
        self.file = folder / "search.json"
        self.pending = folder / "pending_searches.txt"
        folder.mkdir(parents=True, exist_ok=True)
        self.requests = 0

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        answers = json.loads(self.file.read_text()) if self.file.exists() else {}
        if query not in answers:
            existing = self.pending.read_text().splitlines() if self.pending.exists() else []
            if query not in existing:
                self.pending.write_text("\n".join(existing + [query]) + "\n")
            raise NeedsAnswer(self.pending, "search")
        self.requests += 1
        return [SearchResult(url=u, query=query, provider=self.name) for u in answers[query][:max_results]]


class HandoffLLM:
    name = "handoff"

    def __init__(self, folder: Path):
        self.pending_dir = folder / "pending"
        self.answers_dir = folder / "answers"
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.answers_dir.mkdir(parents=True, exist_ok=True)
        self.input_tokens = 0
        self.output_tokens = 0

    def complete_json(self, system: str, user: str, schema: type[M], model: str) -> M:
        key = request_key(schema.__name__, system, user)
        answer = self.answers_dir / f"{key}.json"
        pending = self.pending_dir / f"{key}.md"
        if answer.exists():
            try:
                result = schema.model_validate_json(extract_json(answer.read_text()))
            except ValidationError as exc:
                pending.write_text(self._request_text(schema, system, user, error=str(exc)))
                answer.unlink()
                raise NeedsAnswer(pending, f"{schema.__name__} (previous answer was invalid)") from exc
            pending.unlink(missing_ok=True)
            return result
        pending.write_text(self._request_text(schema, system, user))
        raise NeedsAnswer(pending, schema.__name__)

    @staticmethod
    def _request_text(schema: type[BaseModel], system: str, user: str, error: str | None = None) -> str:
        note = f"\n\n## Previous answer failed validation\n{error}\n" if error else ""
        return (f"# Request: {schema.__name__}\n\nWrite the JSON answer to `answers/<same name>.json`.{note}\n\n"
                f"## System\n{system}{schema_instructions(schema)}\n\n## User\n{user}\n")
