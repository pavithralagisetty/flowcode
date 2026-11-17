"""Step 5: check every quote against the saved page, then ask the AI if the quotes support the claim."""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from flowcode_agent import calllog
from flowcode_agent.context import RunContext
from flowcode_agent.models import Evidence, Opportunity, Verification, VerifyAnswer

log = logging.getLogger(__name__)

QUOTE_CHARS = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
               "“": '"', "”": '"', "„": '"', "″": '"',
               "–": "-", "—": "-", "…": "...", " ": " "}


def normalize(text: str) -> str:
    """Unify quote marks, dashes, case, and whitespace."""
    text = unicodedata.normalize("NFKC", text)
    for src, dst in QUOTE_CHARS.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_score(quote: str, page_text: str) -> float:
    """1.0 for an exact (normalized) match, else the best fuzzy ratio of any same-length window."""
    q, t = normalize(quote), normalize(page_text)
    if not q:
        return 0.0
    if q in t:
        return 1.0
    best = 0.0
    n = len(q)
    starts = [0] + [m.end() for m in re.finditer(r"\s", t)]
    for start in starts:
        window = t[start : start + n]
        sm = SequenceMatcher(None, q, window, autojunk=False)
        if sm.real_quick_ratio() <= best or sm.quick_ratio() <= best:
            continue
        best = max(best, sm.ratio())
        if best >= 0.999:
            break
    return best


def check_quotes(opp: Opportunity, evidence: dict[int, Evidence], threshold: float) -> tuple[int, int]:
    """Remove evidence links whose quote is not on the page. Returns (checked, passed)."""
    kept = []
    for link in opp.moment.evidence:
        ev = evidence.get(link.evidence_id)
        score = quote_score(link.exact_quote, ev.text) if ev else 0.0
        if score >= threshold:
            kept.append(link)
        else:
            log.info("Quote not found (%.2f) in evidence %s: %r", score, link.evidence_id, link.exact_quote[:80])
    checked = len(opp.moment.evidence)
    opp.moment.evidence = kept
    return checked, len(kept)


def ai_check(ctx: RunContext, opp: Opportunity, evidence: dict[int, Evidence]) -> VerifyAnswer:
    quotes = "\n".join(
        f'- "{link.exact_quote}" ({evidence[link.evidence_id].publisher}, '
        f'{evidence[link.evidence_id].published_date or "date unknown"}, {evidence[link.evidence_id].final_url})'
        for link in opp.moment.evidence
    )
    timing = opp.moment.timing.date_text or "not stated"
    user = f"Claim: {opp.moment.summary}\nTiming claimed: {timing}\n\nQuotes:\n{quotes}"
    pages = sorted({evidence[link.evidence_id].final_url for link in opp.moment.evidence})
    with calllog.tagged(step="verify", opportunity=opp.moment.title, pages=pages):
        return ctx.llm.complete_json(ctx.prompt("verify"), user, VerifyAnswer, ctx.main_model)


def verify_opportunity(ctx: RunContext, opp: Opportunity, evidence: dict[int, Evidence]) -> Opportunity:
    """Verify one opportunity in place; sets `verified` and `verification`."""
    checked, passed = check_quotes(opp, evidence, ctx.config.verify.quote_match_threshold)
    result = Verification(quotes_checked=checked, quotes_passed=passed)
    if passed:
        answer = ai_check(ctx, opp, evidence)
        result.verdict, result.reason = answer.verdict, answer.reason
        if answer.verdict == "partly" and answer.supported_claim.strip():
            result.original_summary = opp.moment.summary
            opp.moment.summary = answer.supported_claim.strip()
    opp.verification = result
    opp.verified = passed > 0 and result.verdict in ("yes", "partly")
    return opp


def verify(ctx: RunContext, company_id: int, opps: list[Opportunity]) -> list[Opportunity]:
    """Verify all opportunities. Unverified ones are kept (marked) so eval can count them."""
    evidence = ctx.db.get_evidence_map(company_id)
    calllog.progress("Checking every claim against its source")
    for opp in opps:
        verify_opportunity(ctx, opp, evidence)
    log.info("Verified %d of %d opportunities", sum(o.verified for o in opps), len(opps))
    return opps
