"""Tests for src/branch_config.py."""

import json
import pytest
import src.branch_config as bc


@pytest.fixture(autouse=True)
def reset_registry():
    """Force reload before each test so state doesn't leak."""
    bc._registry = None
    yield
    bc._registry = None


def test_defaults_present():
    registry = bc.get_registry()
    assert set(registry.keys()) >= {"developer", "test", "develop", "main"}


def test_default_labels():
    assert bc.label("developer") == "desarrollo"
    assert bc.label("test") == "pruebas"
    assert bc.label("develop") == "producción (pre)"
    assert bc.label("main") == "producción (desplegado)"


def test_label_fallback_for_unknown():
    assert bc.label("unknown-branch") == "unknown-branch"


def test_base_branch_is_develop():
    assert bc.base_branch() == "develop"


def test_known_targets_excludes_base():
    targets = bc.known_targets()
    assert "develop" not in targets
    assert "developer" in targets
    assert "test" in targets
    assert "main" in targets


def test_get_returns_none_for_unknown():
    assert bc.get("nonexistent") is None


def test_save_and_reload(tmp_path, monkeypatch):
    config_path = tmp_path / "branch_config.json"
    monkeypatch.setattr(bc, "_CONFIG_PATH", config_path)
    bc._registry = None

    bc.save({"staging": {"label": "staging env", "environment": "Staging", "url": None, "is_base": False}})

    registry = bc.get_registry()
    assert "staging" in registry
    assert registry["staging"]["label"] == "staging env"
    # defaults still present
    assert "developer" in registry


def test_save_overrides_existing_label(tmp_path, monkeypatch):
    config_path = tmp_path / "branch_config.json"
    monkeypatch.setattr(bc, "_CONFIG_PATH", config_path)
    bc._registry = None

    bc.save({"developer": {"label": "custom-dev", "environment": None, "url": None, "is_base": False}})
    assert bc.label("developer") == "custom-dev"


def test_corrupted_config_falls_back_to_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "branch_config.json"
    config_path.write_text("not valid json")
    monkeypatch.setattr(bc, "_CONFIG_PATH", config_path)
    bc._registry = None

    registry = bc.get_registry()
    assert "developer" in registry


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TOKEN_AZURE", "test-token")
    monkeypatch.setattr(bc, "_CONFIG_PATH", tmp_path / "branch_config.json")
    bc._registry = None

    import importlib
    import src.task_store as ts
    import src.auth as auth
    import app as app_module
    importlib.reload(ts)
    importlib.reload(auth)
    importlib.reload(app_module)

    app_module.app.testing = True
    with app_module.app.test_client() as c:
        yield c


def test_get_branches_endpoint(app_client):
    resp = app_client.get("/config/branches", headers={"X-Agent-Token": "test-token"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "developer" in data
    assert data["developer"]["label"] == "desarrollo"


def test_put_branches_endpoint(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(bc, "_CONFIG_PATH", tmp_path / "branch_config.json")
    bc._registry = None

    resp = app_client.put(
        "/config/branches",
        json={"developer": {"label": "mi-dev", "environment": None, "url": None, "is_base": False}},
        headers={"X-Agent-Token": "test-token"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["developer"]["label"] == "mi-dev"


def test_put_branches_bad_body(app_client):
    resp = app_client.put(
        "/config/branches",
        data="not json",
        content_type="text/plain",
        headers={"X-Agent-Token": "test-token"},
    )
    assert resp.status_code == 400
