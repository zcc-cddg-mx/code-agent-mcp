"""Basic sanity tests for task_store (no SQLite file needed — uses :memory: via env var)."""

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def tmp_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))
    # Re-import to pick up the new env var
    import importlib
    import src.task_store as ts
    importlib.reload(ts)
    ts.init_db()
    return ts


def test_upsert_and_get(tmp_db):
    ts = tmp_db
    now = "2026-01-01T00:00:00+00:00"
    ts.upsert({"task_id": "abc123", "status": "queued", "ticket": "ZNRX-1", "created_at": now}, now)
    task = ts.get("abc123")
    assert task["status"] == "queued"
    assert task["ticket"] == "ZNRX-1"


def test_partial_upsert_preserves_fields(tmp_db):
    ts = tmp_db
    now = "2026-01-01T00:00:00+00:00"
    ts.upsert({"task_id": "abc123", "status": "queued", "ticket": "ZNRX-1", "created_at": now}, now)
    ts.upsert({"task_id": "abc123", "status": "running"}, now)
    task = ts.get("abc123")
    assert task["status"] == "running"
    assert task["ticket"] == "ZNRX-1"  # not overwritten


def test_get_recent_ticket_filter(tmp_db):
    ts = tmp_db
    now = "2026-01-01T00:00:00+00:00"
    ts.upsert({"task_id": "t1", "status": "done", "ticket": "ZNRX-1", "created_at": now}, now)
    ts.upsert({"task_id": "t2", "status": "done", "ticket": "ZNRX-2", "created_at": now}, now)
    results = ts.get_recent(ticket="ZNRX-1")
    assert len(results) == 1
    assert results[0]["task_id"] == "t1"


def test_get_missing_returns_none(tmp_db):
    assert tmp_db.get("nonexistent") is None


def test_steps_stored_and_retrieved_as_list(tmp_db):
    ts = tmp_db
    now = "2026-06-01T00:00:00+00:00"
    steps = [{"name": "create_branch", "status": "done"},
             {"name": "commit_push", "status": "running"}]
    ts.upsert({"task_id": "s1", "status": "running", "steps": steps, "created_at": now}, now)
    task = ts.get("s1")
    assert task["steps"] == steps


def test_steps_partial_upsert_preserves_other_fields(tmp_db):
    ts = tmp_db
    now = "2026-06-01T00:00:00+00:00"
    # Full insert first, then partial update with only steps (no status)
    ts.upsert({"task_id": "s2", "status": "running", "ticket": "T-1",
               "steps": [{"name": "create_branch", "status": "pending"}],
               "created_at": now}, now)
    ts.upsert({"task_id": "s2", "steps": [{"name": "create_branch", "status": "done"}]}, now)
    task = ts.get("s2")
    assert task["status"] == "running"   # not overwritten
    assert task["ticket"] == "T-1"       # not overwritten
    assert task["steps"][0]["status"] == "done"


def test_migration_adds_steps_column(tmp_path, monkeypatch):
    """init_db must add steps column to an existing DB that lacks it."""
    import sqlite3, importlib
    db_path = str(tmp_path / "legacy.db")
    # Create table without steps column (simulates a pre-migration DB)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY, ticket TEXT, status TEXT NOT NULL,
                command TEXT, branch TEXT, aux_branch TEXT, commit_id TEXT,
                repo TEXT, build_status TEXT, summary TEXT, error TEXT,
                active_task TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
    monkeypatch.setenv("TASKS_DB", db_path)
    import src.task_store as ts
    importlib.reload(ts)
    ts.init_db()  # should add the column without error
    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "steps" in cols
