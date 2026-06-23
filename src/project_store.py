"""SQLite persistence for Azure DevOps projects.

A project is auto-registered (or updated) whenever a repo belonging to it is
added via POST /repos. Projects are keyed by their slug: {org}/{name}.
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
CREATE TABLE IF NOT EXISTS projects (
    project_id        TEXT PRIMARY KEY,
    org               TEXT NOT NULL,
    name              TEXT NOT NULL,
    azure_project_id  TEXT,
    description       TEXT,
    visibility        TEXT,
    state             TEXT,
    web_url           TEXT,
    last_update_time  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
)
"""

_ALL_FIELDS = [
    "project_id", "org", "name", "azure_project_id",
    "description", "visibility", "state", "web_url",
    "last_update_time", "created_at", "updated_at",
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


def slug(org: str, project_name: str) -> str:
    """Canonical project key: '{org}/{name}'."""
    return f"{org}/{project_name}"


def upsert(project: dict, now_iso: str) -> None:
    row = {f: project.get(f) for f in _ALL_FIELDS}
    row["project_id"] = project["project_id"]
    row["updated_at"] = now_iso
    if not project.get("created_at"):
        row["created_at"] = now_iso

    cols = ", ".join(k for k in row if row[k] is not None)
    placeholders = ", ".join(f":{k}" for k in row if row[k] is not None)
    updates = ", ".join(
        f"{k} = :{k}" for k in row
        if k not in ("project_id", "created_at") and row[k] is not None
    )
    sql = (
        f"INSERT INTO projects ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(project_id) DO UPDATE SET {updates}"
    )
    with _lock, _connect() as conn:
        conn.execute(sql, {k: v for k, v in row.items() if v is not None})


def get(project_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_all() -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY org ASC, name ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
