"""Step 4: match moments to plays in the Flowcode playbook."""

from __future__ import annotations

import json
import logging

import yaml

from flowcode_agent import calllog
from flowcode_agent.context import RunContext
from flowcode_agent.models import Company, MatchList, Moment, Opportunity, SimilarStory

log = logging.getLogger(__name__)


def match_moments(ctx: RunContext, company: Company, moments: list[tuple[int, Moment]]) -> list[Opportunity]:
    """Pick a play for each moment and write the pitch. Drops moments with no touchpoint and no play."""
    if not moments:
        return []
    playbook = ctx.playbook()
    plays = {p["id"]: p for p in playbook["plays"]}
    listing = [{"moment_index": i, "title": m.title, "type": m.type, "summary": m.summary,
                "timing": m.timing.model_dump(mode="json"), "physical_touchpoints": m.physical_touchpoints}
               for i, (_, m) in enumerate(moments)]
    user = (f"Company: {company.name} (industry: {company.industry})\n\n"
            f"## Flowcode playbook\n```yaml\n{yaml.safe_dump(playbook, sort_keys=False)}```\n\n"
            f"## Moments\n```json\n{json.dumps(listing, indent=1)}\n```")
    calllog.progress("Matching findings to Flowcode plays")
    with calllog.tagged(step="match", moments=len(moments)):
        answer = ctx.llm.complete_json(ctx.prompt("match"), user, MatchList, ctx.main_model)

    products = set(playbook.get("products", []))
    by_index = {m.moment_index: m for m in answer.matches}
    opportunities = []
    for i, (moment_id, moment) in enumerate(moments):
        match = by_index.get(i)
        play_id = match.play_id if match and match.play_id in plays else None
        if not moment.physical_touchpoints and play_id is None:
            log.info("Dropping moment with no touchpoint and no play: %s", moment.title)
            continue
        if match is None:
            continue
        proof = plays[play_id]["proof"] if play_id else None
        opportunities.append(Opportunity(
            moment_id=moment_id,
            moment=moment,
            play_id=play_id,
            why_flowcode_fits=match.why_flowcode_fits,
            flowcode_products=[p for p in match.flowcode_products if p in products] or match.flowcode_products,
            similar_story=SimilarStory(**proof) if proof else None,
            suggested_first_pitch=match.suggested_first_pitch,
        ))
    return opportunities
