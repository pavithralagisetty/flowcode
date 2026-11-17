"""Copy local data (a SQLite file, report files and the old JSONL call log) into the configured database."""

from __future__ import annotations

import json
from pathlib import Path

from flowcode_agent.db import Database, copy_database


def import_report_files(db: Database, outputs: Path) -> int:
    """Load outputs/<slug>/report.json (+ report.md) into the reports table. Returns reports imported."""
    count = 0
    for path in sorted(outputs.glob("*/report.json")):
        data = json.loads(path.read_text())
        md_path = path.with_name("report.md")
        db.save_report(data["company"]["id"], data["run_id"], path.parent.name, path.read_text(),
                       md_path.read_text() if md_path.exists() else "")
        count += 1
    return count


def import_call_log(db: Database, jsonl: Path) -> int:
    """Load the old logs/api_calls.jsonl into the api_calls table. Returns calls imported."""
    if not jsonl.exists():
        return 0
    count = 0
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        db.add_api_call(entry)
        count += 1
    return count


def migrate(source: Database, target: Database, outputs: Path, jsonl: Path) -> dict[str, int]:
    """Copy all tables from `source`, then fill reports and calls from files if the source had none."""
    counts = copy_database(source, target)
    if counts["reports"] == 0:
        counts["reports"] = import_report_files(target, outputs)
    if counts["api_calls"] == 0:
        counts["api_calls"] = import_call_log(target, jsonl)
    return counts
