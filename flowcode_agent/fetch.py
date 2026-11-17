"""Page fetching and main-text extraction."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

from flowcode_agent.retry import is_http_retryable, with_backoff

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
MIN_TEXT_CHARS = 200


@dataclass
class FetchedPage:
    url: str
    final_url: str
    html: str
    text: str
    title: str | None
    publisher: str | None
    published_date: date | None


def domain_of(url: str) -> str:
    """Host without `www.`, lowercased."""
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str, blocked_domains: list[str]) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in blocked_domains)


def url_hash(url: str) -> str:
    """Hash of a URL with scheme, `www.`, fragment, and trailing slash removed."""
    p = urlparse(url)
    norm = f"{domain_of(url)}{p.path.rstrip('/')}{'?' + p.query if p.query else ''}".lower()
    return hashlib.sha256(norm.encode()).hexdigest()


def text_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def extract_page(html: str, url: str, final_url: str | None = None) -> FetchedPage | None:
    """Pull main text, title, publisher, and date from HTML. None if there is no usable text."""
    doc = trafilatura.bare_extraction(html, url=final_url or url, with_metadata=True, include_comments=False,
                                      as_dict=True)
    if not doc or len((doc.get("text") or "").strip()) < MIN_TEXT_CHARS:
        return None
    return FetchedPage(
        url=url,
        final_url=final_url or url,
        html=html,
        text=doc["text"].strip(),
        title=doc.get("title"),
        publisher=doc.get("sitename") or domain_of(final_url or url),
        published_date=_parse_date(doc.get("date")),
    )


class Fetcher:
    """Fetches pages with a browser user agent. Returns None (and logs) on any failure."""

    def __init__(self, timeout: float = 15):
        self.client = httpx.Client(follow_redirects=True, timeout=timeout, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9",
        })

    def __call__(self, url: str) -> FetchedPage | None:
        try:
            resp = with_backoff(lambda: self._get(url), _retryable_fetch, attempts=3)
        except Exception as exc:  # noqa: BLE001 - any fetch failure is logged and skipped
            log.info("Fetch failed %s: %s", url, exc)
            return None
        if resp.status_code >= 400:
            log.info("Fetch %s returned %d", url, resp.status_code)
            return None
        if "html" not in resp.headers.get("content-type", "html"):
            log.info("Skipping non-HTML %s", url)
            return None
        try:
            page = extract_page(resp.text, url, str(resp.url))
        except Exception as exc:  # noqa: BLE001
            log.info("Extraction failed %s: %s", url, exc)
            return None
        if page is None:
            log.info("No main text in %s", url)
        return page

    def _get(self, url: str) -> httpx.Response:
        resp = self.client.get(url)
        if is_http_retryable(resp.status_code):
            raise _RetryableStatus(resp)
        return resp

    def check_alive(self, url: str) -> bool:
        """True if the URL still loads (status under 400)."""
        try:
            return self.client.get(url).status_code < 400
        except httpx.HTTPError:
            return False


class _RetryableStatus(httpx.HTTPError):
    def __init__(self, resp: httpx.Response):
        super().__init__(f"HTTP {resp.status_code}")
        self.response = resp


def _retryable_fetch(exc: Exception) -> bool:
    return isinstance(exc, (_RetryableStatus, httpx.TimeoutException, httpx.ConnectError))


LINK_RE = re.compile(r"""href=["']([^"'#]+)["']""", re.IGNORECASE)
NEWS_PATH_RE = re.compile(r"/(news|newsroom|press|press-releases|stories|media|blog)/[^/?]+", re.IGNORECASE)


def extract_news_links(html: str, base_url: str, limit: int = 15) -> list[str]:
    """Links on the same site that look like individual news or press articles."""
    base_domain = domain_of(base_url)
    links: list[str] = []
    for href in LINK_RE.findall(html):
        url = urljoin(base_url, href)
        if domain_of(url).endswith(base_domain) and NEWS_PATH_RE.search(urlparse(url).path) and url not in links:
            links.append(url)
        if len(links) >= limit:
            break
    return links
