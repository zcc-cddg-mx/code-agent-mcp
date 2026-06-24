"""Tests for src/branch_config.py — SQLite-backed branch dictionary."""

import importlib
import pytest
import src.branch_config as bc


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    """Each test gets an isolated DB and a fresh module state."""
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))
    importlib.reload(bc)
    bc.init_db()
    yield
    bc._registry = None


# ─── Defaults ────────────────────────────────────────────────────────────────

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


def test_role_returns_correct_value():
    assert bc.role("developer") == "integration"
    assert bc.role("develop") == "base"
    assert bc.role("nonexistent") is None


# ─── Persistence ─────────────────────────────────────────────────────────────

def test_save_adds_new_branch():
    bc.save({"staging": {"label": "staging env", "environment": "Staging",
                         "url": None, "is_base": False, "role": "integration"}})
    registry = bc.get_registry()
    assert "staging" in registry
    assert registry["staging"]["label"] == "staging env"
    # defaults still present
    assert "developer" in registry


def test_save_overrides_existing_label():
    bc.save({"developer": {"label": "custom-dev", "environment": None,
                            "url": None, "is_base": False, "role": "integration"}})
    assert bc.label("developer") == "custom-dev"


def test_save_persists_across_reload():
    bc.save({"developer": {"label": "persisted", "environment": None,
                            "url": None, "is_base": False, "role": "integration"}})
    bc.reload()
    assert bc.label("developer") == "persisted"


def test_init_db_idempotent():
    """Calling init_db() twice must not duplicate or overwrite saved data."""
    bc.save({"developer": {"label": "my-label", "environment": None,
                            "url": None, "is_base": False, "role": "integration"}})
    bc.init_db()  # second call — table already populated, should be no-op
    assert bc.label("developer") == "my-label"


# ─── Endpoints ───────────────────────────────────────────────────────────────

@pytest.fixture
def app_client(monkeypatch, tmp_path):
    monkeypatch.setenv("TASKS_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("TOKEN_AZURE", "test-token")

    import src.task_store as ts
    import src.auth as auth
    import app as app_module
    for mod in (bc, ts, auth, app_module):
        importlib.reload(mod)

    app_module.app.testing = True
    with app_module.app.test_client() as c:
        yield c


def test_get_branches_endpoint(app_client):
    resp = app_client.get("/config/branches", headers={"X-Agent-Token": "test-token"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "developer" in data
    assert data["developer"]["label"] == "desarrollo"


def test_put_branches_endpoint(app_client):
    resp = app_client.put(
        "/config/branches",
        json={"developer": {"label": "mi-dev", "environment": None,
                             "url": None, "is_base": False, "role": "integration"}},
        headers={"X-Agent-Token": "test-token"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["developer"]["label"] == "mi-dev"


def test_put_branches_persists_after_reload(app_client):
    app_client.put(
        "/config/branches",
        json={"test": {"label": "nuevo-test", "environment": None,
                       "url": None, "is_base": False, "role": "integration"}},
        headers={"X-Agent-Token": "test-token"},
    )
    bc.reload()
    assert bc.label("test") == "nuevo-test"


def test_put_branches_bad_body(app_client):
    resp = app_client.put(
        "/config/branches",
        data="not json",
        content_type="text/plain",
        headers={"X-Agent-Token": "test-token"},
    )
    assert resp.status_code == 400
