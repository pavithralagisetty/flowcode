"""Run every step for one company, saving after each so a crashed run can resume."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flowcode_agent import calllog
from flowcode_agent.config import Config
from flowcode_agent.context import RunContext
from flowcode_agent.db import STAGES, Database
from flowcode_agent.fetch import Fetcher
from flowcode_agent.models import Company, CompanyScore, Opportunity
from flowcode_agent.providers import get_llm, get_search_provider
from flowcode_agent.steps.gather import gather
from flowcode_agent.steps.match import match_moments
from flowcode_agent.steps.moments import find_moments
from flowcode_agent.steps.report import write_report
from flowcode_agent.steps.resolve import mark_customer, resolve_company
from flowcode_agent.steps.score import is_over, score
from flowcode_agent.steps.verify import verify

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    company: Company
    run_id: int
    status: str
    score: CompanyScore | None = None
    report_dir: Path | None = None
    new_evidence: int = 0
    opportunities: list[Opportunity] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def build_context(config: Config, db: Database | None = None, search=None, llm=None, fetcher=None) -> RunContext:
    """Make a context with real providers unless test doubles are passed in."""
    return RunContext(
        config=config,
        db=db or Database(config.db_target()),
        search=search or get_search_provider(config),
        llm=llm or get_llm(config),
        fetcher=fetcher or Fetcher(timeout=config.research.fetch_timeout_seconds),
    )


def _reached(status: str, stage: str) -> bool:
    return status in STAGES and STAGES.index(status) >= STAGES.index(stage)


def _known_company(ctx: RunContext, user_input: str, domain: str | None) -> Company | None:
    """A company already in the database, so resolve can be skipped."""
    found = ctx.db.find_company(domain) if domain else ctx.db.find_company_by_name(user_input)
    if found and found.industry != "unknown":
        return mark_customer(ctx, found)
    return None


def run_company(ctx: RunContext, user_input: str, domain: str | None = None, mode: str = "run") -> RunResult:
    """Run (or resume) the full pipeline for one company. `mode` is 'run' or 'refresh'."""
    with calllog.writing_to(ctx.db), calllog.tagged(session=uuid.uuid4().hex[:10], mode=mode):
        return _run_company(ctx, user_input, domain, mode)


def _run_company(ctx: RunContext, user_input: str, domain: str | None, mode: str) -> RunResult:
    start_search, start_in, start_out = ctx.search.requests, *_tokens(ctx)
    with calllog.tagged(company=user_input):
        calllog.progress("Starting research")
        company = _known_company(ctx, user_input, domain) or resolve_company(ctx, user_input, domain)
    unfinished = ctx.db.unfinished_run(company.id, mode)
    if unfinished:
        run_id, status = unfinished["id"], unfinished["status"]
        log.info("Resuming run %d for %s from stage '%s'", run_id, company.name, status)
    else:
        run_id, status = ctx.db.create_run(company.id, mode), "started"
        ctx.db.set_run_status(run_id, "resolved")
        status = "resolved"
    try:
        with calllog.tagged(company=company.name, run_id=run_id):
            result = _run_stages(ctx, company, run_id, status, refresh=(mode == "refresh"))
    finally:
        end_in, end_out = _tokens(ctx)
        ctx.db.add_usage(run_id, company.id, ctx.search.requests - start_search, end_in - start_in, end_out - start_out)
    result.usage = ctx.db.get_usage(run_id)
    return result


def _tokens(ctx: RunContext) -> tuple[int, int]:
    """AI tokens so far, including tokens the search provider spends (Claude web search uses the model)."""
    return (ctx.llm.input_tokens + getattr(ctx.search, "input_tokens", 0),
            ctx.llm.output_tokens + getattr(ctx.search, "output_tokens", 0))


def _run_stages(ctx: RunContext, company: Company, run_id: int, status: str, refresh: bool) -> RunResult:
    db = ctx.db
    if not _reached(status, "gathered"):
        gather(ctx, company, run_id)
        db.set_run_status(run_id, status := "gathered")
    new_ids = {e.id for e in db.list_evidence(company.id) if e.run_id == run_id}

    if refresh and not new_ids:
        db.set_run_status(run_id, "no_change")
        log.info("No new evidence for %s", company.name)
        return RunResult(company=company, run_id=run_id, status="no_change")

    if not _reached(status, "moments"):
        moments = find_moments(ctx, company)
        db.save_moments(company.id, run_id, moments)
        db.set_run_status(run_id, status := "moments")

    if not _reached(status, "matched"):
        opps = match_moments(ctx, company, db.list_moments(run_id))
        db.replace_opportunities(company.id, run_id, opps)
        db.set_run_status(run_id, status := "matched")

    if not _reached(status, "verified"):
        opps = verify(ctx, company.id, db.list_opportunities(run_id))
        db.replace_opportunities(company.id, run_id, opps)
        db.set_run_status(run_id, status := "verified")

    if not _reached(status, "scored"):
        opps = db.list_opportunities(run_id)
        _, cs = score(ctx, company, opps)
        db.replace_opportunities(company.id, run_id, opps)
        db.save_company_score(company.id, run_id, cs)
        db.set_run_status(run_id, status := "scored")

    cs = db.get_company_score(company.id, run_id)
    ranked = sorted(
        [o for o in db.list_opportunities(run_id) if o.verified and o.score is not None and not is_over(o, ctx.today)],
        key=lambda o: o.score, reverse=True,
    )
    calllog.progress("Writing the report")
    folder = write_report(ctx.config.path("outputs"), company, cs, ranked, db.get_evidence_map(company.id), run_id,
                          ctx.today, refresh=refresh, new_evidence_ids=new_ids)
    db.save_report(company.id, run_id, folder.name, (folder / "report.json").read_text(),
                   (folder / "report.md").read_text())
    db.set_run_status(run_id, "done")
    return RunResult(company=company, run_id=run_id, status="done", score=cs, report_dir=folder,
                     new_evidence=len(new_ids), opportunities=ranked)
