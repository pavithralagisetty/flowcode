"""JSON API for the React frontend. Run with: .venv/bin/uvicorn api:app --port 8600

Serves saved reports, starts research runs in background threads, and serves the built
frontend from web/dist when it exists.
"""

from __future__ import annotations

import json
import logging
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

from flowcode_agent.config import load_config
from flowcode_agent.pipeline import build_context, run_company
from flowcode_agent.providers.handoff import NeedsAnswer
from flowcode_agent.steps.resolve import AmbiguousCompany

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "web" / "dist"
cfg = load_config(ROOT / "config.yaml")
log = logging.getLogger("flowcode_agent")
log.setLevel(logging.INFO)
app = FastAPI(title="Flowcode Research API")


@dataclass
class RunJob:
    id: str
    query: str
    status: str = "running"  # running | done | paused | ambiguous | failed
    message: str = ""
    slug: str | None = None
    candidates: list[dict] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


JOBS: dict[str, RunJob] = {}


class JobLogHandler(logging.Handler):
    """Copies pipeline log lines from one worker thread into its job."""

    def __init__(self, job: RunJob, thread_id: int):
        super().__init__(level=logging.INFO)
        self.job, self.thread_id = job, thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread_id:
            self.job.logs.append(record.getMessage())


def _work(job: RunJob) -> None:
    handler = JobLogHandler(job, threading.get_ident())
    log.addHandler(handler)
    try:
        result = run_company(build_context(cfg), job.query)
        job.status, job.slug = "done", result.report_dir.name if result.report_dir else None
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


def _reports() -> dict[str, dict]:
    out = {}
    for path in sorted(cfg.path("outputs").glob("*/report.json")):
        try:
            out[path.parent.name] = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
    return out


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


@app.get("/api/reports")
def list_reports() -> list[dict]:
    items = [_summary(slug, d) for slug, d in _reports().items()]
    return sorted(items, key=lambda r: r["score"], reverse=True)


@app.get("/api/reports/{slug}")
def get_report(slug: str) -> dict:
    folder = cfg.path("outputs") / slug
    if not (folder / "report.json").exists() or folder.parent != cfg.path("outputs"):
        raise HTTPException(404, "No report for that company")
    data = json.loads((folder / "report.json").read_text())
    md = (folder / "report.md").read_text() if (folder / "report.md").exists() else ""
    return {"report": data, "markdown": md}


@app.post("/api/runs")
def start_run(req: RunRequest) -> dict:
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Enter a company name or website")
    job = RunJob(id=uuid.uuid4().hex[:8], query=query)
    JOBS[job.id] = job
    threading.Thread(target=_work, args=(job,), daemon=True).start()
    return asdict(job)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return [asdict(j) for j in sorted(JOBS.values(), key=lambda j: j.started_at, reverse=True)]


@app.get("/api/runs/{job_id}")
def get_run(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown run")
    return asdict(JOBS[job_id])


@app.get("/api/settings")
def settings() -> dict:
    main, cheap = cfg.llm.models()
    playbook = yaml.safe_load(cfg.path("playbook").read_text())
    pending = list((cfg.path("handoff") / "pending").glob("*.md")) if cfg.path("handoff").exists() else []
    return {
        "search_provider": cfg.search.provider, "llm_provider": cfg.llm.provider,
        "main_model": main, "cheap_model": cheap,
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
