"""SQLite setup and queries. One Database object per thread."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

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
"""

# Run statuses in pipeline order. A run resumes after the last one it reached.
STAGES = ["started", "resolved", "gathered", "moments", "matched", "verified", "scored", "done"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin wrapper over a sqlite3 connection."""

    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---------- companies ----------

    def upsert_company(self, company: Company) -> Company:
        """Insert or update a company by domain and return it with its id."""
        brands = json.dumps(company.brands)
        row = self.conn.execute("SELECT id FROM companies WHERE domain = ?", (company.domain,)).fetchone()
        if row:
            self.conn.execute(
                "UPDATE companies SET name=?, industry=?, brands_json=?, is_existing_customer=? WHERE id=?",
                (company.name, company.industry, brands, int(company.is_existing_customer), row["id"]),
            )
            company_id = row["id"]
        else:
            cur = self.conn.execute(
                "INSERT INTO companies (name, domain, industry, brands_json, is_existing_customer, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (company.name, company.domain, company.industry, brands, int(company.is_existing_customer), now_iso()),
            )
            company_id = cur.lastrowid
        self.conn.commit()
        return company.model_copy(update={"id": company_id})

    def _row_to_company(self, row: sqlite3.Row) -> Company:
        return Company(
            id=row["id"],
            name=row["name"],
            domain=row["domain"],
            industry=row["industry"] or "unknown",
            brands=json.loads(row["brands_json"] or "[]"),
            is_existing_customer=bool(row["is_existing_customer"]),
        )

    def get_company(self, company_id: int) -> Company | None:
        row = self.conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        return self._row_to_company(row) if row else None

    def find_company(self, domain: str) -> Company | None:
        row = self.conn.execute("SELECT * FROM companies WHERE domain = ?", (domain,)).fetchone()
        return self._row_to_company(row) if row else None

    def find_company_by_name(self, name: str) -> Company | None:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE lower(name) = lower(?) ORDER BY id DESC", (name,)
        ).fetchone()
        return self._row_to_company(row) if row else None

    def list_companies(self) -> list[Company]:
        rows = self.conn.execute("SELECT * FROM companies ORDER BY id").fetchall()
        return [self._row_to_company(r) for r in rows]

    # ---------- runs ----------

    def create_run(self, company_id: int, mode: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (company_id, mode, status, started_at) VALUES (?, ?, 'started', ?)",
            (company_id, mode, now_iso()),
        )
        self.conn.execute("INSERT OR IGNORE INTO usage (run_id, company_id) VALUES (?, ?)", (cur.lastrowid, company_id))
        self.conn.commit()
        return cur.lastrowid

    def set_run_status(self, run_id: int, status: str) -> None:
        finished = now_iso() if status in ("done", "failed", "no_change") else None
        self.conn.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?", (status, finished, run_id))
        self.conn.commit()

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def unfinished_run(self, company_id: int, mode: str) -> sqlite3.Row | None:
        """The latest run for this company that crashed or stopped before finishing."""
        return self.conn.execute(
            "SELECT * FROM runs WHERE company_id=? AND mode=? AND status NOT IN ('done','failed','no_change') "
            "ORDER BY id DESC LIMIT 1",
            (company_id, mode),
        ).fetchone()

    def latest_done_run(self, company_id: int, before_run: int | None = None) -> sqlite3.Row | None:
        sql = "SELECT * FROM runs WHERE company_id=? AND status='done'"
        args: list = [company_id]
        if before_run is not None:
            sql += " AND id < ?"
            args.append(before_run)
        return self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()

    # ---------- search cache ----------

    def get_cached_search(self, provider: str, query: str, day: str) -> list[SearchResult] | None:
        row = self.conn.execute(
            "SELECT results_json FROM search_cache WHERE provider=? AND query=? AND day=?", (provider, query, day)
        ).fetchone()
        if row is None:
            return None
        return [SearchResult(**r) for r in json.loads(row["results_json"])]

    def save_search(self, provider: str, query: str, day: str, results: list[SearchResult]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO search_cache (provider, query, day, results_json) VALUES (?, ?, ?, ?)",
            (provider, query, day, json.dumps([r.model_dump() for r in results])),
        )
        self.conn.commit()

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
        sql = f"SELECT 1 FROM evidence WHERE company_id = ? AND ({' OR '.join(clauses)}) LIMIT 1"
        return self.conn.execute(sql, args).fetchone() is not None

    def add_evidence(self, ev: Evidence) -> int | None:
        """Save evidence. Returns the new id, or None if the URL or text is already stored."""
        try:
            cur = self.conn.execute(
                "INSERT INTO evidence (company_id, run_id, url, final_url, url_hash, text_hash, title, publisher, "
                "published_date, date_known, fetched_at, text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.company_id, ev.run_id, ev.url, ev.final_url, ev.url_hash, ev.text_hash, ev.title, ev.publisher,
                 ev.published_date.isoformat() if ev.published_date else None, int(ev.date_known), ev.fetched_at,
                 ev.text),
            )
        except sqlite3.IntegrityError:
            return None
        self.conn.commit()
        return cur.lastrowid

    def _row_to_evidence(self, row: sqlite3.Row) -> Evidence:
        data = dict(row)
        data["published_date"] = date.fromisoformat(data["published_date"]) if data["published_date"] else None
        data["date_known"] = bool(data["date_known"])
        return Evidence(**data)

    def list_evidence(self, company_id: int) -> list[Evidence]:
        rows = self.conn.execute("SELECT * FROM evidence WHERE company_id=? ORDER BY id", (company_id,)).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def count_evidence(self, company_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM evidence WHERE company_id=?", (company_id,)).fetchone()[0]

    def get_evidence_map(self, company_id: int) -> dict[int, Evidence]:
        return {e.id: e for e in self.list_evidence(company_id)}

    # ---------- moments ----------

    def save_moments(self, company_id: int, run_id: int, moments: list[Moment]) -> list[int]:
        self.conn.execute("DELETE FROM moments WHERE run_id=?", (run_id,))
        ids = []
        for m in moments:
            cur = self.conn.execute(
                "INSERT INTO moments (company_id, run_id, json) VALUES (?, ?, ?)",
                (company_id, run_id, m.model_dump_json()),
            )
            ids.append(cur.lastrowid)
        self.conn.commit()
        return ids

    def list_moments(self, run_id: int) -> list[tuple[int, Moment]]:
        rows = self.conn.execute("SELECT id, json FROM moments WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [(r["id"], Moment.model_validate_json(r["json"])) for r in rows]

    # ---------- opportunities ----------

    def replace_opportunities(self, company_id: int, run_id: int, opps: list[Opportunity]) -> list[Opportunity]:
        """Replace all opportunities for a run and return them with ids."""
        self.conn.execute("DELETE FROM opportunities WHERE run_id=?", (run_id,))
        saved = []
        for o in opps:
            cur = self.conn.execute(
                "INSERT INTO opportunities (company_id, run_id, moment_id, play_id, json, verified, score, "
                "factor_scores_json) VALUES (?,?,?,?,?,?,?,?)",
                (company_id, run_id, o.moment_id, o.play_id, "{}", int(o.verified), o.score,
                 json.dumps({k: v.model_dump() for k, v in o.factor_ratings.items()})),
            )
            o = o.model_copy(update={"id": cur.lastrowid})
            self.conn.execute("UPDATE opportunities SET json=? WHERE id=?", (o.model_dump_json(), o.id))
            saved.append(o)
        self.conn.commit()
        return saved

    def list_opportunities(self, run_id: int) -> list[Opportunity]:
        rows = self.conn.execute("SELECT json FROM opportunities WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [Opportunity.model_validate_json(r["json"]) for r in rows]

    # ---------- scores ----------

    def save_company_score(self, company_id: int, run_id: int, cs: CompanyScore) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO company_scores (company_id, run_id, score, factors_json) VALUES (?,?,?,?)",
            (company_id, run_id, cs.score, cs.model_dump_json()),
        )
        self.conn.commit()

    def get_company_score(self, company_id: int, run_id: int) -> CompanyScore | None:
        row = self.conn.execute(
            "SELECT factors_json FROM company_scores WHERE company_id=? AND run_id=?", (company_id, run_id)
        ).fetchone()
        return CompanyScore.model_validate_json(row["factors_json"]) if row else None

    # ---------- usage ----------

    def add_usage(self, run_id: int, company_id: int, search_requests: int = 0, input_tokens: int = 0,
                  output_tokens: int = 0) -> None:
        self.conn.execute("INSERT OR IGNORE INTO usage (run_id, company_id) VALUES (?, ?)", (run_id, company_id))
        self.conn.execute(
            "UPDATE usage SET search_requests = search_requests + ?, llm_input_tokens = llm_input_tokens + ?, "
            "llm_output_tokens = llm_output_tokens + ? WHERE run_id=? AND company_id=?",
            (search_requests, input_tokens, output_tokens, run_id, company_id),
        )
        self.conn.commit()

    def get_usage(self, run_id: int) -> dict[str, int]:
        row = self.conn.execute("SELECT * FROM usage WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return {"search_requests": 0, "llm_input_tokens": 0, "llm_output_tokens": 0}
        return {k: row[k] for k in ("search_requests", "llm_input_tokens", "llm_output_tokens")}
