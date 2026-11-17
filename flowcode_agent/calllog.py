"""Per-call log for cost analysis, plus live progress messages.

Every AI call and every search is appended as one JSON line to `paths.calls_log`
(default logs/api_calls.jsonl) with the step it belongs to, the pages that were sent,
and the token and web-search counts. Steps attach that context with `tagged(...)`.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("flowcode_agent.calls")
progress_log = logging.getLogger("flowcode_agent.progress")

_tags: ContextVar[dict[str, Any]] = ContextVar("calllog_tags", default={})
_lock = threading.Lock()
_path: Path | None = None


def configure(path: Path | None) -> None:
    """Set the JSONL file to write to (None turns logging to file off, e.g. in tests)."""
    global _path
    _path = path
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def tagged(**tags: Any) -> Iterator[None]:
    """Attach fields (run_id, company, step, pages...) to every call recorded inside the block."""
    token = _tags.set({**_tags.get(), **tags})
    try:
        yield
    finally:
        _tags.reset(token)


def record(kind: str, **fields: Any) -> dict[str, Any]:
    """Write one call to the log. `kind` is 'llm', 'search' or 'search_cached'."""
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **_tags.get(), **fields}
    log.debug("%s", entry)
    if _path:
        line = json.dumps(entry, default=str, ensure_ascii=False)
        with _lock, _path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return entry


def read(path: Path, run_id: int | None = None) -> list[dict[str, Any]]:
    """All logged calls, optionally for one run."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if run_id is None or row.get("run_id") == run_id:
            rows.append(row)
    return rows


def progress(message: str) -> None:
    """Announce the step that is running now (shown live in the app and in CLI logs)."""
    progress_log.info(message, extra={"progress": message})
