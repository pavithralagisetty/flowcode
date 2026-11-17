"""Storage: SQLite (a local file) or PostgreSQL (a server), chosen in config. One Database object per thread.

Every query is written in SQL that both engines accept (`ON CONFLICT`, `RETURNING`); the only
differences are the placeholder style and a few column types in the schema, handled here.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from flowcode_agent.models import (
    Company,
    CompanyScore,
    Evidence,
    Moment,
    Opportunity,
    SearchResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    industry TEXT,
    brands_json TEXT,
    is_existing_customer INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS search_cache (
    provider TEXT NOT NULL,
    query TEXT NOT NULL,
    day TEXT NOT NULL,
    results_json TEXT NOT NULL,
    PRIMARY KEY (provider, query, day)
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    title TEXT,
    publisher TEXT,
    published_date TEXT,
    date_known INTEGER,
    fetched_at TEXT,
    text TEXT,
    UNIQUE (company_id, url_hash),
    UNIQUE (company_id, text_hash)
);
CREATE TABLE IF NOT EXISTS moments (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    moment_id INTEGER,
    play_id TEXT,
    json TEXT NOT NULL,
    verified INTEGER DEFAULT 0,
    score REAL,
    factor_scores_json TEXT
);
CREATE TABLE IF NOT EXISTS company_scores (
    company_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    score REAL,
    factors_json TEXT,
    PRIMARY KEY (company_id, run_id)
);
CREATE TABLE IF NOT EXISTS usage (
    run_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    search_requests INTEGER DEFAULT 0,
    llm_input_tokens INTEGER DEFAULT 0,
    llm_output_tokens INTEGER DEFAULT 0,
    PRIMARY KEY (run_id, company_id)
);
CREATE TABLE IF NOT EXISTS reports (
    run_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    json TEXT NOT NULL,
    markdown TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    time TEXT NOT NULL,
    session TEXT,
    run_id INTEGER,
    company TEXT,
    kind TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_company ON evidence (company_id);
CREATE INDEX IF NOT EXISTS moments_run ON moments (run_id);
CREATE INDEX IF NOT EXISTS opportunities_run ON opportunities (run_id);
CREATE INDEX IF NOT EXISTS reports_slug ON reports (slug);
CREATE INDEX IF NOT EXISTS api_calls_session ON api_calls (session);
"""

# Tables with an auto-numbered `id`, in an order that copies cleanly (see copy_database).
TABLES = ["companies", "runs", "search_cache", "evidence", "moments", "opportunities", "company_scores", "usage",
          "reports", "api_calls", "jobs"]
SERIAL_TABLES = ["companies", "runs", "evidence", "moments", "opportunities", "api_calls"]

# Run statuses in pipeline order. A run resumes after the last one it reached.
STAGES = ["started", "resolved", "gathered", "moments", "matched", "verified", "scored", "done"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_postgres_url(target: str | Path) -> bool:
    return str(target).startswith(("postgres://", "postgresql://"))


def _postgres_schema() -> str:
    return SCHEMA.replace("id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")


class Database:
    """A connection to SQLite (`target` is a file path) or PostgreSQL (`target` is a postgres:// URL)."""

    def __init__(self, target: str | Path, check_same_thread: bool = True):
        self.postgres = is_postgres_url(target)
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row

            self.conn = psycopg.connect(str(target), autocommit=True, row_factory=dict_row)
            with self.conn.transaction():
                for statement in filter(str.strip, _postgres_schema().split(";")):
                    self.conn.execute(statement)
                # Hosts like Supabase expose the public schema through a web API; with row-level security on
                # and no policies, that API can read nothing. This app connects as the table owner, which is
                # not affected.
                for table in TABLES:
                    self.conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        else:
            self.conn = sqlite3.connect(str(target), timeout=30, check_same_thread=check_same_thread)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------- low-level helpers ----------

    def _run(self, sql: str, args: tuple | list = ()) -> Any:
        return self.conn.execute(sql.replace("?", "%s") if self.postgres else sql, tuple(args))

    def _one(self, sql: str, args: tuple | list = ()) -> dict | None:
        row = self._run(sql, args).fetchone()
        return dict(row) if row is not None else None

    def _all(self, sql: str, args: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self._run(sql, args).fetchall()]

    @contextmanager
    def _write(self) -> Iterator[None]:
        """Group statements into one transaction; commits at the end, rolls back on error."""
        if self.postgres:
            with self.conn.transaction():
                yield
        else:
            try:
                yield
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def _insert(self, sql: str, args: tuple | list) -> int | None:
        """Run an INSERT ... RETURNING id and return the id (None when ON CONFLICT DO NOTHING skipped it)."""
        row = self._run(sql + " RETURNING id", args).fetchone()
        return row["id"] if row is not None else None

    # ---------- companies ----------

    def upsert_company(self, company: Company) -> Company:
        """Insert or update a company by domain and return it with its id."""
        brands = json.dumps(company.brands)
        with self._write():
            row = self._one("SELECT id FROM companies WHERE domain = ?", (company.domain,))
            if row:
                self._run(
                    "UPDATE companies SET name=?, industry=?, brands_json=?, is_existing_customer=? WHERE id=?",
                    (company.name, company.industry, brands, int(company.is_existing_customer), row["id"]),
                )
                company_id = row["id"]
            else:
                company_id = self._insert(
                    "INSERT INTO companies (name, domain, industry, brands_json, is_existing_customer, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (company.name, company.domain, company.industry, brands, int(company.is_existing_customer),
                     now_iso()),
                )
        return company.model_copy(update={"id": company_id})

    @staticmethod
    def _row_to_company(row: dict) -> Company:
        return Company(
            id=row["id"],
            name=row["name"],
            domain=row["domain"],
            industry=row["industry"] or "unknown",
            brands=json.loads(row["brands_json"] or "[]"),
            is_existing_customer=bool(row["is_existing_customer"]),
        )

    def get_company(self, company_id: int) -> Company | None:
        row = self._one("SELECT * FROM companies WHERE id = ?", (company_id,))
        return self._row_to_company(row) if row else None

    def find_company(self, domain: str) -> Company | None:
        row = self._one("SELECT * FROM companies WHERE domain = ?", (domain,))
        return self._row_to_company(row) if row else None

    def find_company_by_name(self, name: str) -> Company | None:
        row = self._one("SELECT * FROM companies WHERE lower(name) = lower(?) ORDER BY id DESC", (name,))
        return self._row_to_company(row) if row else None

    def list_companies(self) -> list[Company]:
        return [self._row_to_company(r) for r in self._all("SELECT * FROM companies ORDER BY id")]

    # ---------- runs ----------

    def create_run(self, company_id: int, mode: str) -> int:
        with self._write():
            run_id = self._insert("INSERT INTO runs (company_id, mode, status, started_at) VALUES (?, ?, 'started', ?)",
                                  (company_id, mode, now_iso()))
            self._run("INSERT INTO usage (run_id, company_id) VALUES (?, ?) ON CONFLICT DO NOTHING", (run_id, company_id))
        return run_id

    def set_run_status(self, run_id: int, status: str) -> None:
        finished = now_iso() if status in ("done", "failed", "no_change") else None
        with self._write():
            self._run("UPDATE runs SET status=?, finished_at=? WHERE id=?", (status, finished, run_id))

    def get_run(self, run_id: int) -> dict | None:
        return self._one("SELECT * FROM runs WHERE id = ?", (run_id,))

    def unfinished_run(self, company_id: int, mode: str) -> dict | None:
        """The latest run for this company that crashed or stopped before finishing."""
        return self._one(
            "SELECT * FROM runs WHERE company_id=? AND mode=? AND status NOT IN ('done','failed','no_change') "
            "ORDER BY id DESC LIMIT 1",
            (company_id, mode),
        )

    def latest_done_run(self, company_id: int, before_run: int | None = None) -> dict | None:
        sql = "SELECT * FROM runs WHERE company_id=? AND status='done'"
        args: list = [company_id]
        if before_run is not None:
            sql += " AND id < ?"
            args.append(before_run)
        return self._one(sql + " ORDER BY id DESC LIMIT 1", args)

    # ---------- search cache ----------

    def get_cached_search(self, provider: str, query: str, day: str) -> list[SearchResult] | None:
        row = self._one("SELECT results_json FROM search_cache WHERE provider=? AND query=? AND day=?",
                        (provider, query, day))
        if row is None:
            return None
        return [SearchResult(**r) for r in json.loads(row["results_json"])]

    def save_search(self, provider: str, query: str, day: str, results: list[SearchResult]) -> None:
        with self._write():
            self._run(
                "INSERT INTO search_cache (provider, query, day, results_json) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (provider, query, day) DO UPDATE SET results_json = excluded.results_json",
                (provider, query, day, json.dumps([r.model_dump() for r in results])),
            )

    # ---------- evidence ----------

    def evidence_exists(self, company_id: int, *, url: str | None = None, url_hash: str | None = None,
                        text_hash: str | None = None) -> bool:
        clauses, args = [], [company_id]
        for col, val in (("url", url), ("url_hash", url_hash), ("text_hash", text_hash)):
            if val is not None:
                clauses.append(f"{col} = ?")
                args.append(val)
        if not clauses:
            return False
        return self._one(f"SELECT 1 AS hit FROM evidence WHERE company_id = ? AND ({' OR '.join(clauses)}) LIMIT 1",
                         args) is not None

    def add_evidence(self, ev: Evidence) -> int | None:
        """Save evidence. Returns the new id, or None if the URL or text is already stored."""
        with self._write():
            return self._insert(
                "INSERT INTO evidence (company_id, run_id, url, final_url, url_hash, text_hash, title, publisher, "
                "published_date, date_known, fetched_at, text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (ev.company_id, ev.run_id, ev.url, ev.final_url, ev.url_hash, ev.text_hash, ev.title, ev.publisher,
                 ev.published_date.isoformat() if ev.published_date else None, int(ev.date_known), ev.fetched_at,
                 ev.text),
            )

    @staticmethod
    def _row_to_evidence(row: dict) -> Evidence:
        row["published_date"] = date.fromisoformat(row["published_date"]) if row["published_date"] else None
        row["date_known"] = bool(row["date_known"])
        return Evidence(**row)

    def list_evidence(self, company_id: int) -> list[Evidence]:
        return [self._row_to_evidence(r) for r in self._all("SELECT * FROM evidence WHERE company_id=? ORDER BY id",
                                                            (company_id,))]

    def count_evidence(self, company_id: int) -> int:
        return self._one("SELECT COUNT(*) AS n FROM evidence WHERE company_id=?", (company_id,))["n"]

    def get_evidence_map(self, company_id: int) -> dict[int, Evidence]:
        return {e.id: e for e in self.list_evidence(company_id)}

    # ---------- moments ----------

    def save_moments(self, company_id: int, run_id: int, moments: list[Moment]) -> list[int]:
        with self._write():
            self._run("DELETE FROM moments WHERE run_id=?", (run_id,))
            return [self._insert("INSERT INTO moments (company_id, run_id, json) VALUES (?, ?, ?)",
                                 (company_id, run_id, m.model_dump_json())) for m in moments]

    def list_moments(self, run_id: int) -> list[tuple[int, Moment]]:
        rows = self._all("SELECT id, json FROM moments WHERE run_id=? ORDER BY id", (run_id,))
        return [(r["id"], Moment.model_validate_json(r["json"])) for r in rows]

    # ---------- opportunities ----------

    def replace_opportunities(self, company_id: int, run_id: int, opps: list[Opportunity]) -> list[Opportunity]:
        """Replace all opportunities for a run and return them with ids."""
        saved = []
        with self._write():
            self._run("DELETE FROM opportunities WHERE run_id=?", (run_id,))
            for o in opps:
                new_id = self._insert(
                    "INSERT INTO opportunities (company_id, run_id, moment_id, play_id, json, verified, score, "
                    "factor_scores_json) VALUES (?,?,?,?,?,?,?,?)",
                    (company_id, run_id, o.moment_id, o.play_id, "{}", int(o.verified), o.score,
                     json.dumps({k: v.model_dump() for k, v in o.factor_ratings.items()})),
                )
                o = o.model_copy(update={"id": new_id})
                self._run("UPDATE opportunities SET json=? WHERE id=?", (o.model_dump_json(), o.id))
                saved.append(o)
        return saved

    def list_opportunities(self, run_id: int) -> list[Opportunity]:
        rows = self._all("SELECT json FROM opportunities WHERE run_id=? ORDER BY id", (run_id,))
        return [Opportunity.model_validate_json(r["json"]) for r in rows]

    # ---------- scores ----------

    def save_company_score(self, company_id: int, run_id: int, cs: CompanyScore) -> None:
        with self._write():
            self._run(
                "INSERT INTO company_scores (company_id, run_id, score, factors_json) VALUES (?,?,?,?) "
                "ON CONFLICT (company_id, run_id) DO UPDATE SET score = excluded.score, "
                "factors_json = excluded.factors_json",
                (company_id, run_id, cs.score, cs.model_dump_json()),
            )

    def get_company_score(self, company_id: int, run_id: int) -> CompanyScore | None:
        row = self._one("SELECT factors_json FROM company_scores WHERE company_id=? AND run_id=?", (company_id, run_id))
        return CompanyScore.model_validate_json(row["factors_json"]) if row else None

    # ---------- usage ----------

    def add_usage(self, run_id: int, company_id: int, search_requests: int = 0, input_tokens: int = 0,
                  output_tokens: int = 0) -> None:
        with self._write():
            self._run("INSERT INTO usage (run_id, company_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
                      (run_id, company_id))
            self._run(
                "UPDATE usage SET search_requests = search_requests + ?, llm_input_tokens = llm_input_tokens + ?, "
                "llm_output_tokens = llm_output_tokens + ? WHERE run_id=? AND company_id=?",
                (search_requests, input_tokens, output_tokens, run_id, company_id),
            )

    def get_usage(self, run_id: int) -> dict[str, int]:
        row = self._one("SELECT * FROM usage WHERE run_id=?", (run_id,))
        if not row:
            return {"search_requests": 0, "llm_input_tokens": 0, "llm_output_tokens": 0}
        return {k: row[k] for k in ("search_requests", "llm_input_tokens", "llm_output_tokens")}

    # ---------- reports ----------

    def save_report(self, company_id: int, run_id: int, slug: str, report_json: str, markdown: str) -> None:
        with self._write():
            self._run(
                "INSERT INTO reports (run_id, company_id, slug, json, markdown, created_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT (run_id) DO UPDATE SET slug = excluded.slug, json = excluded.json, "
                "markdown = excluded.markdown, created_at = excluded.created_at",
                (run_id, company_id, slug, report_json, markdown, now_iso()),
            )

    def latest_reports(self) -> list[dict]:
        """The newest report for each company: {slug, run_id, json (parsed), markdown}."""
        rows = self._all(
            "SELECT r.* FROM reports r JOIN (SELECT slug, MAX(run_id) AS run_id FROM reports GROUP BY slug) last "
            "ON r.slug = last.slug AND r.run_id = last.run_id ORDER BY r.run_id"
        )
        return [{**r, "json": json.loads(r["json"])} for r in rows]

    def get_report(self, slug: str) -> dict | None:
        row = self._one("SELECT * FROM reports WHERE slug=? ORDER BY run_id DESC LIMIT 1", (slug,))
        return {**row, "json": json.loads(row["json"])} if row else None

    # ---------- API call log ----------

    def add_api_call(self, entry: dict[str, Any]) -> None:
        with self._write():
            self._run(
                "INSERT INTO api_calls (time, session, run_id, company, kind, json) VALUES (?,?,?,?,?,?)",
                (entry["time"], entry.get("session"), entry.get("run_id"), entry.get("company"), entry["kind"],
                 json.dumps(entry, default=str, ensure_ascii=False)),
            )

    def list_api_calls(self, session: str | None = None, run_id: int | None = None) -> list[dict]:
        sql, args = "SELECT json FROM api_calls", []
        if session is not None:
            sql, args = sql + " WHERE session = ?", [session]
        elif run_id is not None:
            sql, args = sql + " WHERE run_id = ?", [run_id]
        return [json.loads(r["json"]) for r in self._all(sql + " ORDER BY id", args)]

    # ---------- jobs started from the web app ----------

    def save_job(self, job_id: str, started_at: str, data: dict[str, Any]) -> None:
        with self._write():
            self._run("INSERT INTO jobs (id, started_at, json) VALUES (?,?,?) "
                      "ON CONFLICT (id) DO UPDATE SET json = excluded.json",
                      (job_id, started_at, json.dumps(data, default=str)))

    def list_jobs(self, limit: int = 50) -> list[dict]:
        rows = self._all("SELECT json FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [json.loads(r["json"]) for r in rows]

    def get_job(self, job_id: str) -> dict | None:
        row = self._one("SELECT json FROM jobs WHERE id = ?", (job_id,))
        return json.loads(row["json"]) if row else None


def copy_database(source: Database, target: Database) -> dict[str, int]:
    """Copy every row from one database into another (for example SQLite to Postgres), keeping ids.

    The target must be empty. Returns rows copied per table.
    """
    counts = {}
    with target._write():
        for table in TABLES:
            if target._one(f"SELECT COUNT(*) AS n FROM {table}")["n"]:
                raise ValueError(f"Target table {table} is not empty; refusing to copy over existing data")
            rows = source._all(f"SELECT * FROM {table}")
            for row in rows:
                cols = list(row)
                target._run(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [row[c] for c in cols])
            counts[table] = len(rows)
        if target.postgres:  # move each id sequence past the copied ids
            for table in SERIAL_TABLES:
                target._run(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
    return counts
