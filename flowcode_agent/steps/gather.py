"""Step 2: search, fetch, and save evidence pages."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from flowcode_agent import calllog
from flowcode_agent.context import RunContext
from flowcode_agent.db import now_iso
from flowcode_agent.fetch import FetchedPage, domain_of, extract_news_links, is_blocked, text_hash, url_hash
from flowcode_agent.models import Company, Evidence, SearchResult
from flowcode_agent.providers.handoff import NeedsAnswer

log = logging.getLogger(__name__)

QUERY_TEMPLATES = [
    "{brand} campaign",
    "{brand} new ad",
    "{brand} commercial site:ispot.tv",
    "{brand} sponsorship",
    "{brand} partnership",
    "{brand} tour OR festival OR pop up",
    "{brand} new packaging",
    "{brand} limited edition",
    "{brand} loyalty program OR sweepstakes",
    "{brand} QR code",
    "{brand} site:prnewswire.com OR site:businesswire.com",
]
# What each search looks for, shown as the live step while it runs.
SEARCH_LABELS = {
    "{brand} campaign": "Looking for current campaigns",
    "{brand} new ad": "Looking for new ads",
    "{brand} commercial site:ispot.tv": "Looking for TV commercials",
    "{brand} sponsorship": "Looking for sponsorships",
    "{brand} partnership": "Looking for partnerships",
    "{brand} tour OR festival OR pop up": "Looking for tours, festivals and pop-ups",
    "{brand} new packaging": "Looking for new packaging",
    "{brand} limited edition": "Looking for limited editions",
    "{brand} loyalty program OR sweepstakes": "Looking for loyalty programs and sweepstakes",
    "{brand} QR code": "Checking how they use QR codes",
    "{brand} site:prnewswire.com OR site:businesswire.com": "Reading press releases",
}
NEWSROOM_PATHS = ["/news", "/newsroom", "/press", "/press-releases"]
NEWSROOM_ARTICLE_LIMIT = 10
THIN_RESULTS = 15  # if the company name finds fewer new pages than this, also search each brand
MAX_EXTRA_BRANDS = 3
ONGOING_RE = re.compile(r"\b(through|until|ongoing|upcoming|coming soon|will (run|launch|open|begin|take place))\b",
                        re.IGNORECASE)


def cached_search(ctx: RunContext, query: str, max_results: int) -> list[SearchResult]:
    """Search, cached by (provider, query, day) so a query is never repeated on the same day."""
    day = ctx.today.isoformat()
    cached = ctx.db.get_cached_search(ctx.search.name, query, day)
    if cached is not None:
        calllog.record("search_cached", provider=ctx.search.name, query=query, results=len(cached),
                       urls=[r.url for r in cached])
        return cached
    before = _search_counters(ctx)
    failed = False
    try:
        results = ctx.search.search(query, max_results=max_results)
    except NeedsAnswer:
        raise
    except Exception as exc:  # noqa: BLE001 - one failed query should not stop the run
        log.warning("Search failed for %r: %s", query, exc)
        results, failed = [], True
    after = _search_counters(ctx)
    calllog.record("search", provider=ctx.search.name, model=getattr(ctx.search, "model", None), query=query,
                   web_searches=after[0] - before[0], input_tokens=after[1] - before[1],
                   output_tokens=after[2] - before[2], results=len(results), urls=[r.url for r in results],
                   failed=failed, **({"list_price_usd": round(after[3] - before[3], 6)} if after[3] > before[3] else {}))
    if not failed:
        ctx.db.save_search(ctx.search.name, query, day, results)
    return results


def _search_counters(ctx: RunContext) -> tuple[int, int, int, float]:
    s = ctx.search
    return s.requests, getattr(s, "input_tokens", 0), getattr(s, "output_tokens", 0), getattr(s, "cost_usd", 0.0)


def is_fresh(published: date | None, text: str, today: date, recency_days: int) -> bool:
    """Unknown dates and recent pages pass. Older pages pass only if they describe something ongoing or upcoming."""
    if published is None or published >= today - timedelta(days=recency_days):
        return True
    mentions_now = re.search(rf"\b({today.year}|{today.year + 1})\b", text) is not None
    return mentions_now and ONGOING_RE.search(text) is not None


class Gatherer:
    """Saves evidence for one company during one run, honoring the page cap and dedupe rules."""

    def __init__(self, ctx: RunContext, company: Company, run_id: int):
        self.ctx = ctx
        self.company = company
        self.run_id = run_id
        self.cfg = ctx.config.research
        self.tried: set[str] = set()
        self.new_ids: list[int] = []

    @property
    def full(self) -> bool:
        return len(self.new_ids) >= self.cfg.max_pages_per_company

    def consider(self, url: str) -> FetchedPage | None:
        """Fetch a URL and save it as evidence if it is new, allowed, and fresh. Returns the page if fetched."""
        if self.full or url in self.tried or is_blocked(url, self.cfg.blocked_domains):
            return None
        self.tried.add(url)
        if self.ctx.db.evidence_exists(self.company.id, url=url):
            return None
        page = self.ctx.fetcher(url)
        if page is None or is_blocked(page.final_url, self.cfg.blocked_domains):
            return None
        self.save(page)
        return page

    def save(self, page: FetchedPage) -> int | None:
        uh, th = url_hash(page.final_url), text_hash(page.text)
        if self.ctx.db.evidence_exists(self.company.id, url_hash=uh, text_hash=th):
            return None
        if not is_fresh(page.published_date, page.text, self.ctx.today, self.cfg.recency_days):
            log.info("Too old (%s): %s", page.published_date, page.final_url)
            return None
        ev = Evidence(
            company_id=self.company.id, run_id=self.run_id, url=page.url, final_url=page.final_url, url_hash=uh,
            text_hash=th, title=page.title, publisher=page.publisher, published_date=page.published_date,
            date_known=page.published_date is not None, fetched_at=now_iso(), text=page.text,
        )
        new_id = self.ctx.db.add_evidence(ev)
        if new_id:
            self.new_ids.append(new_id)
            log.info("Saved evidence %d: %s", new_id, page.final_url)
        return new_id

    def run_queries(self, brand: str) -> None:
        for template in QUERY_TEMPLATES:
            if self.full:
                return
            query = template.format(brand=brand)
            calllog.progress(SEARCH_LABELS.get(template, "Searching the web"))
            for result in cached_search(self.ctx, query, self.ctx.config.search.max_results_per_query):
                self.consider(result.url)

    def read_newsroom(self) -> None:
        """Find the newsroom by search and common paths, then read its recent articles."""
        domain = self.company.domain
        calllog.progress("Reading the company newsroom")
        pages = [f"https://{domain}{p}" for p in NEWSROOM_PATHS]
        for r in cached_search(self.ctx, f"{self.company.name} newsroom press releases",
                               self.ctx.config.search.max_results_per_query):
            if domain_of(r.url).endswith(domain):
                pages.insert(0, r.url)
        followed = 0
        for url in dict.fromkeys(pages):
            page = self.consider(url)
            if page is None:
                continue
            for link in extract_news_links(page.html, page.final_url):
                if followed >= NEWSROOM_ARTICLE_LIMIT:
                    return
                if self.consider(link):
                    followed += 1


def gather(ctx: RunContext, company: Company, run_id: int) -> list[int]:
    """Run the fixed searches and newsroom read. Returns ids of newly saved evidence."""
    g = Gatherer(ctx, company, run_id)
    g.read_newsroom()
    g.run_queries(company.name)
    if len(g.new_ids) < THIN_RESULTS:
        extra = [b for b in company.brands if b.lower() != company.name.lower()][:MAX_EXTRA_BRANDS]
        for brand in extra:
            g.run_queries(brand)
    log.info("Gathered %d new evidence pages for %s", len(g.new_ids), company.name)
    return g.new_ids
