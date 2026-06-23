"""Tests for src/placer.py — git calls are mocked via subprocess."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from src.placer import aux_branch_name, create_feature_branch, create_auxiliary_branch, git_add_commit_push, ensure_auxiliary_branch


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


# ─── ensure_auxiliary_branch ─────────────────────────────────────────────────

def _make_subprocess_mock(ls_remote_output: str, file_content_feature: bytes, file_content_aux: bytes | None):
    """
    Build a subprocess.run side_effect that handles ls-remote, rev-parse,
    git show (feature), git show (aux), and all other git commands.
    """
    call_counter = {"git_show": 0}

    def side_effect(cmd, **kwargs):
        m = MagicMock(returncode=0, stdout=b"", stderr="")
        cmd_str = " ".join(str(c) for c in cmd)

        if "ls-remote" in cmd_str:
            m.stdout = ls_remote_output
            return m

        if "rev-parse" in cmd_str:
            m.stdout = "develop\n"
            return m

        if "git" in cmd_str and "show" in cmd_str:
            # Alternate: first call → feature content, second → aux content
            idx = call_counter["git_show"]
            call_counter["git_show"] += 1
            if idx % 2 == 0:
                m.stdout = file_content_feature
            else:
                m.stdout = file_content_aux if file_content_aux is not None else b""
                m.returncode = 0 if file_content_aux is not None else 1
            return m

        return m

    return side_effect


def test_ensure_auxiliary_branch_creates_when_not_exists(tmp_path):
    f = tmp_path / "README.md"
    f.write_bytes(b"content")

    side_effect = _make_subprocess_mock(
        ls_remote_output="",            # branch does not exist
        file_content_feature=b"new content",
        file_content_aux=None,
    )

    with patch("src.placer.subprocess.run", side_effect=side_effect) as mock_run:
        aux, action = ensure_auxiliary_branch(tmp_path, "feature/X", "test", [f], "T-1", "msg")

    assert aux == "feature/X_test_auxiliar"
    assert action == "created"
    all_calls = [" ".join(str(a) for a in c.args[0]) for c in mock_run.call_args_list]
    assert any("checkout" in c and "feature/X_test_auxiliar" in c for c in all_calls)
    assert any("branch" in c and "-D" in c for c in all_calls)   # local cleanup


def test_ensure_auxiliary_branch_unchanged_when_files_match(tmp_path):
    f = tmp_path / "README.md"
    f.write_bytes(b"same content")

    side_effect = _make_subprocess_mock(
        ls_remote_output="abc123\trefs/heads/feature/X_test_auxiliar\n",
        file_content_feature=b"same content",
        file_content_aux=b"same content",   # identical → unchanged
    )

    with patch("src.placer.subprocess.run", side_effect=side_effect):
        aux, action = ensure_auxiliary_branch(tmp_path, "feature/X", "test", [f], "T-1", "msg")

    assert aux == "feature/X_test_auxiliar"
    assert action == "unchanged"


def test_ensure_auxiliary_branch_updates_when_files_differ(tmp_path):
    f = tmp_path / "README.md"
    f.write_bytes(b"old")

    side_effect = _make_subprocess_mock(
        ls_remote_output="abc123\trefs/heads/feature/X_test_auxiliar\n",
        file_content_feature=b"new content",
        file_content_aux=b"old content",    # different → update
    )

    with patch("src.placer.subprocess.run", side_effect=side_effect) as mock_run:
        aux, action = ensure_auxiliary_branch(tmp_path, "feature/X", "test", [f], "T-1", "msg")

    assert aux == "feature/X_test_auxiliar"
    assert action == "updated"
    all_calls = [" ".join(str(a) for a in c.args[0]) for c in mock_run.call_args_list]
    assert any("commit" in c for c in all_calls)
