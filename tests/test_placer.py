"""Tests for src/placer.py — git calls are mocked via subprocess."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from src.placer import aux_branch_name, create_feature_branch, create_auxiliary_branch, git_add_commit_push


# ─── aux_branch_name ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("feature,target,expected", [
    (
        "feature/RITM2521020_relatividades_junio",
        "developer",
        "feature/RITM2521020_relatividades_junio_developer_auxiliar",
    ),
    (
        "feature/RITM2521020_relatividades_junio",
        "test",
        "feature/RITM2521020_relatividades_junio_test_auxiliar",
    ),
    (
        "feature/ZNRX-67108_renov_agosto",
        "develop",
        "feature/ZNRX-67108_renov_agosto_develop_auxiliar",
    ),
    (
        "fix/INC23186730_bug",
        "main",
        "fix/INC23186730_bug_main_auxiliar",
    ),
])
def test_aux_branch_name(feature, target, expected):
    assert aux_branch_name(feature, target) == expected


# ─── create_feature_branch ───────────────────────────────────────────────────

def test_create_feature_branch_default_base_is_develop(tmp_path):
    with patch("src.placer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        create_feature_branch(tmp_path, "feature/X_test")
    calls = [c.args[0] for c in mock_run.call_args_list]
    assert any("develop" in str(c) for c in calls)
    assert not any("developer" in str(c) for c in calls)


def test_create_feature_branch_custom_base(tmp_path):
    with patch("src.placer.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        create_feature_branch(tmp_path, "feature/X_test", base_branch="test")
    calls = [c.args[0] for c in mock_run.call_args_list]
    assert any("test" in str(c) for c in calls)


# ─── create_auxiliary_branch ─────────────────────────────────────────────────

def _mock_run_ok(*args, **kwargs):
    m = MagicMock(returncode=0, stdout=b"file-content", stderr="")
    return m


def test_create_auxiliary_branch_developer(tmp_path):
    (tmp_path / "subdir").mkdir()
    f = tmp_path / "subdir" / "file.txt"
    f.write_text("content")

    with patch("src.placer.subprocess.run", side_effect=_mock_run_ok):
        result = create_auxiliary_branch(tmp_path, "feature/X", "developer", [f], "T-1", "msg")

    assert result == "feature/X_developer_auxiliar"


def test_create_auxiliary_branch_test(tmp_path):
    (tmp_path / "subdir").mkdir()
    f = tmp_path / "subdir" / "file.txt"
    f.write_text("content")

    with patch("src.placer.subprocess.run", side_effect=_mock_run_ok):
        result = create_auxiliary_branch(tmp_path, "feature/X", "test", [f], "T-1", "msg")

    assert result == "feature/X_test_auxiliar"


def test_create_auxiliary_branch_fetches_correct_target(tmp_path):
    (tmp_path / "subdir").mkdir()
    f = tmp_path / "subdir" / "file.txt"
    f.write_text("content")

    with patch("src.placer.subprocess.run", side_effect=_mock_run_ok) as mock_run:
        create_auxiliary_branch(tmp_path, "feature/X", "test", [f], "T-1", "msg")

    all_calls = [str(c.args[0]) for c in mock_run.call_args_list]
    fetch_calls = [c for c in all_calls if "fetch" in c]
    assert any("test" in c for c in fetch_calls)
    assert not any("developer" in c for c in fetch_calls)
