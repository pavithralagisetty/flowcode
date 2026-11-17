"""Step 3: find concrete moments (launches, ads, events...) in the evidence."""

from __future__ import annotations

import json
import logging

from flowcode_agent import calllog
from flowcode_agent.context import RunContext
from flowcode_agent.models import Company, Evidence, MergeGroups, Moment, MomentList
from flowcode_agent.steps.gather import is_fresh

log = logging.getLogger(__name__)

MERGE_SYSTEM = """You group moments that describe the same real world thing (the same campaign, launch, or event).
Return groups of moment indexes. Every index appears in exactly one group; a moment with no duplicate is a group of one.
Use only the moments given. Return only JSON matching the schema."""


def usable_evidence(ctx: RunContext, company: Company) -> list[Evidence]:
    """All stored evidence for the company that is still fresh today."""
    days = ctx.config.research.recency_days
    return [e for e in ctx.db.list_evidence(company.id) if is_fresh(e.published_date, e.text, ctx.today, days)]


def format_evidence(ev: Evidence, max_chars: int) -> str:
    published = ev.published_date.isoformat() if ev.published_date else "unknown"
    text = ev.text if len(ev.text) <= max_chars else ev.text[:max_chars] + "\n[page text continues]"
    return (f"<evidence evidence_id={ev.id}>\nURL: {ev.final_url}\nPublisher: {ev.publisher}\n"
            f"Published: {published}\nTitle: {ev.title}\n\n{text}\n</evidence>")


def find_moments_in_batch(ctx: RunContext, company: Company, batch: list[Evidence]) -> list[Moment]:
    max_chars = ctx.config.research.max_chars_per_page
    user = (f"Today's date: {ctx.today.isoformat()}\nTarget company: {company.name} ({company.domain})\n"
            f"Brands: {', '.join(company.brands) or 'none listed'}\n\n"
            + "\n\n".join(format_evidence(e, max_chars) for e in batch))
    pages = [{"evidence_id": e.id, "url": e.final_url, "chars_sent": min(len(e.text), max_chars)} for e in batch]
    with calllog.tagged(step="moments", pages=pages):
        answer = ctx.llm.complete_json(ctx.prompt("moments"), user, MomentList, ctx.cheap_model)
    valid_ids = {e.id for e in batch}
    moments = []
    for m in answer.moments:
        m.evidence = [link for link in m.evidence if link.evidence_id in valid_ids and link.exact_quote.strip()]
        if m.evidence:
            moments.append(m)
    return moments


def merge_moments(ctx: RunContext, moments: list[Moment]) -> list[Moment]:
    """Ask the AI which moments are duplicates, then combine their evidence in code."""
    if len(moments) < 2:
        return moments
    listing = [{"index": i, "title": m.title, "type": m.type, "summary": m.summary,
                "timing": m.timing.date_text} for i, m in enumerate(moments)]
    calllog.progress("Combining duplicate findings")
    with calllog.tagged(step="merge", moments=len(moments)):
        groups = ctx.llm.complete_json(MERGE_SYSTEM, json.dumps(listing, indent=1), MergeGroups, ctx.cheap_model).groups
    used: set[int] = set()
    merged: list[Moment] = []
    for group in groups + [[i] for i in range(len(moments))]:
        group = [i for i in group if 0 <= i < len(moments) and i not in used]
        if not group:
            continue
        used.update(group)
        merged.append(_combine([moments[i] for i in group]))
    log.info("Merged %d moments into %d", len(moments), len(merged))
    return merged


def _combine(group: list[Moment]) -> Moment:
    """Keep the first moment's text; union touchpoints and evidence links."""
    base = group[0].model_copy(deep=True)
    for other in group[1:]:
        base.physical_touchpoints += [t for t in other.physical_touchpoints if t not in base.physical_touchpoints]
        seen = {(e.evidence_id, e.exact_quote) for e in base.evidence}
        base.evidence += [e for e in other.evidence if (e.evidence_id, e.exact_quote) not in seen]
    return base


def find_moments(ctx: RunContext, company: Company) -> list[Moment]:
    """Read evidence in batches and return merged moments."""
    evidence = usable_evidence(ctx, company)
    size = ctx.config.research.evidence_batch_size
    moments: list[Moment] = []
    calllog.progress("Reading the pages for campaigns, launches and events")
    for start in range(0, len(evidence), size):
        batch = evidence[start : start + size]
        with calllog.tagged(batch=f"{start // size + 1}/{-(-len(evidence) // size)}"):
            moments += find_moments_in_batch(ctx, company, batch)
    log.info("Found %d moments in %d evidence pages", len(moments), len(evidence))
    return merge_moments(ctx, moments)
