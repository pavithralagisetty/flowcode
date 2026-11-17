from datetime import date

from flowcode_agent.models import Company
from flowcode_agent.providers.search_fake import FakeSearchProvider
from flowcode_agent.steps.gather import QUERY_TEMPLATES, cached_search, gather, is_fresh
from tests.conftest import TODAY, FakeFetcher, make_ctx

PAGES = {
    "https://acmesoda.com/news/fizz-fest": "acme_tour.html",
    "https://copycat.example/fizz-fest": "acme_tour_copy.html",
    "https://prwire.example/holiday-cans": "acme_packaging.html",
    "https://gazette.example/fun-run": "acme_old.html",
    "https://www.flowcode.com/case-study/acme": "acme_packaging.html",
}


def _setup(config, db):
    search = FakeSearchProvider({"acme soda": list(PAGES)})
    fetcher = FakeFetcher(PAGES)
    ctx = make_ctx(config, db, search=search, fetcher=fetcher)
    company = db.upsert_company(Company(name="Acme Soda", domain="acmesoda.com", industry="beverages"))
    run_id = db.create_run(company.id, "run")
    return ctx, company, run_id, search, fetcher


def test_gather_dedupes_blocks_and_drops_old(config, db):
    ctx, company, run_id, search, fetcher = _setup(config, db)
    new_ids = gather(ctx, company, run_id)
    urls = {e.final_url for e in db.list_evidence(company.id)}
    assert urls == {"https://acmesoda.com/news/fizz-fest", "https://prwire.example/holiday-cans"}
    assert len(new_ids) == 2
    assert "https://www.flowcode.com/case-study/acme" not in fetcher.fetched
    ev = db.list_evidence(company.id)[0]
    assert ev.date_known and ev.published_date == date(2026, 10, 20)


def test_search_is_cached_per_day(config, db):
    ctx, company, run_id, search, _ = _setup(config, db)
    cached_search(ctx, "acme soda campaign", 10)
    cached_search(ctx, "acme soda campaign", 10)
    assert search.requests == 1


def test_second_gather_finds_nothing_new(config, db):
    ctx, company, run_id, search, _ = _setup(config, db)
    gather(ctx, company, run_id)
    requests_after_first = search.requests
    assert gather(ctx, company, db.create_run(company.id, "refresh")) == []
    assert search.requests == requests_after_first  # same day: every query served from cache


def test_page_cap(config, db):
    config.research.max_pages_per_company = 1
    ctx, company, run_id, _, _ = _setup(config, db)
    assert len(gather(ctx, company, run_id)) == 1


def test_runs_all_query_templates(config, db):
    ctx, company, run_id, search, _ = _setup(config, db)
    gather(ctx, company, run_id)
    for template in QUERY_TEMPLATES:
        assert template.format(brand="Acme Soda") in search.queries


def test_is_fresh():
    assert is_fresh(None, "", TODAY, 180)
    assert is_fresh(date(2026, 9, 1), "", TODAY, 180)
    assert not is_fresh(date(2024, 1, 1), "A race in 2024.", TODAY, 180)
    assert is_fresh(date(2025, 1, 1), "The program runs through December 2026.", TODAY, 180)
