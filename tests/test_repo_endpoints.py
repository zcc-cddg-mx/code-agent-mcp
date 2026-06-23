"""Tests for /repos endpoints in app.py."""

import pytest
from unittest.mock import patch


_HEADERS = {"X-Agent-Token": "test-token"}

_INSPECT_RESULT = {
    "name":           "ov-arizona-restat",
    "git_url":        "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
    "org":            "ZurichInsurance-EC",
    "project":        "Oficina-Virtual-ZEC",
    "azure_repo_id":  "ba5cfbbc-0000",
    "default_branch": "develop",
    "web_url":        "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
    "branches":       {"integration": ["develop", "developer"], "feature": [], "other": []},
    "known_branches": ["develop", "developer"],
    "size_kb":        86605,
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("AGENT_TOKEN", "test-token")
    monkeypatch.setenv("AZURE_PAT", "fake-pat")

    import importlib
    import src.task_store as ts
    import src.repo_store as rs
    import src.auth as auth
    import app as app_module
    for mod in (ts, rs, auth, app_module):
        importlib.reload(mod)

    app_module.app.testing = True
    with app_module.app.test_client() as c:
        yield c


def test_register_repo_happy_path(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        resp = client.post("/repos", json={
            "git_url": "https://ZurichInsurance-EC@dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"
        }, headers=_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["name"] == "ov-arizona-restat"
    assert data["default_branch"] == "develop"
    assert "known_branches" in data
    assert "repo_id" in data


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


def test_list_repos(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"}, headers=_HEADERS)
    resp = client.get("/repos", headers=_HEADERS)
    assert resp.status_code == 200
    assert len(resp.get_json()) == 1


def test_get_repo_by_name(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"}, headers=_HEADERS)
    resp = client.get("/repos/ov-arizona-restat", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "ov-arizona-restat"


def test_get_repo_not_found(client):
    resp = client.get("/repos/nonexistent", headers=_HEADERS)
    assert resp.status_code == 404


def test_refresh_repo(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"}, headers=_HEADERS)

    refreshed = {**_INSPECT_RESULT, "size_kb": 99999}
    with patch("src.repo_inspector.inspect", return_value=refreshed):
        resp = client.post("/repos/ov-arizona-restat/refresh", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["size_kb"] == 99999


def test_delete_repo(client):
    with patch("src.repo_inspector.inspect", return_value=_INSPECT_RESULT):
        client.post("/repos", json={"git_url": "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"}, headers=_HEADERS)

    resp = client.delete("/repos/ov-arizona-restat", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == "ov-arizona-restat"

    assert client.get("/repos/ov-arizona-restat", headers=_HEADERS).status_code == 404
