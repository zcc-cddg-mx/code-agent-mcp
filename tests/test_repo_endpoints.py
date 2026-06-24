"""Tests for /repos, /projects, and /run endpoints in app.py."""

import pytest
from unittest.mock import patch


_HEADERS = {"X-Agent-Token": "test-token"}

_INSPECT_RESULT = {
    "repo": {
        "name":           "ov-arizona-restat",
        "git_url":        "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
        "org":            "ZurichInsurance-EC",
        "project":        "Oficina-Virtual-ZEC",
        "project_id":     "ZurichInsurance-EC/Oficina-Virtual-ZEC",
        "azure_repo_id":  "ba5cfbbc-0000",
        "default_branch": "develop",
        "web_url":        "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
        "branches":       {"integration": ["develop", "developer"], "feature": [], "other": []},
        "known_branches": ["develop", "developer"],
        "branch_roles":   {"develop": "base", "developer": "integration"},
        "size_kb":        86605,
    },
    "project": {
        "project_id":      "ZurichInsurance-EC/Oficina-Virtual-ZEC",
        "org":             "ZurichInsurance-EC",
        "name":            "Oficina-Virtual-ZEC",
        "azure_project_id": "c720df0b-0000",
        "description":     "Portal Brokers/Clientes",
        "visibility":      "private",
        "state":           "wellFormed",
        "web_url":         "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC",
    },
}

_INSPECT_RESULT_2 = {
    "repo": {
        "name":           "client-control-orchestration-service",
        "git_url":        "https://dev.azure.com/ZurichInsurance-EC/Zenith-ZEC/_git/client-control-orchestration-service",
        "org":            "ZurichInsurance-EC",
        "project":        "Zenith-ZEC",
        "project_id":     "ZurichInsurance-EC/Zenith-ZEC",
        "azure_repo_id":  "cccc0000-0000",
        "default_branch": "main",
        "web_url":        "https://dev.azure.com/ZurichInsurance-EC/Zenith-ZEC/_git/client-control-orchestration-service",
        "branches":       {"integration": ["main", "develop"], "feature": [], "other": []},
        "known_branches": ["main", "develop"],
        "branch_roles":   {"main": "integration", "develop": "base"},
        "size_kb":        12000,
    },
    "project": {
        "project_id":      "ZurichInsurance-EC/Zenith-ZEC",
        "org":             "ZurichInsurance-EC",
        "name":            "Zenith-ZEC",
        "azure_project_id": "aaaa0000-0000",
        "description":     "Zenith platform",
        "visibility":      "private",
        "state":           "wellFormed",
        "web_url":         "https://dev.azure.com/ZurichInsurance-EC/Zenith-ZEC",
    },
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TOKEN_AZURE", "test-token")
    monkeypatch.setenv("AZURE_PAT", "fake-pat")

    import importlib
    import src.task_store as ts
    import src.repo_store as rs
    import src.project_store as ps
    import src.auth as auth
    import app as app_module
    for mod in (ts, rs, ps, auth, app_module):
        importlib.reload(mod)

    app_module.app.testing = True
    with app_module.app.test_client() as c:
        yield c


# ─── POST /repos ─────────────────────────────────────────────────────────────

def test_register_repo_returns_repo_and_project(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        resp = client.post("/repos", json={
            "git_url": "https://ZurichInsurance-EC@dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"
        }, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["repo"]["name"] == "ov-arizona-restat"
    assert data["repo"]["project_id"] == "ZurichInsurance-EC/Oficina-Virtual-ZEC"
    assert data["project"]["name"] == "Oficina-Virtual-ZEC"
    assert data["project"]["visibility"] == "private"
    assert "repo_id" in data["repo"]


def test_register_repo_missing_url(client):
    resp = client.post("/repos", json={}, headers=_HEADERS)
    assert resp.status_code == 400


def test_register_repo_invalid_url(client):
    resp = client.post("/repos", json={"git_url": "https://github.com/org/repo"}, headers=_HEADERS)
    assert resp.status_code == 400


def test_register_repo_duplicate(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"}, headers=_HEADERS)
        resp = client.post("/repos", json={"git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"}, headers=_HEADERS)
    assert resp.status_code == 409


def test_register_two_repos_same_project_deduplicates_project(client):
    """Two repos in the same project → only one project record."""
    ov_restat = _INSPECT_RESULT
    ov_backend = {
        "repo": {**_INSPECT_RESULT["repo"], "name": "ov-arizona-backend-ecuador",
                 "azure_repo_id": "dddd0000", "git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-backend-ecuador"},
        "project": _INSPECT_RESULT["project"],
    }
    _url1 = "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"
    _url2 = "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-backend-ecuador"
    with patch("src.repo_inspector.inspect", side_effect=[ov_restat, ov_backend]):
        client.post("/repos", json={"git_url": _url1}, headers=_HEADERS)
        client.post("/repos", json={"git_url": _url2}, headers=_HEADERS)

    projects = client.get("/projects", headers=_HEADERS).get_json()
    assert len(projects) == 1
    assert set(projects[0]["repos"]) == {"ov-arizona-restat", "ov-arizona-backend-ecuador"}


def test_register_repos_different_projects(client):
    _url1 = "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"
    _url2 = "https://dev.azure.com/ZurichInsurance-EC/Zenith-ZEC/_git/client-control-orchestration-service"
    with patch("src.repo_inspector.inspect", side_effect=[_INSPECT_RESULT, _INSPECT_RESULT_2]):
        client.post("/repos", json={"git_url": _url1}, headers=_HEADERS)
        client.post("/repos", json={"git_url": _url2}, headers=_HEADERS)

    projects = client.get("/projects", headers=_HEADERS).get_json()
    assert len(projects) == 2
    names = {p["name"] for p in projects}
    assert names == {"Oficina-Virtual-ZEC", "Zenith-ZEC"}


# ─── GET /repos ──────────────────────────────────────────────────────────────

_URL_OV_RESTAT = "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"


def test_list_repos(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)
    assert len(client.get("/repos", headers=_HEADERS).get_json()) == 1


def test_get_repo_by_name(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)
    resp = client.get("/repos/ov-arizona-restat", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "ov-arizona-restat"


def test_get_repo_not_found(client):
    assert client.get("/repos/nonexistent", headers=_HEADERS).status_code == 404


def test_refresh_repo(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)

    refreshed = {**_INSPECT_RESULT, "repo": {**_INSPECT_RESULT["repo"], "size_kb": 99999}}
    with patch("src.repo_inspector.inspect", return_value=refreshed):
        resp = client.post("/repos/ov-arizona-restat/refresh", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["repo"]["size_kb"] == 99999


def test_delete_repo(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)

    resp = client.delete("/repos/ov-arizona-restat", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == "ov-arizona-restat"
    assert client.get("/repos/ov-arizona-restat", headers=_HEADERS).status_code == 404


# ─── GET /projects ───────────────────────────────────────────────────────────

def test_list_projects_empty(client):
    assert client.get("/projects", headers=_HEADERS).get_json() == []


def test_get_project_by_id(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)

    resp = client.get("/projects/ZurichInsurance-EC/Oficina-Virtual-ZEC", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["name"] == "Oficina-Virtual-ZEC"
    assert data["org"] == "ZurichInsurance-EC"
    assert "ov-arizona-restat" in data["repos"]


def test_get_project_not_found(client):
    assert client.get("/projects/Org/NonExistent", headers=_HEADERS).status_code == 404


# ─── PATCH /repos/<name>/branches/<branch> ───────────────────────────────────

def test_set_branch_role(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)

    resp = client.patch(
        "/repos/ov-arizona-restat/branches/develop",
        json={"role": "base"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["branch_roles"]["develop"] == "base"
    assert "base" in data["branches_by_role"]
    assert "develop" in data["branches_by_role"]["base"]


def test_set_branch_role_invalid(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)

    resp = client.patch(
        "/repos/ov-arizona-restat/branches/develop",
        json={"role": "invalid-role"},
        headers=_HEADERS,
    )
    assert resp.status_code == 400


def test_set_branch_role_repo_not_found(client):
    resp = client.patch(
        "/repos/nonexistent/branches/main",
        json={"role": "integration"},
        headers=_HEADERS,
    )
    assert resp.status_code == 404


def test_get_repo_includes_branches_by_role(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": _URL_OV_RESTAT}, headers=_HEADERS)

    data = client.get("/repos/ov-arizona-restat", headers=_HEADERS).get_json()
    assert "branches_by_role" in data


# ─── POST /run — registry validation ─────────────────────────────────────────

_RUN_BODY = {
    "repo":           "/tmp/ov-arizona-restat",
    "branch":         "feature/test",
    "files":          ["/tmp/ov-arizona-restat/src/File.java"],
    "ticket":         "ZNRX-001",
    "commit_message": "test commit",
}


def test_run_repo_not_registered_returns_403(client):
    """POST /run rejects repos not in the registry."""
    resp = client.post("/run", json=_RUN_BODY, headers=_HEADERS)
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["status"] == "error"
    assert "not registered" in body["error"]


def test_run_registered_repo_is_accepted(client):
    """POST /run accepts repos present in the registry."""
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos",
                    json={"git_url": _URL_OV_RESTAT},
                    headers=_HEADERS)

    with patch("src.placer.create_feature_branch"), \
         patch("src.placer.git_add_commit_push", return_value="abc123"), \
         patch("src.placer.create_auxiliary_branch", return_value="feature/test_developer_auxiliar"):
        resp = client.post("/run", json={**_RUN_BODY, "repo": "/tmp/ov-arizona-restat"}, headers=_HEADERS)

    assert resp.status_code == 202
    assert resp.get_json()["status"] in ("queued", "rejected")
