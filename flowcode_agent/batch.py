"""Run many companies concurrently, plus refresh of stored companies."""

from __future__ import annotations

import csv
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from flowcode_agent.config import Config
from flowcode_agent.context import RunContext
from flowcode_agent.db import Database
from flowcode_agent.pipeline import RunResult, run_company
from flowcode_agent.steps.resolve import AmbiguousCompany, CompanyNotFound

log = logging.getLogger(__name__)
ContextFactory = Callable[[Database], RunContext]


@dataclass
class BatchItem:
    name: str
    domain: str | None = None
    result: RunResult | None = None
    error: str | None = None


def read_companies(path: str | Path) -> list[BatchItem]:
    """Read a CSV with a `name` column and an optional `domain` column."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [BatchItem(name=r["name"].strip(), domain=(r.get("domain") or "").strip() or None)
            for r in rows if (r.get("name") or "").strip()]


def _run_one(config: Config, make_ctx: ContextFactory, item: BatchItem, mode: str) -> BatchItem:
    """Each worker thread gets its own database connection and providers, so usage counts stay per company."""
    db = Database(config.db_target())
    try:
        item.result = run_company(make_ctx(db), item.name, item.domain, mode=mode)
    except CompanyNotFound:
        item.error = "no company found, check the name or add a domain"
    except AmbiguousCompany as exc:
        item.error = f"ambiguous, add a domain: {', '.join(f'{c.name} ({c.domain})' for c in exc.candidates)}"
    except Exception as exc:  # noqa: BLE001 - one company failing must not stop the batch
        log.exception("Failed: %s", item.name)
        item.error = f"{type(exc).__name__}: {exc}"
    finally:
        db.close()
    return item


def run_many(config: Config, make_ctx: ContextFactory, items: list[BatchItem], mode: str = "run",
             on_done: Callable[[BatchItem], None] | None = None) -> list[BatchItem]:
    with ThreadPoolExecutor(max_workers=config.batch.concurrency) as pool:
        futures = [pool.submit(_run_one, config, make_ctx, item, mode) for item in items]
        for fut in as_completed(futures):
            if on_done:
                on_done(fut.result())
    return items


def refresh_items(db: Database) -> list[BatchItem]:
    return [BatchItem(name=c.name, domain=c.domain) for c in db.list_companies()]


def write_ranking(out_dir: Path, items: list[BatchItem]) -> tuple[Path, Path]:
    """Write ranking.md and ranking.csv sorted by company score; existing customers listed separately."""
    out_dir.mkdir(parents=True, exist_ok=True)
    done = [i for i in items if i.result and i.result.score]
    done.sort(key=lambda i: i.result.score.score, reverse=True)
    prospects = [i for i in done if not i.result.company.is_existing_customer]
    customers = [i for i in done if i.result.company.is_existing_customer]

    with open(out_dir / "ranking.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "company", "domain", "company_score", "existing_customer", "top_opportunity",
                    "top_opportunity_score", "opportunities", "report"])
        for rank, i in enumerate(prospects + customers, 1):
            w.writerow(_row(rank, i))

    lines = ["# Company ranking", "", "## Prospects", "",
             "| Rank | Company | Score | Top opportunity | Opportunities |", "|---|---|---|---|---|"]
    lines += [_md_row(n, i) for n, i in enumerate(prospects, 1)] or ["| | none | | | |"]
    lines += ["", "## Existing Flowcode customers", "",
              "| Rank | Company | Score | Top opportunity | Opportunities |", "|---|---|---|---|---|"]
    lines += [_md_row(n, i) for n, i in enumerate(customers, 1)] or ["| | none | | | |"]
    skipped = [i for i in items if not (i.result and i.result.score)]
    if skipped:
        lines += ["", "## Not ranked", ""]
        lines += [f"- {i.name}: {i.error or (i.result.status if i.result else 'no result')}" for i in skipped]
    (out_dir / "ranking.md").write_text("\n".join(lines) + "\n")
    return out_dir / "ranking.md", out_dir / "ranking.csv"


def _row(rank: int, i: BatchItem) -> list:
    r = i.result
    top = r.opportunities[0] if r.opportunities else None
    return [rank, r.company.name, r.company.domain, f"{r.score.score:.2f}", r.company.customer_tier or "no",
            top.moment.title if top else "", f"{top.score:.2f}" if top else "", len(r.opportunities),
            str(r.report_dir / "report.md") if r.report_dir else ""]


def _md_row(rank: int, i: BatchItem) -> str:
    r = i.result
    top = r.opportunities[0] if r.opportunities else None
    top_txt = f"{top.moment.title} ({top.score:.2f})" if top else "none"
    return f"| {rank} | {r.company.name} | {r.score.score:.2f} | {top_txt} | {len(r.opportunities)} |"
