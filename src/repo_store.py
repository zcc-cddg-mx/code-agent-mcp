"""SQLite persistence for registered repositories.

Separate from task_store — repos are long-lived config, tasks are ephemeral jobs.
Uses the same DB file (TASKS_DB) but a different table.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from src.logger import log

_DB_PATH = Path(os.environ.get("TASKS_DB", "/data/tasks.db"))
_lock = threading.Lock()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS repos (
    repo_id          TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    git_url          TEXT NOT NULL,
    org              TEXT,
    project          TEXT,
    azure_repo_id    TEXT,
    default_branch   TEXT,
    web_url          TEXT,
    branches         TEXT,
    known_branches   TEXT,
    size_kb          INTEGER,
    last_inspected_at TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
)
"""

_JSON_FIELDS = {"branches", "known_branches"}
_ALL_FIELDS = [
    "repo_id", "name", "git_url", "org", "project",
    "azure_repo_id", "default_branch", "web_url",
    "branches", "known_branches", "size_kb",
    "last_inspected_at", "created_at", "updated_at",
]


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(_CREATE_TABLE)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in _JSON_FIELDS:
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (ValueError, TypeError):
                pass
    return {k: v for k, v in d.items() if v is not None}


def upsert(repo: dict, now_iso: str) -> None:
    row = {f: repo.get(f) for f in _ALL_FIELDS}
    row["repo_id"] = repo["repo_id"]
    row["updated_at"] = now_iso
    if not repo.get("created_at"):
        row["created_at"] = now_iso

    for field in _JSON_FIELDS:
        if isinstance(row.get(field), (dict, list)):
            row[field] = json.dumps(row[field], ensure_ascii=False)

    cols = ", ".join(k for k in row if row[k] is not None)
    placeholders = ", ".join(f":{k}" for k in row if row[k] is not None)
    updates = ", ".join(
        f"{k} = :{k}" for k in row
        if k not in ("repo_id", "created_at") and row[k] is not None
    )
    sql = (
        f"INSERT INTO repos ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(repo_id) DO UPDATE SET {updates}"
    )
    with _lock, _connect() as conn:
        conn.execute(sql, {k: v for k, v in row.items() if v is not None})


def get(repo_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM repos WHERE repo_id = ?", (repo_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_by_name(name: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM repos WHERE name = ?", (name,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_all() -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM repos ORDER BY name ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete(repo_id: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM repos WHERE repo_id = ?", (repo_id,))
    return cur.rowcount > 0
