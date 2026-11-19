"""Step 1: turn a name or URL into an official company record."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from flowcode_agent import calllog
from flowcode_agent.context import RunContext
from flowcode_agent.fetch import domain_of, is_blocked
from flowcode_agent.models import Candidate, Company, ResolvedCompany
from flowcode_agent.steps.gather import cached_search

log = logging.getLogger(__name__)
EXCERPT_CHARS = 1500


class AmbiguousCompany(Exception):
    """The input could mean several companies; the user has to choose."""

    def __init__(self, query: str, candidates: list[Candidate], confidence: float):
        super().__init__(f"'{query}' is ambiguous (confidence {confidence:.2f})")
        self.query = query
        self.candidates = candidates
        self.confidence = confidence


class CompanyNotFound(Exception):
    """The input does not match any real company."""

    def __init__(self, query: str):
        super().__init__(f"No company found for '{query}'")
        self.query = query


def normalize_name(name: str) -> str:
    """Lowercase and drop everything but letters and digits."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def customer_tier(names: list[str], customers_path: Path) -> str | None:
    """'case_study' or 'likely' if any name matches existing_customers.yaml, else None."""
    data = yaml.safe_load(customers_path.read_text()) or {}
    wanted = {normalize_name(n) for n in names if n}
    for tier, key in (("case_study", "case_study_customers"), ("likely", "likely_customers")):
        if wanted & {normalize_name(c) for c in data.get(key, [])}:
            return tier
    return None


def looks_like_url(text: str) -> bool:
    return text.startswith(("http://", "https://", "www.")) or bool(re.fullmatch(r"[\w-]+(\.[\w-]+)+(/.*)?", text))


def _gather_context(ctx: RunContext, query: str, domain: str | None) -> tuple[str, list[dict]]:
    """Search results plus short page excerpts for the AI to resolve from, and the pages used."""
    blocked = ctx.config.research.blocked_domains
    lines: list[str] = []
    pages: list[str] = []
    if domain:
        pages.append(f"https://{domain}")
    results = cached_search(ctx, f"{query} official website", ctx.config.search.max_results_per_query)
    for r in results:
        if is_blocked(r.url, blocked):
            continue
        lines.append(f"- {r.title or ''} | {r.url}")
        if len(pages) < 3 and r.url not in pages:
            pages.append(r.url)
    excerpts, used = [], []
    for url in pages:
        page = ctx.fetcher(url)
        if page:
            excerpt = page.text[:EXCERPT_CHARS]
            excerpts.append(f"### {page.final_url}\nTitle: {page.title}\n{excerpt}")
            used.append({"url": page.final_url, "chars_sent": len(excerpt)})
    context = "## Search results\n" + "\n".join(lines) + "\n\n## Page excerpts\n" + "\n\n".join(excerpts)
    return context, used


def resolve_company(ctx: RunContext, user_input: str, domain: str | None = None) -> Company:
    """Resolve a company. Raises CompanyNotFound or AmbiguousCompany when no domain was given and it is unclear."""
    user_input = user_input.strip()
    if not domain and looks_like_url(user_input):
        domain = domain_of(user_input)
    if domain:
        domain = domain_of(domain)
        existing = ctx.db.find_company(domain)
        if existing and existing.industry != "unknown":
            return mark_customer(ctx, existing)

    query = domain or user_input
    calllog.progress("Finding the official website", 1)
    context, used = _gather_context(ctx, query if not domain else f"{user_input} {domain}", domain)
    user = f"User input: {user_input}\nKnown domain: {domain or 'none'}\n\n{context}"
    calllog.progress("Identifying the company and its brands", 2)
    with calllog.tagged(step="resolve", pages=used):
        answer = ctx.llm.complete_json(ctx.prompt("resolve"), user, ResolvedCompany, ctx.cheap_model)
    log.info("Resolved %r -> %s (%s), confidence %.2f", user_input, answer.official_name, answer.official_domain,
             answer.confidence)

    if not domain and not answer.is_company:
        raise CompanyNotFound(user_input)
    if not domain and answer.confidence < ctx.config.resolve.min_confidence:
        candidates = answer.candidates or (
            [Candidate(name=answer.official_name, domain=answer.official_domain)] if answer.official_name else [])
        if not candidates:
            raise CompanyNotFound(user_input)
        raise AmbiguousCompany(user_input, candidates, answer.confidence)

    company = Company(
        name=answer.official_name or user_input,
        domain=domain or domain_of(answer.official_domain),
        industry=answer.industry or "unknown",
        brands=answer.brands,
    )
    return mark_customer(ctx, company)


def mark_customer(ctx: RunContext, company: Company) -> Company:
    tier = customer_tier([company.name, *company.brands], ctx.config.path("existing_customers"))
    company = company.model_copy(update={"customer_tier": tier, "is_existing_customer": tier is not None})
    return ctx.db.upsert_company(company).model_copy(update={"customer_tier": tier})
