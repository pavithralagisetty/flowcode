"""SearchProvider interface."""

from __future__ import annotations

from typing import Protocol

from flowcode_agent.models import SearchResult

__all__ = ["SearchProvider", "SearchResult"]


class SearchProvider(Protocol):
    """Returns links for a query. Any written answer from the provider is ignored."""

    name: str
    requests: int  # number of paid/real requests made, for usage logging

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]: ...
