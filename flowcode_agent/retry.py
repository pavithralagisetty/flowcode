"""Retry with exponential backoff for rate limits and server errors."""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)


def with_backoff(
    fn: Callable[[], T],
    is_retryable: Callable[[Exception], bool],
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Call `fn`, retrying retryable errors with jittered exponential backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - filtered by is_retryable
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            log.warning("Retryable error (%s), attempt %d/%d, sleeping %.1fs", exc, attempt, attempts, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def is_http_retryable(status: int | None) -> bool:
    """Rate limits and server errors are worth retrying."""
    return status is not None and (status == 429 or status >= 500)
