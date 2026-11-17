"""Fake search provider for tests: maps query substrings to URLs."""

from __future__ import annotations

from flowcode_agent.models import SearchResult


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results: dict[str, list[str]]):
        """`results` maps a lowercase substring of the query to the URLs it returns."""
        self.results = {k.lower(): v for k, v in results.items()}
        self.requests = 0
        self.queries: list[str] = []

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        self.requests += 1
        self.queries.append(query)
        urls: list[str] = []
        for key, found in self.results.items():
            if key in query.lower():
                urls.extend(u for u in found if u not in urls)
        return [SearchResult(url=u, title=None, query=query, provider=self.name) for u in urls[:max_results]]
