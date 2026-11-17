"""Per-call log for cost analysis, plus live progress messages.

Every AI call and every search is saved as a row in the `api_calls` table of the run's database,
with the step it belongs to, the pages that were sent, and the token and web-search counts.
Steps attach that context with `tagged(...)`; the pipeline picks the database with `writing_to(db)`.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Iterator

log = logging.getLogger("flowcode_agent.calls")
progress_log = logging.getLogger("flowcode_agent.progress")

_tags: ContextVar[dict[str, Any]] = ContextVar("calllog_tags", default={})
_sink: ContextVar[Any] = ContextVar("calllog_sink", default=None)  # a Database with add_api_call


@contextmanager
def writing_to(db: Any) -> Iterator[None]:
    """Save calls recorded inside the block to this database (each thread uses its own connection)."""
    token = _sink.set(db)
    try:
        yield
    finally:
        _sink.reset(token)


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
    sink = _sink.get()
    if sink is not None:
        try:
            sink.add_api_call(entry)
        except Exception as exc:  # noqa: BLE001 - a logging failure must never stop a research run
            log.warning("Could not save call log entry: %s", exc)
    return entry


def progress(message: str) -> None:
    """Announce the step that is running now (shown live in the app and in CLI logs)."""
    progress_log.info(message, extra={"progress": message})
