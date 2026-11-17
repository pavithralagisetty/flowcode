"""JSON API for the React frontend. Run with: .venv/bin/uvicorn api:app --port 8600

Serves reports and usage from the database, starts research runs in background threads,
and serves the built frontend from web/dist when it exists. Nothing the app shows lives
only on the server's disk or in memory, so a redeploy or restart loses no data.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from flowcode_agent.config import PROVIDER_MODES, forced_mode, load_config
from flowcode_agent.db import Database
from flowcode_agent.pipeline import build_context, run_company
from flowcode_agent.providers.handoff import NeedsAnswer
from flowcode_agent.steps.resolve import AmbiguousCompany

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "web" / "dist"
cfg = load_config(ROOT / "config.yaml")
log = logging.getLogger("flowcode_agent")
log.setLevel(logging.INFO)
app = FastAPI(title="Flowcode Research API")
MAX_JOB_LOG_LINES = 300


_local = threading.local()


def _db() -> Database:
    """This thread's database connection (FastAPI runs each request on a worker thread)."""
    if getattr(_local, "db", None) is None:
        _local.db = Database(cfg.db_target())
    return _local.db


@dataclass
class RunJob:
    id: str
    query: str
    status: str = "running"  # running | done | paused | ambiguous | failed | interrupted
    message: str = ""
    step: str = "Starting"  # the research step running right now
    slug: str | None = None
    candidates: list[dict] = field(default_factory=list)
    run_id: int | None = None
    logs: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def save(self) -> None:
        """Persist the job so the run list survives a restart."""
        _db().save_job(self.id, self.started_at, {**asdict(self), "logs": self.logs[-MAX_JOB_LOG_LINES:]})


JOBS: dict[str, RunJob] = {}  # jobs started by this process; also saved in the database


class JobLogHandler(logging.Handler):
    """Copies pipeline log lines from one worker thread into its job, saving it when the step changes."""

    def __init__(self, job: RunJob, thread_id: int):
        super().__init__(level=logging.INFO)
        self.job, self.thread_id = job, thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        self.job.logs.append(record.getMessage())
        if hasattr(record, "progress"):
            self.job.step = record.progress
            self.job.save()


def _work(job: RunJob) -> None:
    handler = JobLogHandler(job, threading.get_ident())
    log.addHandler(handler)
    try:
        result = run_company(build_context(cfg, db=_db()), job.query)
        job.status, job.slug = "done", result.report_dir.name if result.report_dir else None
        job.run_id = result.run_id
        job.message = (f"Company score {result.score.score:.2f}, {len(result.opportunities)} opportunities"
                       if result.score else "Nothing new since the last run")
    except AmbiguousCompany as exc:
        job.status, job.message = "ambiguous", f"'{exc.query}' could mean several companies."
        job.candidates = [c.model_dump() for c in exc.candidates]
    except NeedsAnswer as exc:
        job.status = "paused"
        job.message = (f"No API key mode: waiting for a {exc.kind} answer in {exc.path.relative_to(ROOT)}. "
                       "Ask Claude Code to answer it, then run again to continue.")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        job.status, job.message = "failed", f"{type(exc).__name__}: {exc}"
    finally:
        log.removeHandler(handler)
        job.save()


@app.on_event("startup")
def _mark_interrupted_jobs() -> None:
    """Runs that were in progress when the server last stopped will never finish; say so."""
    db = _db()
    for data in db.list_jobs(limit=500):
        if data.get("status") == "running":
            data.update(status="interrupted", message="The server restarted before this run finished. Run it again "
                                                      "to continue from where it stopped.")
            db.save_job(data["id"], data["started_at"], data)


def _summary(slug: str, d: dict) -> dict:
    opps = d["opportunities"]
    return {
        "slug": slug, "name": d["company"]["name"], "domain": d["company"]["domain"],
        "industry": d["company"]["industry"], "customer_tier": d["company"].get("customer_tier"),
        "score": d["company_score"]["score"], "opportunities": len(opps),
        "strong": sum(1 for o in opps if o["score"] >= cfg.scoring.strong_threshold),
        "top": opps[0]["moment"]["title"] if opps else None, "top_score": opps[0]["score"] if opps else None,
        "channels": d.get("channels_covered", []), "run_date": d["run_date"],
    }


class RunRequest(BaseModel):
    query: str


class ModeRequest(BaseModel):
    mode: str  # "api" or "claude_code"


def _current_mode() -> str:
    pair = (cfg.search.provider, cfg.llm.provider)
    return next((m for m, p in PROVIDER_MODES.items() if p == pair), "custom")


def _set_providers(search: str, llm: str) -> None:
    """Rewrite the provider lines in config.yaml (keeping comments) and reload the config."""
    global cfg
    path = ROOT / "config.yaml"
    text = path.read_text()
    for section, value in (("search", search), ("llm", llm)):
        text, n = re.subn(rf"(^{section}:\n(?:[ \t].*\n)*?[ \t]+provider:[ \t]*)[\w-]+", rf"\g<1>{value}", text,
                          count=1, flags=re.M)
        if n != 1:
            raise HTTPException(500, f"Could not find {section}.provider in config.yaml")
    path.write_text(text)
    cfg = load_config(path)


@app.get("/api/reports")
def list_reports() -> list[dict]:
    items = [_summary(r["slug"], r["json"]) for r in _db().latest_reports()]
    return sorted(items, key=lambda r: r["score"], reverse=True)


@app.get("/api/reports/{slug}")
def get_report(slug: str) -> dict:
    saved = _db().get_report(slug)
    if saved is None:
        raise HTTPException(404, "No report for that company")
    return {"report": saved["json"], "markdown": saved["markdown"]}


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Enter a company name or website")
    job = RunJob(id=uuid.uuid4().hex[:8], query=query)
    JOBS[job.id] = job
    job.save()
    threading.Thread(target=_work, args=(job,), daemon=True).start()
    return asdict(job)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    saved = {j["id"]: j for j in _db().list_jobs()}
    saved.update({j.id: asdict(j) for j in JOBS.values()})  # live jobs have the freshest state
    return sorted(saved.values(), key=lambda j: j["started_at"], reverse=True)


@app.get("/api/runs/{job_id}")
def get_run(job_id: str) -> dict:
    if job_id in JOBS:
        return asdict(JOBS[job_id])
    saved = _db().get_job(job_id)
    if saved is None:
        raise HTTPException(404, "Unknown run")
    return saved


def _cost(row: dict) -> float | None:
    """Estimated dollars for one logged call at API list prices, or None when no price is set for its model.

    Claude Code calls use the list-price figure the CLI reports, which also counts cache rates."""
    if row.get("list_price_usd") is not None:
        return round(row["list_price_usd"], 6)
    prices = cfg.pricing
    cost = row.get("web_searches", 0) / 1000 * prices.web_search_per_1k
    price = prices.models.get(row.get("model") or "")
    tokens = row.get("input_tokens", 0) or row.get("output_tokens", 0)
    if tokens and (price is None or not (price.input_per_mtok or price.output_per_mtok)):
        return None
    if price:
        cost += row.get("input_tokens", 0) / 1e6 * price.input_per_mtok
        cost += row.get("output_tokens", 0) / 1e6 * price.output_per_mtok
    return round(cost, 6)


def _calls(session: str | None = None) -> list[dict]:
    rows = _db().list_api_calls(session=session)
    for r in rows:
        r["cost"] = _cost(r)
    return rows


@app.get("/api/usage")
def usage() -> list[dict]:
    """Totals per run: AI calls, searches, tokens and estimated cost, newest first."""
    runs: dict = {}
    for r in _calls():
        key = r.get("session") or r.get("run_id") or r["time"]
        u = runs.setdefault(key, {"session": key, "run_id": r.get("run_id"), "company": r.get("company"),
                                  "mode": r.get("mode", "run"), "started": r["time"],
                                  "ai_calls": 0, "searches": 0, "cached_searches": 0, "web_searches": 0,
                                  "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "priced": True,
                                  "by_step": {}})
        u["company"] = r.get("company") or u["company"]
        u["run_id"] = r.get("run_id") or u["run_id"]
        u["ended"] = r["time"]
        if r["kind"] == "search_cached":
            u["cached_searches"] += 1
            continue
        u["ai_calls" if r["kind"] == "llm" else "searches"] += 1
        u["web_searches"] += r.get("web_searches", 0)
        u["input_tokens"] += r.get("input_tokens", 0)
        u["output_tokens"] += r.get("output_tokens", 0)
        step = r.get("step") or ("search" if r["kind"] == "search" else "other")
        s = u["by_step"].setdefault(step, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        s["calls"] += 1
        s["input_tokens"] += r.get("input_tokens", 0)
        s["output_tokens"] += r.get("output_tokens", 0)
        if r["cost"] is None:
            u["priced"] = False
        else:
            u["cost"] += r["cost"]
    # Hide sessions that stopped at the company lookup (e.g. an ambiguous name) and never became a run.
    out = sorted((u for u in runs.values() if u["run_id"] is not None), key=lambda u: u["started"], reverse=True)
    for u in out:
        u["cost"] = round(u["cost"], 4) if u["priced"] else None
    return out


@app.get("/api/calls")
def calls(session: str | None = None) -> list[dict]:
    """Every logged AI call and search, optionally for one research session."""
    return _calls(session)


@app.get("/api/mode")
def get_mode() -> dict:
    from flowcode_agent.providers.claude_code import claude_available

    return {"mode": _current_mode(), "locked": forced_mode() is not None,
            "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "claude_code_installed": claude_available(),
            "running": any(j.status == "running" for j in JOBS.values())}


@app.post("/api/mode")
def set_mode(req: ModeRequest) -> dict:
    if forced_mode():
        raise HTTPException(409, "The mode is set by the FLOWCODE_MODE environment variable on this server")
    if req.mode not in PROVIDER_MODES:
        raise HTTPException(400, f"Mode must be one of {', '.join(PROVIDER_MODES)}")
    _set_providers(*PROVIDER_MODES[req.mode])
    return get_mode()


@app.get("/api/settings")
def settings() -> dict:
    main, cheap = cfg.llm.models()
    playbook = yaml.safe_load(cfg.path("playbook").read_text())
    pending = list((cfg.path("handoff") / "pending").glob("*.md")) if cfg.path("handoff").exists() else []
    return {
        "search_provider": cfg.search.provider, "llm_provider": cfg.llm.provider,
        "main_model": main, "cheap_model": cheap, "storage": cfg.storage.backend,
        "recency_days": cfg.research.recency_days, "max_pages": cfg.research.max_pages_per_company,
        "strong_threshold": cfg.scoring.strong_threshold, "strong_industries": cfg.scoring.strong_industries,
        "opportunity_weights": cfg.scoring.opportunity_weights, "company_weights": cfg.scoring.company_weights,
        "plays": playbook.get("plays", []), "pending_handoff": len(pending),
    }


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        """Serve the React app; unknown paths fall back to index.html for client-side routing."""
        target = DIST / path
        return FileResponse(target if path and target.is_file() else DIST / "index.html")
