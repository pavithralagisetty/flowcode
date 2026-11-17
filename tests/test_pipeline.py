"""End to end with fake search, saved pages, and a scripted LLM."""

import json
import re

import pytest

from flowcode_agent.batch import BatchItem, run_many, write_ranking
from flowcode_agent.evaluate import golden_pass, quality
from flowcode_agent.models import (EvidenceLink, FactorRating, Match, MatchList, MergeGroups, Moment, MomentList,
                                   OpportunityRatings, ResolvedCompany, ScoreAnswer, Timing, VerifyAnswer)
from flowcode_agent.pipeline import run_company
from flowcode_agent.providers.search_fake import FakeSearchProvider
from flowcode_agent.steps import match as match_step
from tests.conftest import TODAY, FakeFetcher, FakeLLM, make_ctx

PAGES = {
    "https://acmesoda.com/": "acme_home.html",
    "https://acmesoda.com/news/fizz-fest": "acme_tour.html",
    "https://copycat.example/fizz-fest": "acme_tour_copy.html",
    "https://prwire.example/holiday-cans": "acme_packaging.html",
    "https://gazette.example/fun-run": "acme_old.html",
}
EVIDENCE_RE = re.compile(r"<evidence evidence_id=(\d+)>\nURL: (\S+)")


def moments_reply(user: str) -> MomentList:
    ids = {url: int(i) for i, url in EVIDENCE_RE.findall(user)}
    out = []
    if "https://acmesoda.com/news/fizz-fest" in ids:
        out.append(Moment(
            title="Fizz Fest 20 city tour", type="live_event",
            summary="Acme Soda announced Fizz Fest, a 20 city music tour starting December 5, 2026.",
            timing=Timing(date_text="Dec 2026 to Mar 2027", status="upcoming"),
            physical_touchpoints=["tickets", "stadium signs", "merch"],
            evidence=[EvidenceLink(evidence_id=ids["https://acmesoda.com/news/fizz-fest"],
                                   exact_quote="Acme Soda today announced Fizz Fest, a 20 city music tour that kicks "
                                               "off on December 5, 2026 and runs through March 2027.")]))
    if "https://prwire.example/holiday-cans" in ids:
        pid = ids["https://prwire.example/holiday-cans"]
        out.append(Moment(
            title="Limited edition holiday cans", type="packaging",
            summary="Acme Soda is rolling out limited edition holiday cans in all 50 states.",
            timing=Timing(date_text="Nov 10 to Jan 15", status="ongoing"),
            physical_touchpoints=["can"],
            evidence=[EvidenceLink(evidence_id=pid,
                                   exact_quote="Acme Soda is rolling out limited edition holiday cans in all 50 "
                                               "states this month."),
                      EvidenceLink(evidence_id=pid, exact_quote="Every can comes with a free car.")]))
        out.append(Moment(
            title="Old fun run", type="sponsorship", summary="A past fun run.",
            timing=Timing(status="past"), physical_touchpoints=["bib"],
            evidence=[EvidenceLink(evidence_id=pid, exact_quote="The holiday cans will be on shelves")]))
    return MomentList(moments=out)


def match_reply(user: str) -> MatchList:
    listing = json.loads(user.split("## Moments\n```json\n")[1].split("\n```")[0])
    plays = {"live_event": "live_event", "packaging": "packaging_signup", "sponsorship": "live_event"}
    return MatchList(matches=[Match(moment_index=m["moment_index"], play_id=plays.get(m["type"]),
                                    why_flowcode_fits="Codes on the physical touchpoint.",
                                    flowcode_products=["Flowcodes (branded QR codes)"],
                                    suggested_first_pitch="Let's put a code on it.") for m in listing])


def score_reply(user: str) -> ScoreAnswer:
    listing = json.loads(user.split("```json\n")[1].split("\n```")[0])
    f = lambda n: FactorRating(rating=n, reason="because")  # noqa: E731
    return ScoreAnswer(
        opportunities=[OpportunityRatings(index=o["index"], timing=f(5), playbook_fit=f(5 if i == 0 else 4),
                                          reach=f(4), evidence_strength=f(4), openness=f(3))
                       for i, o in enumerate(listing)],
        company_size=f(4), snapshot="Acme Soda is touring and selling holiday cans.")


def make_llm() -> FakeLLM:
    return FakeLLM({
        ResolvedCompany: lambda u: ResolvedCompany(official_name="Acme Soda", official_domain="acmesoda.com",
                                                   industry="food and beverage", brands=["Acme Soda"],
                                                   confidence=0.9),
        MomentList: moments_reply,
        MergeGroups: lambda u: MergeGroups(groups=[]),
        MatchList: match_reply,
        VerifyAnswer: lambda u: VerifyAnswer(verdict="yes", supported_claim="", reason="quotes match"),
        ScoreAnswer: score_reply,
    })


@pytest.fixture
def ctx(config, db):
    return make_ctx(config, db, search=FakeSearchProvider({"acme": list(PAGES)}), llm=make_llm(),
                    fetcher=FakeFetcher(PAGES))


def test_full_run_writes_verified_report(ctx):
    result = run_company(ctx, "Acme Soda")
    assert result.status == "done"
    titles = [o.moment.title for o in result.opportunities]
    assert titles == ["Fizz Fest 20 city tour", "Limited edition holiday cans"]  # past event removed
    cans = result.opportunities[1]
    assert len(cans.moment.evidence) == 1  # the made up quote was removed
    assert cans.similar_story.name == "LOVE CORN"
    # 0.25*5 + 0.25*5 + 0.2*4 + 0.15*4 + 0.15*3 = 4.35
    assert result.opportunities[0].score == 4.35

    md = (result.report_dir / "report.md").read_text()
    assert "# Acme Soda: where Flowcode could help" in md
    assert "### 1. Fizz Fest 20 city tour (score 4.35)" in md
    assert "Existing Flowcode customer: no" in md
    assert "flowcode.com/case-study/ceremony-of-roses" in md
    for line in md.splitlines():
        if line.startswith("- ") and '"' in line:
            quote = line.split('"')[1]
            assert len(quote.split()) < 21  # 19 words plus an ellipsis at most
    data = json.loads((result.report_dir / "report.json").read_text())
    assert data["company_score"]["score"] == result.score.score
    assert result.usage["llm_input_tokens"] > 0 and result.usage["search_requests"] > 0


def test_company_score_math(ctx, config):
    result = run_company(ctx, "Acme Soda")
    f = result.score.factors
    w = config.scoring.company_weights
    expected = (w["best_opportunity"] * 4.35 + w["strong_count"] * f["strong_count"].rating
                + w["company_size"] * 4 + w["industry_fit"] * f["industry_fit"].rating
                + w["new_activity"] * f["new_activity"].rating)
    assert result.score.score == round(expected, 2)
    assert f["strong_count"].rating == 3  # two strong opportunities


def test_refresh_twice_finds_nothing_new(ctx):
    run_company(ctx, "Acme Soda")
    first = run_company(ctx, "Acme Soda", "acmesoda.com", mode="refresh")
    second = run_company(ctx, "Acme Soda", "acmesoda.com", mode="refresh")
    assert first.status == "no_change" and second.status == "no_change"


def test_refresh_with_new_evidence_fills_new_section(ctx):
    pages = dict(PAGES)
    del pages["https://prwire.example/holiday-cans"]
    ctx.fetcher = FakeFetcher(pages)
    run_company(ctx, "Acme Soda")
    ctx.fetcher = FakeFetcher(PAGES)
    result = run_company(ctx, "Acme Soda", "acmesoda.com", mode="refresh")
    assert result.status == "done" and result.new_evidence == 1
    md = (result.report_dir / "report.md").read_text()
    new_section = md.split("## New since last run")[1].split("## Gaps")[0]
    assert "Limited edition holiday cans" in new_section
    assert "Fizz Fest" not in new_section


def test_crash_resumes_without_redoing_finished_steps(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("crash in match")

    monkeypatch.setattr("flowcode_agent.pipeline.match_moments", boom)
    with pytest.raises(RuntimeError):
        run_company(ctx, "Acme Soda")
    monkeypatch.setattr("flowcode_agent.pipeline.match_moments", match_step.match_moments)
    moments_calls = sum(1 for name, _ in ctx.llm.calls if name == "MomentList")
    result = run_company(ctx, "Acme Soda")
    assert result.status == "done"
    assert sum(1 for name, _ in ctx.llm.calls if name == "MomentList") == moments_calls  # moments not redone


def test_eval_numbers_and_golden(ctx):
    result = run_company(ctx, "Acme Soda")
    q = quality(ctx.db, result.company, result.run_id, TODAY, check_alive=ctx.fetcher.check_alive)
    assert q.opportunities == 2
    assert q.claims_verified == 0.75  # 3 of 4 quotes passed
    assert q.links_loading == 1.0
    assert set(q.channels) == {"events", "packaging"}
    ok, plays = golden_pass(ctx.db, result.run_id, "packaging_signup", TODAY)
    assert ok and plays == ["live_event", "packaging_signup"]


def test_batch_ranking(config, tmp_path):
    from flowcode_agent.db import Database

    def make(db: Database):
        return make_ctx(config, db, search=FakeSearchProvider({"acme": list(PAGES)}), llm=make_llm(),
                        fetcher=FakeFetcher(PAGES))

    items = run_many(config, make, [BatchItem(name="Acme Soda", domain="acmesoda.com")])
    md, csv_path = write_ranking(config.path("outputs"), items)
    assert "| 1 | Acme Soda |" in md.read_text()
    assert "Acme Soda" in csv_path.read_text()


def test_call_log_records_every_ai_call_with_step_and_pages(ctx, config):
    from flowcode_agent import calllog

    result = run_company(ctx, "Acme Soda")
    rows = calllog.read(config.path("calls_log"), run_id=result.run_id)
    llm_steps = [r["step"] for r in rows if r["kind"] == "llm"]
    assert {"moments", "match", "verify", "score"} <= set(llm_steps)
    moments = next(r for r in rows if r["kind"] == "llm" and r["step"] == "moments")
    assert moments["company"] == "Acme Soda" and moments["pages"][0]["url"].startswith("https://")
    assert all("input_tokens" in r for r in rows if r["kind"] == "llm")
    assert any(r["kind"] in ("search", "search_cached") for r in rows)
