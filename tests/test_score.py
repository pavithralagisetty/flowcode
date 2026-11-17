from datetime import date

from flowcode_agent.models import Company, Evidence, EvidenceLink, FactorRating, Moment, Opportunity, Timing
from flowcode_agent.steps.score import (company_score, industry_rating, is_over, new_activity_rating,
                                        strong_count_rating, weighted)
from tests.conftest import TODAY


def r(n: int) -> FactorRating:
    return FactorRating(rating=n, reason="x")


def test_weighted_uses_config_weights(config):
    ratings = {"timing": r(5), "playbook_fit": r(5), "reach": r(4), "evidence_strength": r(3), "openness": r(2)}
    # 0.25*5 + 0.25*5 + 0.20*4 + 0.15*3 + 0.15*2 = 4.05
    assert weighted(ratings, config.scoring.opportunity_weights) == 4.05


def test_strong_count_mapping():
    assert [strong_count_rating(n) for n in range(6)] == [1, 2, 3, 4, 5, 5]


def test_industry_fit(config):
    assert industry_rating("Retail and consumer goods", config.scoring).rating == 5
    assert industry_rating("software", config.scoring).rating == 3


def _opp(score: float, published: date | None, status="ongoing", end=None) -> tuple[Opportunity, Evidence]:
    ev = Evidence(id=int(score * 100), company_id=1, run_id=1, url="u", final_url="u", url_hash="a", text_hash="b",
                  fetched_at="now", text="t", published_date=published)
    m = Moment(title="m", type="tv_ad", summary="s", timing=Timing(status=status, end_date=end),
               evidence=[EvidenceLink(evidence_id=ev.id, exact_quote="q")])
    return Opportunity(moment=m, score=score, verified=True), ev


def test_new_activity_buckets():
    for days, want in ((10, 5), (45, 4), (75, 3), (120, 2)):
        opp, ev = _opp(4.0, date.fromordinal(TODAY.toordinal() - days))
        assert new_activity_rating([opp], {ev.id: ev}, TODAY).rating == want


def test_is_over():
    assert is_over(_opp(4, None, status="past")[0], TODAY)
    assert is_over(_opp(4, None, end=date(2026, 1, 1))[0], TODAY)
    assert not is_over(_opp(4, None, status="upcoming")[0], TODAY)


def test_company_factors(config):
    o1, e1 = _opp(4.2, date(2026, 11, 1))
    o2, e2 = _opp(3.0, date(2026, 11, 1))
    company = Company(name="Acme", domain="acme.com", industry="media")
    f = company_score(company, [o1, o2], r(4), {e1.id: e1, e2.id: e2}, config.scoring, TODAY)
    assert f["strong_count"].rating == 2
    assert f["industry_fit"].rating == 5
    assert f["new_activity"].rating == 5
    assert f["best_opportunity"].rating == 4
