"""Branch dictionary — maps branch names to their metadata.

Persisted in the SQLite DB (TASKS_DB) in a dedicated `branch_config` table.
Defaults are seeded on first init_db() if the table is empty.

Schema of each entry:
  {
    "label":       human-readable name shown in logs and API responses,
    "environment": deployed environment name (or null),
    "url":         environment URL (or null),
    "is_base":     true if feature/fix branches are cut from this branch,
    "role":        "base" | "integration"  — logical use of the branch
  }
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()

_DEFAULTS: dict[str, dict] = {
    "developer": {
        "label":       "desarrollo",
        "environment": "DEV (UAT)",
        "url":         "https://uat-oficinavirtual.zurichseguros.com.ec",
        "is_base":     False,
        "role":        "integration",
    },
    "test": {
        "label":       "pruebas",
        "environment": "Test / Preprod",
        "url":         "https://preprod-oficinavirtual.zurichseguros.com.ec",
        "is_base":     False,
        "role":        "integration",
    },
    "develop": {
        "label":       "producción",
        "environment": "Producción (pre-deploy)",
        "url":         None,
        "is_base":     True,
        "role":        "base",
    },
    "main": {
        "label":       "producción (desplegado)",
        "environment": "Producción",
        "url":         "https://oficinavirtual.zurichseguros.com.ec",
        "is_base":     False,
        "role":        "integration",
    },
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS branch_config (
    branch  TEXT PRIMARY KEY,
    meta    TEXT NOT NULL
)
"""

# In-process cache; cleared by reload()
_registry: dict[str, dict] | None = None


def _connect() -> sqlite3.Connection:
    db_path = Path(os.environ.get("TASKS_DB", "/data/tasks.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create table and seed defaults if the table is empty."""
    with _lock, _connect() as conn:
        conn.execute(_CREATE_TABLE)
        if not conn.execute("SELECT 1 FROM branch_config LIMIT 1").fetchone():
            for branch, meta in _DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO branch_config (branch, meta) VALUES (?, ?)",
                    (branch, json.dumps(meta, ensure_ascii=False)),
                )


def _load() -> dict[str, dict]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT branch, meta FROM branch_config").fetchall()
    if rows:
        result = {}
        for row in rows:
            try:
                result[row["branch"]] = json.loads(row["meta"])
            except (ValueError, TypeError):
                pass
        return result
    return dict(_DEFAULTS)


def get_registry() -> dict[str, dict]:
    """Return the branch registry, loading from DB on first call."""
    global _registry
    if _registry is None:
        _registry = _load()
    return _registry


def reload() -> dict[str, dict]:
    """Invalidate in-process cache and reload from DB."""
    global _registry
    _registry = None
    return get_registry()


def get(branch: str) -> dict | None:
    """Return metadata for *branch*, or None if unknown."""
    return get_registry().get(branch)


def label(branch: str) -> str:
    """Return human-readable label for *branch*, falling back to the branch name."""
    entry = get(branch)
    return entry["label"] if entry else branch


def base_branch() -> str:
    """Return the branch that feature/fix branches are cut from."""
    for name, meta in get_registry().items():
        if meta.get("is_base"):
            return name
    return "develop"


def role(branch: str) -> str | None:
    """Return the logical role of *branch* from the global registry, or None if unknown."""
    entry = get(branch)
    return entry.get("role") if entry else None


def known_targets() -> list[str]:
    """Return all branch names that can be used as PR targets (non-base branches)."""
    return [name for name, meta in get_registry().items() if not meta.get("is_base")]


def save(new_config: dict[str, dict]) -> None:
    """Replace the branch registry with *new_config* merged over defaults.

    Writes to SQLite and invalidates the in-process cache.
    """
    merged = {**_DEFAULTS, **new_config}
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM branch_config")
        for branch, meta in merged.items():
            conn.execute(
                "INSERT INTO branch_config (branch, meta) VALUES (?, ?)",
                (branch, json.dumps(meta, ensure_ascii=False)),
            )
    reload()
