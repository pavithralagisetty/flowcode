"""Step 6: the AI rates factors 1 to 5; Python computes every weighted score."""

from __future__ import annotations

import json
import logging
from datetime import date

from flowcode_agent.config import ScoringConfig
from flowcode_agent import calllog
from flowcode_agent.context import RunContext
from flowcode_agent.models import Company, CompanyScore, Evidence, FactorRating, Opportunity, ScoreAnswer

log = logging.getLogger(__name__)
OPPORTUNITY_FACTORS = ["timing", "playbook_fit", "reach", "evidence_strength", "openness"]


def weighted(ratings: dict[str, FactorRating], weights: dict[str, float]) -> float:
    """Weighted sum of 1 to 5 ratings, rounded to 2 places."""
    return round(sum(weights[k] * ratings[k].rating for k in weights), 2)


def is_over(opp: Opportunity, today: date) -> bool:
    t = opp.moment.timing
    if t.status == "past":
        return True
    return t.end_date is not None and t.end_date < today


def scoreable(opps: list[Opportunity], today: date) -> list[Opportunity]:
    """Only verified opportunities that are not already over."""
    return [o for o in opps if o.verified and not is_over(o, today)]


def strong_count_rating(n: int) -> int:
    """none = 1, one = 2, two = 3, three = 4, four or more = 5."""
    return min(n, 4) + 1


def newest_date(opp: Opportunity, evidence: dict[int, Evidence]) -> date | None:
    dates = [evidence[l.evidence_id].published_date for l in opp.moment.evidence
             if l.evidence_id in evidence and evidence[l.evidence_id].published_date]
    return max(dates) if dates else None


def new_activity_rating(strong: list[Opportunity], evidence: dict[int, Evidence], today: date) -> FactorRating:
    dates = [d for d in (newest_date(o, evidence) for o in strong) if d]
    if not strong:
        return FactorRating(rating=1, reason="No strong opportunities")
    if not dates:
        return FactorRating(rating=2, reason="Strong opportunities have no known publish date")
    age = (today - max(dates)).days
    rating = 5 if age <= 30 else 4 if age <= 60 else 3 if age <= 90 else 2
    return FactorRating(rating=rating, reason=f"Newest strong opportunity source is {age} days old")


def industry_rating(industry: str, cfg: ScoringConfig) -> FactorRating:
    ind = industry.lower()
    hit = any(s.lower() in ind or ind in s.lower() for s in cfg.strong_industries) if ind != "unknown" else False
    return FactorRating(rating=5 if hit else 3, reason=f"Industry '{industry}'" + (" is a strong fit" if hit else ""))


def company_score(company: Company, opps: list[Opportunity], size: FactorRating, evidence: dict[int, Evidence],
                  cfg: ScoringConfig, today: date) -> dict[str, FactorRating]:
    """Company factor ratings. `best_opportunity` uses the best score itself (already on the 1 to 5 scale)."""
    best = max((o.score for o in opps if o.score is not None), default=None)
    strong = [o for o in opps if (o.score or 0) >= cfg.strong_threshold]
    return {
        "best_opportunity": FactorRating(rating=max(1, round(best)) if best else 1,
                                         reason=f"Best opportunity scores {best}" if best else "No opportunities"),
        "strong_count": FactorRating(rating=strong_count_rating(len(strong)),
                                     reason=f"{len(strong)} opportunities at or above {cfg.strong_threshold}"),
        "company_size": size,
        "industry_fit": industry_rating(company.industry, cfg),
        "new_activity": new_activity_rating(strong, evidence, today),
    }


def _company_weighted(factors: dict[str, FactorRating], best: float | None, weights: dict[str, float]) -> float:
    """Like `weighted`, but uses the exact best opportunity score instead of its rounded rating."""
    total = 0.0
    for k, w in weights.items():
        value = best if (k == "best_opportunity" and best is not None) else factors[k].rating
        total += w * value
    return round(total, 2)


def _describe(i: int, opp: Opportunity, evidence: dict[int, Evidence]) -> dict:
    sources = []
    for link in opp.moment.evidence:
        ev = evidence.get(link.evidence_id)
        if ev:
            sources.append({"publisher": ev.publisher, "published": str(ev.published_date or "unknown"),
                            "url": ev.final_url, "quote": link.exact_quote})
    return {"index": i, "title": opp.moment.title, "type": opp.moment.type, "summary": opp.moment.summary,
            "timing": opp.moment.timing.model_dump(mode="json"), "touchpoints": opp.moment.physical_touchpoints,
            "play_id": opp.play_id, "similar_story": opp.similar_story.model_dump() if opp.similar_story else None,
            "why_flowcode_fits": opp.why_flowcode_fits, "sources": sources}


def score(ctx: RunContext, company: Company, opps: list[Opportunity]) -> tuple[list[Opportunity], CompanyScore]:
    """Rate and score opportunities, then the company. Returns scored opportunities (best first)."""
    cfg = ctx.config.scoring
    evidence = ctx.db.get_evidence_map(company.id)
    live = scoreable(opps, ctx.today)
    size = FactorRating(rating=1, reason="No verified opportunities to judge from")
    snapshot = ""
    if live:
        listing = [_describe(i, o, evidence) for i, o in enumerate(live)]
        user = (f"Today's date: {ctx.today.isoformat()}\nCompany: {company.name} (industry: {company.industry})\n\n"
                f"## Opportunities\n```json\n{json.dumps(listing, indent=1)}\n```")
        calllog.progress(f"Scoring {len(live)} opportunities")
        with calllog.tagged(step="score", opportunities=len(live)):
            answer = ctx.llm.complete_json(ctx.prompt("score"), user, ScoreAnswer, ctx.main_model)
        ratings = {r.index: r for r in answer.opportunities}
        size, snapshot = answer.company_size, answer.snapshot
        for i, opp in enumerate(live):
            r = ratings.get(i)
            if r is None:
                log.warning("No ratings returned for %s", opp.moment.title)
                continue
            opp.factor_ratings = {k: getattr(r, k) for k in OPPORTUNITY_FACTORS}
            opp.score = weighted(opp.factor_ratings, cfg.opportunity_weights)
    scored = sorted([o for o in live if o.score is not None], key=lambda o: o.score, reverse=True)
    factors = company_score(company, scored, size, evidence, cfg, ctx.today)
    best = scored[0].score if scored else None
    cs = CompanyScore(score=_company_weighted(factors, best, cfg.company_weights), factors=factors, snapshot=snapshot)
    return scored, cs
