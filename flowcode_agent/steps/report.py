"""Step 7: write report.md and report.json for a company."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from flowcode_agent.models import Company, CompanyScore, Evidence, Opportunity

MAX_QUOTE_WORDS = 19  # quotes in the report stay under 20 words
TOP_N = 5
CHANNELS = {
    "TV": {"tv_ad"},
    "social": {"digital_campaign"},
    "email": {"loyalty_or_contest"},
    "events": {"live_event", "sponsorship"},
    "packaging": {"packaging", "product_launch"},
    "retail": {"retail_expansion"},
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"


def short_quote(quote: str) -> str:
    words = quote.split()
    return " ".join(words) if len(words) <= MAX_QUOTE_WORDS else " ".join(words[:MAX_QUOTE_WORDS]) + " …"


def channels_covered(opps: list[Opportunity]) -> list[str]:
    types = {o.moment.type for o in opps}
    return [c for c, ts in CHANNELS.items() if types & ts]


def sources(opp: Opportunity, evidence: dict[int, Evidence]) -> list[dict]:
    out = []
    for link in opp.moment.evidence:
        ev = evidence.get(link.evidence_id)
        if ev:
            out.append({"evidence_id": ev.id, "publisher": ev.publisher or "unknown",
                        "date": ev.published_date.isoformat() if ev.published_date else "date unknown",
                        "quote": short_quote(link.exact_quote), "url": ev.final_url})
    return out


def is_new(opp: Opportunity, new_evidence_ids: set[int]) -> bool:
    return any(l.evidence_id in new_evidence_ids for l in opp.moment.evidence)


def _opportunity_md(rank: int, o: Opportunity, evidence: dict[int, Evidence]) -> list[str]:
    breakdown = ", ".join(f"{k} {r.rating} ({r.reason})" for k, r in o.factor_ratings.items())
    story = f"{o.similar_story.name} ({o.similar_story.url}): {o.similar_story.result}" if o.similar_story else "none"
    lines = [
        f"### {rank}. {o.moment.title} (score {o.score:.2f})",
        f"**What's happening:** {o.moment.summary} *Timing: {o.moment.timing.date_text or 'not stated'}"
        f" ({o.moment.timing.status}).*",
        f"**Where a code goes:** {', '.join(o.moment.physical_touchpoints) or 'not stated'}",
        f"**Why Flowcode fits:** {o.why_flowcode_fits}",
        f"**Flowcode products:** {', '.join(o.flowcode_products) or 'not stated'}",
        f"**Similar Flowcode story:** {story}",
        f"**Suggested first pitch:** {o.suggested_first_pitch}",
        f"**Score breakdown:** {breakdown}",
        "**Sources:**",
    ]
    lines += [f'- {s["publisher"]}, {s["date"]}: "{s["quote"]}" {s["url"]}' for s in sources(o, evidence)]
    return lines + [""]


def build_markdown(company: Company, cs: CompanyScore, opps: list[Opportunity], evidence: dict[int, Evidence],
                   run_date: date, refresh: bool, new_evidence_ids: set[int]) -> str:
    customer = {"case_study": "yes (published case study)", "likely": "likely (logo strip or gallery)"}.get(
        company.customer_tier or "", "no")
    lines = [
        f"# {company.name}: where Flowcode could help",
        f"Run date {run_date.isoformat()} · Company score {cs.score:.2f} · Existing Flowcode customer: {customer}",
        "",
        "## Snapshot",
        f"**Industry:** {company.industry} · **Main brands:** {', '.join(company.brands) or 'not found'}",
        "",
        cs.snapshot or "No verified current activity was found.",
        "",
        "**Company score breakdown:** " + ", ".join(f"{k} {r.rating} ({r.reason})" for k, r in cs.factors.items()),
        "",
        "## Top opportunities",
    ]
    top, rest = opps[:TOP_N], opps[TOP_N:]
    if not top:
        lines += ["No verified, current opportunities were found.", ""]
    for rank, o in enumerate(top, 1):
        lines += _opportunity_md(rank, o, evidence)
    lines += ["## Other opportunities"]
    lines += [f"- {o.moment.title} ({o.score:.2f}): {o.moment.summary.split('. ')[0].rstrip('.')}." for o in rest]
    lines += ["" if rest else "None.", "", "## New since last run"]
    if not refresh:
        lines += ["Only filled in refresh mode."]
    else:
        new = [o for o in opps if is_new(o, new_evidence_ids)]
        lines += [f"- {o.moment.title} ({o.score:.2f})" for o in new] or ["Nothing new."]
    covered = channels_covered(opps)
    gaps = [c for c in CHANNELS if c not in covered]
    lines += ["", "## Gaps", "Nothing found for: " + (", ".join(gaps) if gaps else "none, every channel is covered"),
              ""]
    return "\n".join(lines)


def build_json(company: Company, cs: CompanyScore, opps: list[Opportunity], evidence: dict[int, Evidence],
               run_id: int, run_date: date, refresh: bool, new_evidence_ids: set[int]) -> dict:
    return {
        "company": company.model_dump(),
        "run_id": run_id,
        "run_date": run_date.isoformat(),
        "mode": "refresh" if refresh else "run",
        "company_score": cs.model_dump(),
        "opportunities": [
            {**o.model_dump(mode="json"), "sources": sources(o, evidence), "new": is_new(o, new_evidence_ids)}
            for o in opps
        ],
        "channels_covered": channels_covered(opps),
    }


def write_report(out_dir: Path, company: Company, cs: CompanyScore, opps: list[Opportunity],
                 evidence: dict[int, Evidence], run_id: int, run_date: date, refresh: bool = False,
                 new_evidence_ids: set[int] | None = None) -> Path:
    """Write both report files and return the folder."""
    new_ids = new_evidence_ids or set()
    folder = out_dir / slugify(company.name)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(build_markdown(company, cs, opps, evidence, run_date, refresh, new_ids))
    data = build_json(company, cs, opps, evidence, run_id, run_date, refresh, new_ids)
    (folder / "report.json").write_text(json.dumps(data, indent=2))
    return folder
