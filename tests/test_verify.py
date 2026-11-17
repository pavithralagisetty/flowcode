from flowcode_agent.models import Evidence, EvidenceLink, Moment, Opportunity, Timing
from flowcode_agent.steps.verify import check_quotes, normalize, quote_score

PAGE = ("Acme Soda today announced Fizz Fest, a 20 city music tour. “Fizz Fest brings our fans together in "
        "person,” said Jane Smith.\n\nTickets go on sale November 20.")


def _evidence() -> Evidence:
    return Evidence(id=1, company_id=1, run_id=1, url="u", final_url="u", url_hash="a", text_hash="b",
                    fetched_at="now", text=PAGE)


def test_normalize_quotes_and_spaces():
    assert normalize("“Hi”  there’s\n") == "\"hi\" there's"


def test_exact_match_after_normalizing():
    assert quote_score('"Fizz Fest brings our fans together in person," said Jane Smith.', PAGE) == 1.0


def test_fuzzy_match_allows_tiny_differences():
    assert quote_score("Acme Soda today announced Fizz Fest, a 20-city music tour.", PAGE) >= 0.9


def test_made_up_quote_fails():
    assert quote_score("Acme Soda will sponsor the Super Bowl halftime show next year.", PAGE) < 0.9


def test_check_quotes_removes_bad_links():
    moment = Moment(title="t", type="live_event", summary="s", timing=Timing(status="upcoming"), evidence=[
        EvidenceLink(evidence_id=1, exact_quote="Tickets go on sale November 20."),
        EvidenceLink(evidence_id=1, exact_quote="Free tickets for everyone forever."),
        EvidenceLink(evidence_id=99, exact_quote="Tickets go on sale November 20."),
    ])
    opp = Opportunity(moment=moment)
    assert check_quotes(opp, {1: _evidence()}, 0.9) == (3, 1)
    assert len(opp.moment.evidence) == 1
