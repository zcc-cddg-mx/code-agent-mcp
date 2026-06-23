"""Tests for src/repo_inspector.py — HTTP and subprocess calls are mocked."""

import pytest
from unittest.mock import patch, MagicMock
from src.repo_inspector import parse_azure_url, classify_branches, inspect


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

    assert result["name"] == "ov-arizona-restat"
    assert result["default_branch"] == "develop"
    assert result["size_kb"] == 86605
    assert "develop" in result["known_branches"]
    assert "developer" in result["known_branches"]
    assert "test" in result["known_branches"]
    assert "feature/RITM2521020_relatividades" in result["branches"]["feature"]


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

    assert result["name"] == "ov-arizona-restat"
    assert result["branches"] == {"integration": [], "feature": [], "other": []}


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
