"""Tests for src/repo_inspector.py — HTTP and subprocess calls are mocked."""

import pytest
from unittest.mock import patch, MagicMock
from src.repo_inspector import parse_azure_url, classify_branches, inspect, auto_assign_roles


# ─── parse_azure_url ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    (
        "https://ZurichInsurance-EC@dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
        {"org": "ZurichInsurance-EC", "project": "Oficina-Virtual-ZEC", "repo": "ov-arizona-restat"},
    ),
    (
        "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-backend-ecuador",
        {"org": "ZurichInsurance-EC", "project": "Oficina-Virtual-ZEC", "repo": "ov-arizona-backend-ecuador"},
    ),
])
def test_parse_azure_url(url, expected):
    result = parse_azure_url(url)
    assert result["org"] == expected["org"]
    assert result["project"] == expected["project"]
    assert result["repo"] == expected["repo"]
    assert "user@" not in result["clean_url"]


def test_parse_azure_url_invalid():
    with pytest.raises(ValueError):
        parse_azure_url("https://github.com/org/repo")


def test_parse_azure_url_malformed_path():
    with pytest.raises(ValueError):
        parse_azure_url("https://dev.azure.com/org/project/nope/repo")


# ─── classify_branches ───────────────────────────────────────────────────────

def test_classify_integration_branches():
    result = classify_branches(["main", "develop", "developer", "test"])
    assert set(result["integration"]) == {"main", "develop", "developer", "test"}
    assert result["feature"] == []
    assert result["other"] == []


def test_classify_feature_and_fix():
    branches = ["develop", "feature/ZNRX-1_test", "fix/INC001_bug", "release/1.0"]
    result = classify_branches(branches)
    assert "develop" in result["integration"]
    assert "feature/ZNRX-1_test" in result["feature"]
    assert "fix/INC001_bug" in result["feature"]
    assert "release/1.0" in result["other"]


# ─── auto_assign_roles ───────────────────────────────────────────────────────

def test_auto_assign_roles_known_branches():
    roles = auto_assign_roles(["develop", "developer", "test", "main"])
    assert roles["develop"] == "base"
    assert roles["developer"] == "integration"
    assert roles["test"] == "integration"
    assert roles["main"] == "integration"


def test_auto_assign_roles_feature_prefix():
    roles = auto_assign_roles(["feature/ZNRX-123_test", "fix/INC001_bug"])
    assert roles["feature/ZNRX-123_test"] == "feature"
    assert roles["fix/INC001_bug"] == "feature"


def test_auto_assign_roles_unknown_falls_back_to_other():
    roles = auto_assign_roles(["release/1.0", "hotfix/urgent"])
    assert roles["release/1.0"] == "other"
    assert roles["hotfix/urgent"] == "other"


def test_auto_assign_roles_mixed():
    branches = ["develop", "developer", "feature/ZNRX-1", "release/2.0"]
    roles = auto_assign_roles(branches)
    assert roles["develop"] == "base"
    assert roles["developer"] == "integration"
    assert roles["feature/ZNRX-1"] == "feature"
    assert roles["release/2.0"] == "other"


# ─── inspect ─────────────────────────────────────────────────────────────────

def _mock_azure_metadata():
    m = MagicMock()
    m.ok = True
    m.json.return_value = {
        "id":            "ba5cfbbc-0000-0000-0000-000000000000",
        "name":          "ov-arizona-restat",
        "defaultBranch": "refs/heads/develop",
        "webUrl":        "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
        "size":          86605,
        "project": {
            "id":             "c720df0b-0000-0000-0000-000000000000",
            "name":           "Oficina-Virtual-ZEC",
            "description":    "Portal Brokers/Clientes",
            "visibility":     "private",
            "state":          "wellFormed",
            "lastUpdateTime": "2026-05-29T15:08:25.077Z",
        },
    }
    return m


def _mock_ls_remote():
    m = MagicMock()
    m.returncode = 0
    m.stdout = (
        "abc123\trefs/heads/main\n"
        "def456\trefs/heads/develop\n"
        "ghi789\trefs/heads/developer\n"
        "jkl012\trefs/heads/test\n"
        "mno345\trefs/heads/feature/RITM2521020_relatividades\n"
    )
    m.stderr = ""
    return m


def test_inspect_happy_path():
    with patch("src.repo_inspector.requests.get", return_value=_mock_azure_metadata()), \
         patch("src.repo_inspector.subprocess.run", return_value=_mock_ls_remote()):
        result = inspect(
            "https://ZurichInsurance-EC@dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
            pat="fake-pat",
        )

    repo = result["repo"]
    assert repo["name"] == "ov-arizona-restat"
    assert repo["default_branch"] == "develop"
    assert repo["size_kb"] == 86605
    assert repo["project_id"] == "ZurichInsurance-EC/Oficina-Virtual-ZEC"
    assert "develop" in repo["known_branches"]
    assert "developer" in repo["known_branches"]
    assert "test" in repo["known_branches"]
    assert "feature/RITM2521020_relatividades" in repo["branches"]["feature"]
    assert "branch_roles" in repo
    assert repo["branch_roles"]["develop"] == "base"
    assert repo["branch_roles"]["developer"] == "integration"
    assert repo["branch_roles"]["feature/RITM2521020_relatividades"] == "feature"

    project = result["project"]
    assert project["project_id"] == "ZurichInsurance-EC/Oficina-Virtual-ZEC"
    assert project["name"] == "Oficina-Virtual-ZEC"
    assert project["org"] == "ZurichInsurance-EC"
    assert project["visibility"] == "private"
    assert project["state"] == "wellFormed"


def test_inspect_ls_remote_failure_still_returns_record():
    err = MagicMock()
    err.returncode = 1
    err.stderr = "authentication failed"

    with patch("src.repo_inspector.requests.get", return_value=_mock_azure_metadata()), \
         patch("src.repo_inspector.subprocess.run", return_value=err):
        result = inspect(
            "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
            pat="fake-pat",
        )

    assert result["repo"]["name"] == "ov-arizona-restat"
    assert result["repo"]["branches"] == {"integration": [], "feature": [], "other": []}
    assert result["project"]["name"] == "Oficina-Virtual-ZEC"


def test_inspect_azure_api_error():
    m = MagicMock()
    m.ok = False
    m.status_code = 401
    m.text = "Unauthorized"

    with patch("src.repo_inspector.requests.get", return_value=m):
        with pytest.raises(RuntimeError, match="401"):
            inspect(
                "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat",
                pat="bad-pat",
            )
