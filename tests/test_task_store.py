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
