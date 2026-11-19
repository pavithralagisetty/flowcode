from flowcode_agent.models import EvidenceLink, Moment, Timing
from flowcode_agent.steps.moments import _combine


def _moment(touchpoints: list[str], quote: str) -> Moment:
    return Moment(title="New flavor", type="product_launch", summary="A new flavor.",
                  timing=Timing(status="ongoing"), physical_touchpoints=touchpoints,
                  evidence=[EvidenceLink(evidence_id=1, exact_quote=quote)])


def test_combine_drops_touchpoints_that_differ_only_in_case():
    merged = _combine([_moment(["product packaging", "menu boards"], "a"),
                       _moment(["Menu boards", "Product  packaging", "in-store signage"], "b")])
    assert merged.physical_touchpoints == ["product packaging", "menu boards", "in-store signage"]
    assert len(merged.evidence) == 2
