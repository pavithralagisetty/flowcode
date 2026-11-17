"""Command line: run, batch, refresh, eval. Everything else lives in config.yaml."""

from __future__ import annotations

import logging
from datetime import date

import typer

from flowcode_agent.batch import BatchItem, read_companies, refresh_items, run_many, write_ranking
from flowcode_agent.config import Config, load_config
from flowcode_agent.db import Database
from flowcode_agent.evaluate import GOLDEN, golden_pass, quality, write_eval
from flowcode_agent.fetch import Fetcher
from flowcode_agent.pipeline import RunResult, build_context, run_company
from flowcode_agent.providers.handoff import NeedsAnswer
from flowcode_agent.steps.resolve import AmbiguousCompany

app = typer.Typer(add_completion=False, help="Research where Flowcode could help a company.")
CONFIG_OPT = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml")
VERBOSE_OPT = typer.Option(False, "--verbose", "-v", help="Show debug logs")


def _setup(config_path: str, verbose: bool) -> Config:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "trafilatura", "google_genai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return load_config(config_path)


def _print_usage(usages: list[dict[str, int]]) -> None:
    keys = ("search_requests", "llm_input_tokens", "llm_output_tokens")
    totals = {k: sum(u.get(k, 0) for u in usages) for k in keys}
    typer.echo(f"Usage: {totals['search_requests']} search requests, "
               f"{totals['llm_input_tokens']:,} input tokens, {totals['llm_output_tokens']:,} output tokens")


def _print_result(r: RunResult) -> None:
    if r.status == "no_change":
        typer.echo(f"{r.company.name}: nothing new")
        return
    typer.echo(f"{r.company.name}: company score {r.score.score:.2f}, {len(r.opportunities)} opportunities, "
               f"{r.new_evidence} new pages -> {r.report_dir / 'report.md'}")


@app.command()
def run(company: str = typer.Argument(..., help="Company name or website URL"),
        config: str = CONFIG_OPT, verbose: bool = VERBOSE_OPT) -> None:
    """Research one company and write its report."""
    cfg = _setup(config, verbose)
    ctx = build_context(cfg)
    try:
        result = run_company(ctx, company)
    except AmbiguousCompany as exc:
        typer.echo(f"'{exc.query}' could mean several companies (confidence {exc.confidence:.2f}). "
                   "Rerun with the website of the one you mean:")
        for c in exc.candidates:
            typer.echo(f"  - {c.name}: {c.domain or 'no domain'} {('- ' + c.note) if c.note else ''}")
        raise typer.Exit(2)
    except NeedsAnswer as exc:
        typer.echo(f"PAUSED: {exc}. Answer it, then rerun the same command to continue.")
        raise typer.Exit(3)
    _print_result(result)
    _print_usage([result.usage])


def _batch_items(cfg: Config, items: list[BatchItem], mode: str) -> list[BatchItem]:
    def on_done(item: BatchItem) -> None:
        if item.result:
            _print_result(item.result)
        else:
            typer.echo(f"{item.name}: skipped ({item.error})")

    return run_many(cfg, lambda db: build_context(cfg, db=db), items, mode=mode, on_done=on_done)


@app.command()
def batch(csv_path: str = typer.Argument("companies.csv", help="CSV with name and optional domain columns"),
          config: str = CONFIG_OPT, verbose: bool = VERBOSE_OPT) -> None:
    """Research every company in a CSV and write outputs/ranking.md and ranking.csv."""
    cfg = _setup(config, verbose)
    items = _batch_items(cfg, read_companies(csv_path), "run")
    md, csv_file = write_ranking(cfg.path("outputs"), items)
    typer.echo(f"Ranking written to {md} and {csv_file}")
    _print_usage([i.result.usage for i in items if i.result])


@app.command()
def refresh(config: str = CONFIG_OPT, verbose: bool = VERBOSE_OPT) -> None:
    """Re-gather evidence for stored companies and redo the analysis only where something new appeared."""
    cfg = _setup(config, verbose)
    db = Database(cfg.db_target())
    items = refresh_items(db)
    db.close()
    if not items:
        typer.echo("No stored companies yet. Use `run` or `batch` first.")
        return
    items = _batch_items(cfg, items, "refresh")
    changed = [i for i in items if i.result and i.result.status == "done"]
    typer.echo(f"Refresh finished: {len(changed)} of {len(items)} companies had new evidence.")
    _print_usage([i.result.usage for i in items if i.result])


@app.command()
def migrate(source: str = typer.Argument(None, help="SQLite file to copy from (default: paths.db in config)"),
            config: str = CONFIG_OPT, verbose: bool = VERBOSE_OPT) -> None:
    """Copy local data into the configured Postgres database (set storage.backend: postgres first)."""
    from flowcode_agent.migrate import migrate as run_migrate

    cfg = _setup(config, verbose)
    if cfg.storage.backend != "postgres":
        typer.echo("Set storage.backend: postgres in config.yaml and DATABASE_URL in .env, then run this again.")
        raise typer.Exit(1)
    src = Database(source or cfg.path("db"))
    dst = Database(cfg.db_target())
    try:
        counts = run_migrate(src, dst, cfg.path("outputs"), cfg.base_dir / "logs" / "api_calls.jsonl")
    except ValueError as exc:
        typer.echo(f"Not copied: {exc}")
        raise typer.Exit(1)
    finally:
        src.close()
        dst.close()
    typer.echo("Copied: " + ", ".join(f"{n} {table}" for table, n in counts.items() if n))


@app.command(name="eval")
def eval_cmd(config: str = CONFIG_OPT, verbose: bool = VERBOSE_OPT) -> None:
    """Golden test on Eddie Bauer and LOVE CORN, plus quality numbers for the latest run of every company."""
    cfg = _setup(config, verbose)
    ctx = build_context(cfg)
    today = date.today()
    golden, usages = [], []
    for name, play in GOLDEN.items():
        try:
            r = run_company(ctx, name)
            usages.append(r.usage)
            ok, plays = golden_pass(ctx.db, r.run_id, play, today)
            golden.append((name, play, ok, plays, None))
        except Exception as exc:  # noqa: BLE001 - report the failure in eval.md
            golden.append((name, play, False, [], str(exc)))
    fetcher = Fetcher(timeout=cfg.research.fetch_timeout_seconds)
    qualities = []
    for company in ctx.db.list_companies():
        run_row = ctx.db.latest_done_run(company.id)
        if run_row:
            qualities.append(quality(ctx.db, company, run_row["id"], today, check_alive=fetcher.check_alive))
    path = write_eval(cfg.path("outputs") / "eval.md", golden, qualities)
    for name, play, ok, plays, error in golden:
        typer.echo(f"{name}: {'PASS' if ok else 'FAIL'} (expected {play}, got {', '.join(plays) or error or 'none'})")
    typer.echo(f"Eval written to {path}")
    _print_usage(usages)
