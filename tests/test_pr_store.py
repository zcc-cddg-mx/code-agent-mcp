"""Tests for src/pr_store.py and GET /prs endpoints."""

import importlib
import os
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env_vars(monkeypatch):
    monkeypatch.setenv("TOKEN_AZURE", "test-token")
    monkeypatch.setenv("AZURE_PAT", "fake-pat")
    monkeypatch.setenv("AZURE_ORG", "MyOrg")
    monkeypatch.setenv("AZURE_PROJECT", "MyProject")
    monkeypatch.setenv("AZURE_REPO", "my-repo")


@pytest.fixture
def pr_store(env_vars, tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "test.db"))
    import src.pr_store as ps
    importlib.reload(ps)
    ps.init_db()
    return ps


@pytest.fixture
def client(env_vars, tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))

    import src.task_store as ts
    import src.azure_client as ac
    import src.auth as auth
    import app as app_module

    importlib.reload(ts)
    importlib.reload(ac)
    importlib.reload(auth)
    importlib.reload(app_module)

    app_module.app.testing = True
    with app_module.app.test_client() as c:
        yield c


_HEADERS = {"X-Agent-Token": "test-token"}

_SAMPLE_PR = {
    "pr_id": 100,
    "pr_url": "https://dev.azure.com/Org/Proj/_git/repo/pullrequest/100",
    "repo": "my-repo",
    "source_branch": "feature/test",
    "target_branch": "test",
    "title": "Test PR",
    "status": "active",
}


# ---------------------------------------------------------------------------
# pr_store unit tests
# ---------------------------------------------------------------------------

def test_upsert_and_get(pr_store):
    pr_store.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")
    record = pr_store.get(100)
    assert record["pr_id"] == 100
    assert record["repo"] == "my-repo"
    assert record["status"] == "active"
    assert record["source_branch"] == "feature/test"


def test_upsert_idempotent(pr_store):
    pr_store.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")
    pr_store.upsert({**_SAMPLE_PR, "title": "Updated Title"}, "2026-01-02T00:00:00+00:00")
    record = pr_store.get(100)
    assert record["title"] == "Updated Title"


def test_get_missing_returns_none(pr_store):
    assert pr_store.get(9999) is None


def test_list_all_returns_all(pr_store):
    pr_store.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")
    pr_store.upsert({**_SAMPLE_PR, "pr_id": 101, "pr_url": "https://x/101"}, "2026-01-01T00:00:00+00:00")
    records = pr_store.list_all()
    assert len(records) == 2


def test_list_all_filter_by_repo(pr_store):
    pr_store.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")
    pr_store.upsert({**_SAMPLE_PR, "pr_id": 101, "pr_url": "https://x/101", "repo": "other-repo"},
                    "2026-01-01T00:00:00+00:00")
    records = pr_store.list_all(repo="my-repo")
    assert len(records) == 1
    assert records[0]["repo"] == "my-repo"


def test_list_all_filter_by_status(pr_store):
    pr_store.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")
    pr_store.upsert({**_SAMPLE_PR, "pr_id": 102, "pr_url": "https://x/102", "status": "abandoned"},
                    "2026-01-01T00:00:00+00:00")
    active = pr_store.list_all(status="active")
    assert len(active) == 1
    assert active[0]["pr_id"] == 100


def test_list_all_filter_by_task_id(pr_store):
    pr_store.upsert({**_SAMPLE_PR, "task_id": "task-abc"}, "2026-01-01T00:00:00+00:00")
    pr_store.upsert({**_SAMPLE_PR, "pr_id": 103, "pr_url": "https://x/103"}, "2026-01-01T00:00:00+00:00")
    records = pr_store.list_all(task_id="task-abc")
    assert len(records) == 1
    assert records[0]["pr_id"] == 100


def test_update_status(pr_store):
    pr_store.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")
    found = pr_store.update_status(100, "abandoned", "2026-01-02T00:00:00+00:00")
    assert found is True
    assert pr_store.get(100)["status"] == "abandoned"


def test_update_status_missing_returns_false(pr_store):
    found = pr_store.update_status(9999, "abandoned", "2026-01-01T00:00:00+00:00")
    assert found is False


def test_cleanup_old_records(pr_store):
    import time
    old_ts = "2020-01-01T00:00:00+00:00"
    recent_ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    pr_store.upsert({**_SAMPLE_PR, "created_at": old_ts}, old_ts)
    pr_store.upsert({**_SAMPLE_PR, "pr_id": 200, "pr_url": "https://x/200"}, recent_ts)
    deleted = pr_store.cleanup_old_records(days=90)
    assert deleted == 1
    assert pr_store.get(100) is None
    assert pr_store.get(200) is not None


# ---------------------------------------------------------------------------
# GET /prs endpoint
# ---------------------------------------------------------------------------

def test_get_prs_empty(client):
    import importlib, src.pr_store as ps
    importlib.reload(ps); ps.init_db()
    resp = client.get("/prs", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_get_prs_returns_stored(client):
    import importlib, src.pr_store as ps
    importlib.reload(ps); ps.init_db()
    ps.upsert(_SAMPLE_PR, "2026-06-01T00:00:00+00:00")
    resp = client.get("/prs", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["pr_id"] == 100


def test_get_prs_filter_by_repo(client):
    import importlib, src.pr_store as ps
    importlib.reload(ps); ps.init_db()
    ps.upsert(_SAMPLE_PR, "2026-06-01T00:00:00+00:00")
    ps.upsert({**_SAMPLE_PR, "pr_id": 101, "pr_url": "https://x/101", "repo": "other"},
              "2026-06-01T00:00:00+00:00")
    resp = client.get("/prs?repo=my-repo", headers=_HEADERS)
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["repo"] == "my-repo"


def test_get_prs_filter_by_status(client):
    import importlib, src.pr_store as ps
    importlib.reload(ps); ps.init_db()
    ps.upsert(_SAMPLE_PR, "2026-06-01T00:00:00+00:00")
    ps.upsert({**_SAMPLE_PR, "pr_id": 102, "pr_url": "https://x/102", "status": "abandoned"},
              "2026-06-01T00:00:00+00:00")
    resp = client.get("/prs?status=abandoned", headers=_HEADERS)
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["pr_id"] == 102


def test_get_prs_no_token(client):
    resp = client.get("/prs")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /prs/<pr_id> endpoint
# ---------------------------------------------------------------------------

def _mock_get_response(status: str = "active"):
    m = MagicMock()
    m.ok = True
    m.status_code = 200
    m.json.return_value = {
        "pullRequestId": 100,
        "status": status,
        "sourceRefName": "refs/heads/feature/test",
        "targetRefName": "refs/heads/test",
        "title": "Test PR",
        "lastMergeSourceCommit": {"commitId": "abc123"},
    }
    return m


def test_get_pr_record_refreshes_status(client):
    import importlib, src.pr_store as ps
    importlib.reload(ps); ps.init_db()
    ps.upsert(_SAMPLE_PR, "2026-06-01T00:00:00+00:00")

    with patch("requests.get", return_value=_mock_get_response("completed")):
        resp = client.get("/prs/100", headers=_HEADERS)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pr_id"] == 100
    assert data["status"] == "completed"
    assert ps.get(100)["status"] == "completed"


def test_get_pr_record_not_in_registry_uses_live_data(client, monkeypatch):
    monkeypatch.delenv("AZURE_REPO", raising=False)
    with patch("requests.get", return_value=_mock_get_response("active")):
        resp = client.get("/prs/100?repo=my-repo", headers=_HEADERS)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pr_id"] == 100
    assert data["source_branch"] == "feature/test"


def test_get_pr_record_not_found_in_azure(client):
    import src.pr_store as ps
    # Not in registry, not in Azure
    m = MagicMock()
    m.ok = False
    m.status_code = 404
    m.json.return_value = {}

    not_found = MagicMock()
    not_found.ok = True
    not_found.status_code = 404
    not_found.json.return_value = {}

    with patch("requests.get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.status_code = 404
        mock_get.return_value.json.return_value = {}
        # _get_pr returns {} on 404 → no record → 404
        resp = client.get("/prs/9999?repo=my-repo", headers=_HEADERS)

    assert resp.status_code == 404


def test_get_pr_record_azure_error(client):
    import src.pr_store as ps
    ps.upsert(_SAMPLE_PR, "2026-01-01T00:00:00+00:00")

    m = MagicMock()
    m.ok = False
    m.status_code = 500
    m.text = "Internal Server Error"
    with patch("requests.get", return_value=m):
        resp = client.get("/prs/100", headers=_HEADERS)

    assert resp.status_code == 502


def test_get_pr_record_no_token(client):
    resp = client.get("/prs/100")
    assert resp.status_code == 401
