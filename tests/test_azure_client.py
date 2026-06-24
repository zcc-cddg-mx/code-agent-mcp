"""Tests for src/azure_client.py — all Azure HTTP calls are mocked."""

import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def env_vars(monkeypatch):
    monkeypatch.setenv("TOKEN_AZURE", "test-token")
    monkeypatch.setenv("AZURE_PAT", "fake-pat")
    monkeypatch.setenv("AZURE_ORG", "MyOrg")
    monkeypatch.setenv("AZURE_PROJECT", "MyProject")
    monkeypatch.setenv("AZURE_REPO", "my-repo")


@pytest.fixture
def client(env_vars, tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))

    import importlib
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


def _mock_post_response(pr_id: int):
    m = MagicMock()
    m.ok = True
    m.json.return_value = {"pullRequestId": pr_id}
    return m


def _mock_get_pr_response(status: str = "active"):
    m = MagicMock()
    m.ok = True
    m.status_code = 200
    m.json.return_value = {"pullRequestId": 123, "status": status}
    return m


def _mock_statuses_response(state: str = "succeeded"):
    m = MagicMock()
    m.ok = True
    m.json.return_value = {"value": [{"state": state}]}
    return m


# ─── POST /azure/pull-requests ───────────────────────────────────────────────

def test_create_pull_requests_happy_path(client):
    with patch("src.azure_client.requests.post") as mock_post:
        mock_post.side_effect = [_mock_post_response(101), _mock_post_response(102)]
        resp = client.post("/azure/pull-requests", json={
            "branch":      "feature/ZNRX-1_test",
            "aux_branch":  "feature/ZNRX-1_test_developer_auxiliar",
            "title":       "ZNRX-1 — test PR",
            "description": "test",
            "repo":        "my-repo",
            "target":      "developer",
        }, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["feature_pr"]["pr_id"] == 101
    assert data["aux_pr"]["pr_id"] == 102
    assert "dev.azure.com" in data["feature_pr"]["pr_url"]


def test_create_pull_requests_missing_fields(client):
    resp = client.post("/azure/pull-requests", json={"branch": "feat/x"}, headers=_HEADERS)
    assert resp.status_code == 400
    assert "Missing" in resp.get_json()["error"]


def test_create_pull_requests_azure_error(client):
    with patch("src.azure_client.requests.post") as mock_post:
        m = MagicMock()
        m.ok = False
        m.status_code = 422
        m.text = "TF401179"
        mock_post.return_value = m
        resp = client.post("/azure/pull-requests", json={
            "branch": "feat/x", "aux_branch": "feat/x_aux",
            "title": "T", "repo": "my-repo",
        }, headers=_HEADERS)
    assert resp.status_code == 502


def test_create_pull_requests_no_token(client):
    resp = client.post("/azure/pull-requests", json={
        "branch": "feat/x", "aux_branch": "feat/x_aux",
        "title": "T", "repo": "my-repo",
    })
    assert resp.status_code == 401


# ─── GET /azure/pull-requests/<pr_id> ────────────────────────────────────────

def test_get_pull_request_active(client):
    with patch("src.azure_client.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_get_pr_response("active"),
            _mock_statuses_response("succeeded"),
        ]
        resp = client.get("/azure/pull-requests/123?repo=my-repo", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pr_id"] == 123
    assert data["status"] == "active"
    assert data["build_status"] == "succeeded"
    assert "dev.azure.com" in data["pr_url"]


def test_get_pull_request_completed(client):
    with patch("src.azure_client.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_get_pr_response("completed"),
            _mock_statuses_response("succeeded"),
        ]
        resp = client.get("/azure/pull-requests/123?repo=my-repo", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "completed"


def test_get_pull_request_build_failed(client):
    with patch("src.azure_client.requests.get") as mock_get:
        mock_get.side_effect = [
            _mock_get_pr_response("active"),
            _mock_statuses_response("failed"),
        ]
        resp = client.get("/azure/pull-requests/123?repo=my-repo", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["build_status"] == "failed"


def test_get_pull_request_not_found(client):
    with patch("src.azure_client.requests.get") as mock_get:
        m = MagicMock()
        m.ok = False
        m.status_code = 404
        mock_get.return_value = m
        resp = client.get("/azure/pull-requests/999?repo=my-repo", headers=_HEADERS)
    assert resp.status_code == 404


def test_get_pull_request_missing_repo_param(client):
    # No repo param and AZURE_REPO not set for this test
    with patch.dict(os.environ, {"AZURE_REPO": ""}):
        resp = client.get("/azure/pull-requests/123", headers=_HEADERS)
    assert resp.status_code == 400


# ─── POST /azure/prepare-and-pr ──────────────────────────────────────────────

_PREPARE_BODY = {
    "repo":      "my-repo",
    "repo_path": "/tmp/fake-repo",
    "branch":    "feature/test_mcp_server",
    "files":     ["/tmp/fake-repo/README.md"],
    "target":    "test",
    "ticket":    "test_mcp",
    "title":     "test_mcp → test",
}


def test_prepare_and_pr_creates_branch_and_pr(client, tmp_path):
    with patch("src.placer.ensure_auxiliary_branch", return_value=("feature/test_mcp_server_test_auxiliar", "created")), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.azure_client.requests.post", return_value=_mock_post_response(2560)):
        resp = client.post("/azure/prepare-and-pr", json=_PREPARE_BODY, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["aux_branch"] == "feature/test_mcp_server_test_auxiliar"
    assert data["action"] == "created"
    assert data["pr"]["pr_id"] == 2560


def test_prepare_and_pr_returns_existing_pr(client):
    existing = {"pr_id": 2555, "pr_url": "https://dev.azure.com/.../pullrequest/2555"}
    with patch("src.placer.ensure_auxiliary_branch", return_value=("feature/test_mcp_server_test_auxiliar", "unchanged")), \
         patch("src.azure_client._find_existing_pr", return_value=existing):
        resp = client.post("/azure/prepare-and-pr", json=_PREPARE_BODY, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["action"] == "unchanged"
    assert data["pr"]["pr_id"] == 2555


def test_prepare_and_pr_updates_branch_and_creates_pr(client):
    with patch("src.placer.ensure_auxiliary_branch", return_value=("feature/test_mcp_server_test_auxiliar", "updated")), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.azure_client.requests.post", return_value=_mock_post_response(2561)):
        resp = client.post("/azure/prepare-and-pr", json=_PREPARE_BODY, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["action"] == "updated"
    assert data["pr"]["pr_id"] == 2561


def test_prepare_and_pr_missing_fields(client):
    resp = client.post("/azure/prepare-and-pr", json={"repo": "x"}, headers=_HEADERS)
    assert resp.status_code == 400
    assert "Missing" in resp.get_json()["error"]


def test_prepare_and_pr_git_error(client):
    with patch("src.placer.ensure_auxiliary_branch", side_effect=RuntimeError("git failed")):
        resp = client.post("/azure/prepare-and-pr", json=_PREPARE_BODY, headers=_HEADERS)
    assert resp.status_code == 502


def test_prepare_and_pr_response_includes_files_detected(client):
    with patch("src.placer.ensure_auxiliary_branch", return_value=("feature/test_mcp_server_test_auxiliar", "created")), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.azure_client.requests.post", return_value=_mock_post_response(2562)):
        resp = client.post("/azure/prepare-and-pr", json=_PREPARE_BODY, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert "files_detected" in data
    assert data["files_detected"] == ["/tmp/fake-repo/README.md"]


# ─── POST /azure/prepare-and-pr — auto-detect files ─────────────────────────

_PREPARE_BODY_NO_FILES = {
    "repo":      "my-repo",
    "repo_path": "/tmp/fake-repo",
    "branch":    "feature/test_mcp_server",
    "target":    "test",
    "ticket":    "test_mcp",
    "title":     "test_mcp → test",
}


def test_prepare_and_pr_auto_detects_files(client, tmp_path):
    detected = [tmp_path / "README.md"]
    with patch("subprocess.run") as mock_sp, \
         patch("src.placer.detect_changed_files", return_value=detected), \
         patch("src.placer.ensure_auxiliary_branch", return_value=("feature/test_mcp_server_test_auxiliar", "created")), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.azure_client.requests.post", return_value=_mock_post_response(2563)):
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREPARE_BODY_NO_FILES)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr", json=body, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["action"] == "created"
    assert len(data["files_detected"]) == 1
    assert "README.md" in data["files_detected"][0]


def test_prepare_and_pr_auto_detect_no_changes(client, tmp_path):
    with patch("subprocess.run") as mock_sp, \
         patch("src.placer.detect_changed_files", return_value=[]):
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREPARE_BODY_NO_FILES)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr", json=body, headers=_HEADERS)
    assert resp.status_code == 400
    assert "No changed files" in resp.get_json()["error"]


def test_prepare_and_pr_auto_detect_fetch_error(client, tmp_path):
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(128, "git", stderr="fatal")):
        body = dict(_PREPARE_BODY_NO_FILES)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr", json=body, headers=_HEADERS)
    assert resp.status_code == 502
    assert "git fetch failed" in resp.get_json()["error"]


def test_prepare_and_pr_response_includes_base_branch(client, tmp_path):
    detected = [tmp_path / "README.md"]
    with patch("subprocess.run") as mock_sp, \
         patch("src.placer.detect_changed_files", return_value=detected), \
         patch("src.placer.detect_base_branch", return_value="develop"), \
         patch("src.placer.ensure_auxiliary_branch", return_value=("feature/test_mcp_server_test_auxiliar", "created")), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.azure_client.requests.post", return_value=_mock_post_response(2564)):
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREPARE_BODY_NO_FILES)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr", json=body, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert "base_branch" in data
    assert data["base_branch"] == "develop"


def test_prepare_and_pr_auto_detects_base_from_repo_roles(client, tmp_path):
    """When repo is registered with branch_roles, detect_base_branch is called with those candidates."""
    detected = [tmp_path / "avisos.component.html"]
    repo_record = {
        "name": "my-repo",
        "branch_roles": {"develop": "base", "test": "integration", "main": "integration"},
    }
    with patch("subprocess.run") as mock_sp, \
         patch("src.repo_store.get_by_name", return_value=repo_record), \
         patch("src.placer.detect_base_branch", return_value="test") as mock_detect_base, \
         patch("src.placer.detect_changed_files", return_value=detected), \
         patch("src.placer.ensure_auxiliary_branch", return_value=("fix/X_test_auxiliar", "created")), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.azure_client.requests.post", return_value=_mock_post_response(2565)):
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREPARE_BODY_NO_FILES)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr", json=body, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["base_branch"] == "test"
    # base-role branches (develop) must come before integration in the candidates list
    call_args = mock_detect_base.call_args
    candidates = call_args[0][2]
    assert candidates.index("develop") < candidates.index("test")


# ─── POST /azure/prepare-and-pr/preview ─────────────────────────────────────

_PREVIEW_BODY = {
    "repo":      "my-repo",
    "repo_path": "/tmp/fake-repo",
    "branch":    "feature/test_mcp_server",
    "target":    "test",
}


def test_preview_happy_path_with_files(client, tmp_path):
    existing = {"pr_id": 2560, "pr_url": "https://dev.azure.com/.../pullrequest/2560"}
    body = dict(_PREVIEW_BODY)
    body["files"] = ["/tmp/fake-repo/README.md"]
    with patch("src.azure_client._find_existing_pr", return_value=existing):
        resp = client.post("/azure/prepare-and-pr/preview", json=body, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["branch"] == "feature/test_mcp_server"
    assert data["target"] == "test"
    assert data["aux_branch"] == "feature/test_mcp_server_test_auxiliar"
    assert data["files_detected"] == ["/tmp/fake-repo/README.md"]
    assert data["existing_pr"]["pr_id"] == 2560


def test_preview_auto_detects_files_no_existing_pr(client, tmp_path):
    detected = [tmp_path / "avisos.html"]
    with patch("subprocess.run") as mock_sp, \
         patch("src.placer.detect_changed_files", return_value=detected), \
         patch("src.placer.detect_base_branch", return_value="develop"), \
         patch("src.azure_client._find_existing_pr", return_value=None):
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREVIEW_BODY)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr/preview", json=body, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["base_branch"] == "develop"
    assert len(data["files_detected"]) == 1
    assert data["existing_pr"] is None


def test_preview_missing_required_fields(client):
    resp = client.post("/azure/prepare-and-pr/preview",
                       json={"repo": "x"}, headers=_HEADERS)
    assert resp.status_code == 400
    assert "Missing" in resp.get_json()["error"]


def test_preview_no_changed_files_returns_400(client, tmp_path):
    with patch("subprocess.run") as mock_sp, \
         patch("src.placer.detect_changed_files", return_value=[]):
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREVIEW_BODY)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr/preview", json=body, headers=_HEADERS)
    assert resp.status_code == 400
    assert "No changed files" in resp.get_json()["error"]


def test_preview_fetch_error_returns_502(client, tmp_path):
    import subprocess
    with patch("subprocess.run",
               side_effect=subprocess.CalledProcessError(128, "git", stderr="fatal")):
        body = dict(_PREVIEW_BODY)
        body["repo_path"] = str(tmp_path)
        resp = client.post("/azure/prepare-and-pr/preview", json=body, headers=_HEADERS)
    assert resp.status_code == 502
    assert "git fetch failed" in resp.get_json()["error"]


def test_preview_does_not_create_aux_branch_or_pr(client, tmp_path):
    """Preview must not call ensure_auxiliary_branch or _create_pr."""
    detected = [tmp_path / "file.ts"]
    with patch("subprocess.run") as mock_sp, \
         patch("src.placer.detect_changed_files", return_value=detected), \
         patch("src.placer.detect_base_branch", return_value="develop"), \
         patch("src.azure_client._find_existing_pr", return_value=None), \
         patch("src.placer.ensure_auxiliary_branch") as mock_ensure, \
         patch("src.azure_client._create_pr") as mock_create:
        mock_sp.return_value = MagicMock(returncode=0, stdout="", stderr="")
        body = dict(_PREVIEW_BODY)
        body["repo_path"] = str(tmp_path)
        client.post("/azure/prepare-and-pr/preview", json=body, headers=_HEADERS)
    mock_ensure.assert_not_called()
    mock_create.assert_not_called()
