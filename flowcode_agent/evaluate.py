"""Golden test plus quality numbers for runs, written to outputs/eval.md."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from flowcode_agent.db import Database
from flowcode_agent.models import Company
from flowcode_agent.steps.report import TOP_N, channels_covered
from flowcode_agent.steps.score import is_over

log = logging.getLogger(__name__)

# Company -> the play used in its real Flowcode story. flowcode.com is always blocked, so it cannot leak in.
GOLDEN = {"Eddie Bauer": "product_tags", "LOVE CORN": "packaging_signup"}


@dataclass
class Quality:
    company: str
    run_id: int
    claims_verified: float | None
    sources_last_90_days: float | None
    links_loading: float | None
    opportunities: int
    channels: list[str]


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


def quality(db: Database, company: Company, run_id: int, today: date,
            check_alive: Callable[[str], bool] | None = None) -> Quality:
    """Quality numbers for one finished run."""
    all_opps = db.list_opportunities(run_id)
    checked = sum(o.verification.quotes_checked for o in all_opps if o.verification)
    passed = sum(o.verification.quotes_passed for o in all_opps if o.verification and o.verified)
    ranked = [o for o in all_opps if o.verified and o.score is not None and not is_over(o, today)]
    evidence = db.get_evidence_map(company.id)
    ids = {l.evidence_id for o in ranked for l in o.moment.evidence if l.evidence_id in evidence}
    cutoff = today - timedelta(days=90)
    recent = sum(1 for i in ids if evidence[i].published_date and evidence[i].published_date >= cutoff)
    alive = sum(1 for i in ids if check_alive(evidence[i].final_url)) if check_alive else None
    return Quality(
        company=company.name, run_id=run_id,
        claims_verified=_share(passed, checked),
        sources_last_90_days=_share(recent, len(ids)),
        links_loading=_share(alive, len(ids)) if alive is not None else None,
        opportunities=len(ranked),
        channels=channels_covered(ranked),
    )


def golden_pass(db: Database, run_id: int, expected_play: str, today: date) -> tuple[bool, list[str]]:
    """Pass if any top opportunity uses the expected play."""
    ranked = sorted([o for o in db.list_opportunities(run_id) if o.verified and o.score is not None
                     and not is_over(o, today)], key=lambda o: o.score, reverse=True)[:TOP_N]
    plays = [o.play_id or "none" for o in ranked]
    return expected_play in plays, plays


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def write_eval(path: Path, golden: list[tuple[str, str, bool, list[str], str | None]], qualities: list[Quality]) -> Path:
    lines = ["# Evaluation", "", "## Golden test (flowcode.com blocked)", "",
             "| Company | Expected play | Top plays found | Result |", "|---|---|---|---|"]
    for name, expected, ok, plays, error in golden:
        result = f"error: {error}" if error else ("PASS" if ok else "FAIL")
        lines.append(f"| {name} | {expected} | {', '.join(plays) or 'none'} | {result} |")
    lines += ["", "## Quality numbers", "",
              "| Company | Run | Claims verified | Sources last 90 days | Links load | Opportunities | Channels |",
              "|---|---|---|---|---|---|---|"]
    for q in qualities:
        lines.append(f"| {q.company} | {q.run_id} | {_pct(q.claims_verified)} | {_pct(q.sources_last_90_days)} | "
                     f"{_pct(q.links_loading)} | {q.opportunities} | {', '.join(q.channels) or 'none'} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path
