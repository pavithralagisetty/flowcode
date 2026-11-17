"""Gemini with Grounding with Google Search, used only as a link finder."""

from __future__ import annotations

import logging

import httpx
from google import genai
from google.genai import errors, types

from flowcode_agent.config import require_env
from flowcode_agent.fetch import USER_AGENT
from flowcode_agent.models import SearchResult
from flowcode_agent.retry import is_http_retryable, with_backoff

log = logging.getLogger(__name__)

PROMPT = (
    "Search Google for: {query}\n\n"
    "Find the most recent relevant web pages (news articles, press releases, official company pages). "
    "Prefer pages from the last few months. List the pages you found."
)
REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com", "grounding-api-redirect")


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, errors.APIError) and is_http_retryable(getattr(exc, "code", None))


class GeminiSearchProvider:
    name = "gemini"

    def __init__(self, model: str, timeout: float = 15):
        self.model = model
        self.client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
        self.http = httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT})
        self.requests = 0

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        """Run one grounded request and return its source links. The answer text is ignored."""
        config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        response = with_backoff(
            lambda: self.client.models.generate_content(
                model=self.model, contents=PROMPT.format(query=query), config=config
            ),
            _retryable,
        )
        self.requests += 1
        meta = response.candidates[0].grounding_metadata if response.candidates else None
        if meta is None:
            log.info("No grounding metadata for query %r", query)
            return []
        log.debug("Gemini search queries for %r: %s", query, meta.web_search_queries)
        results: list[SearchResult] = []
        seen: set[str] = set()
        for chunk in meta.grounding_chunks or []:
            if not chunk.web or not chunk.web.uri:
                continue
            url = self._resolve_redirect(chunk.web.uri)
            if url and url not in seen:
                seen.add(url)
                snippet = f"gemini queries: {', '.join(meta.web_search_queries or [])}"
                results.append(SearchResult(url=url, title=chunk.web.title, snippet=snippet, query=query,
                                            provider=self.name))
            if len(results) >= max_results:
                break
        return results

    def _resolve_redirect(self, uri: str) -> str | None:
        """Grounding links are often Google redirect links; follow them to the real page."""
        if not any(h in uri for h in REDIRECT_HOSTS):
            return uri
        try:
            resp = self.http.head(uri)
            if resp.status_code >= 400 or any(h in str(resp.url) for h in REDIRECT_HOSTS):
                resp = self.http.get(uri)
            return str(resp.url)
        except httpx.HTTPError as exc:
            log.warning("Could not resolve redirect %s: %s", uri, exc)
            return None
