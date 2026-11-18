import pytest

from flowcode_agent.models import Candidate, ResolvedCompany
from flowcode_agent.providers.search_fake import FakeSearchProvider
from flowcode_agent.steps.resolve import AmbiguousCompany, CompanyNotFound, customer_tier, looks_like_url, resolve_company
from tests.conftest import ROOT, FakeFetcher, FakeLLM, make_ctx

CUSTOMERS = ROOT / "existing_customers.yaml"


def test_customer_match_ignores_case_and_punctuation():
    assert customer_tier(["eddie-bauer"], CUSTOMERS) == "case_study"
    assert customer_tier(["Welchs"], CUSTOMERS) == "case_study"
    assert customer_tier(["Nike"], CUSTOMERS) is None
    assert customer_tier(["Disney+"], CUSTOMERS) == "likely"
    assert customer_tier(["Acme Soda"], CUSTOMERS) is None


def test_looks_like_url():
    assert looks_like_url("https://www.example.com")
    assert looks_like_url("example.com")
    assert not looks_like_url("Eddie Bauer")


def test_low_confidence_raises_with_candidates(config, db):
    answer = ResolvedCompany(official_name="Delta", official_domain="delta.com", industry="airline", confidence=0.3,
                             candidates=[Candidate(name="Delta Air Lines", domain="delta.com"),
                                         Candidate(name="Delta Faucet", domain="deltafaucet.com")])
    ctx = make_ctx(config, db, llm=FakeLLM({ResolvedCompany: lambda u: answer}))
    with pytest.raises(AmbiguousCompany) as exc:
        resolve_company(ctx, "Delta")
    assert len(exc.value.candidates) == 2


def test_not_a_company_raises_not_found(config, db):
    answer = ResolvedCompany(is_company=False, official_name="", official_domain="", industry="", confidence=0.0)
    ctx = make_ctx(config, db, llm=FakeLLM({ResolvedCompany: lambda u: answer}))
    with pytest.raises(CompanyNotFound):
        resolve_company(ctx, "asdfgh")


def test_low_confidence_without_any_match_raises_not_found(config, db):
    answer = ResolvedCompany(official_name="", official_domain="", industry="", confidence=0.1)
    ctx = make_ctx(config, db, llm=FakeLLM({ResolvedCompany: lambda u: answer}))
    with pytest.raises(CompanyNotFound):
        resolve_company(ctx, "pizza")


def test_resolve_saves_company_and_flags_customer(config, db):
    answer = ResolvedCompany(official_name="Eddie Bauer LLC", official_domain="www.eddiebauer.com",
                             industry="retail and consumer goods", brands=["Eddie Bauer"], confidence=0.95)
    search = FakeSearchProvider({"official website": ["https://www.eddiebauer.com/", "https://www.flowcode.com/x"]})
    ctx = make_ctx(config, db, search=search, llm=FakeLLM({ResolvedCompany: lambda u: answer}),
                   fetcher=FakeFetcher({}))
    company = resolve_company(ctx, "Eddie Bauer")
    assert company.domain == "eddiebauer.com"
    assert company.is_existing_customer and company.customer_tier == "case_study"
    assert db.find_company("eddiebauer.com").id == company.id
