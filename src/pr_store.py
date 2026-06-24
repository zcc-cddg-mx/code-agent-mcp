"""SQLite persistence for Azure DevOps pull requests.

Separate from task_store — PRs are long-lived artefacts, tasks are ephemeral jobs.
Uses the same DB file (TASKS_DB) but a dedicated table.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

from src.logger import log

_DB_PATH = Path(os.environ.get("TASKS_DB", "/data/tasks.db"))
_lock = threading.Lock()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS prs (
    pr_id          INTEGER PRIMARY KEY,
    pr_url         TEXT NOT NULL,
    repo           TEXT NOT NULL,
    source_branch  TEXT NOT NULL,
    target_branch  TEXT NOT NULL,
    title          TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    task_id        TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
)
"""

_ALL_FIELDS = [
    "pr_id", "pr_url", "repo", "source_branch", "target_branch",
    "title", "status", "task_id", "created_at", "updated_at",
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
    return {k: v for k, v in dict(row).items() if v is not None}


def upsert(pr: dict, now_iso: str) -> None:
    """Insert or update a PR record. pr must contain at least pr_id, pr_url, repo,
    source_branch, target_branch."""
    row = {f: pr.get(f) for f in _ALL_FIELDS}
    row["pr_id"] = pr["pr_id"]
    row["status"] = pr.get("status", "active")
    row["updated_at"] = now_iso
    if not pr.get("created_at"):
        row["created_at"] = now_iso

    non_null = {k: v for k, v in row.items() if v is not None}
    cols = ", ".join(non_null)
    placeholders = ", ".join(f":{k}" for k in non_null)
    updates = ", ".join(
        f"{k} = :{k}" for k in non_null
        if k not in ("pr_id", "created_at")
    )
    sql = (
        f"INSERT INTO prs ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(pr_id) DO UPDATE SET {updates}"
    )
    with _lock, _connect() as conn:
        conn.execute(sql, non_null)


def get(pr_id: int) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM prs WHERE pr_id = ?", (pr_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_all(
    repo: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if repo:
        clauses.append("repo = ?")
        params.append(repo)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _lock, _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM prs {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_status(pr_id: int, status: str, now_iso: str) -> bool:
    """Update only the status field of an existing PR. Returns True if found."""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE prs SET status = ?, updated_at = ? WHERE pr_id = ?",
            (status, now_iso, pr_id),
        )
    return cur.rowcount > 0


def cleanup_old_records(days: int = 90) -> int:
    """Delete PR records older than *days* days. Returns rows deleted."""
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00",
        time.gmtime(time.time() - days * 86400),
    )
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM prs WHERE created_at < ?", (cutoff,))
        deleted = cur.rowcount
    if deleted:
        log("CLEANUP", f"purged {deleted} PR record(s) older than {days} days from SQLite")
    return deleted
