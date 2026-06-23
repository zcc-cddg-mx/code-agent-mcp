"""Generic git operations: branch, commit, push, auxiliary branch.

Branch strategy:
  feature/{ticket}_{suffix}            →  cut from origin/develop, pushed to origin
  {feature_branch}_{target}_auxiliar   →  cut from origin/{target}, receives only the
                                           files from the feature branch, pushed to origin
  PR target examples:
    developer  →  feature/ZNRX-1_desc_developer_auxiliar  (from origin/developer)
    test       →  feature/ZNRX-1_desc_test_auxiliar        (from origin/test)
    develop    →  feature/ZNRX-1_desc_develop_auxiliar     (from origin/develop)
    main       →  feature/ZNRX-1_desc_main_auxiliar        (from origin/main)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.logger import log
import src.branch_config as branch_config


def aux_branch_name(feature_branch: str, target: str) -> str:
    """Return the canonical auxiliary branch name for a given target.

    Convention: {feature_branch}_{target}_auxiliar
    Examples:
      feature/RITM2521020_relatividades_junio + developer
        → feature/RITM2521020_relatividades_junio_developer_auxiliar
      feature/RITM2521020_relatividades_junio + test
        → feature/RITM2521020_relatividades_junio_test_auxiliar
    """
    return f"{feature_branch}_{target}_auxiliar"


def create_feature_branch(repo_root: Path, branch_name: str, base_branch: str | None = None) -> None:
    """Create and checkout *branch_name* from *base_branch* in *repo_root*.

    Default base comes from branch_config.base_branch() (the branch marked is_base=True).
    """
    if base_branch is None:
        base_branch = branch_config.base_branch()
    r = str(Path(repo_root).resolve())
    log("GIT", f"fetch origin/{base_branch}")
    subprocess.run(["git", "-C", r, "fetch", "origin", base_branch], check=True)
    subprocess.run(["git", "-C", r, "stash"], check=True)
    subprocess.run(
        ["git", "-C", r, "checkout", "-b", branch_name, f"origin/{base_branch}"],
        check=True,
    )
    log("GIT", f"branch '{branch_name}' created from origin/{base_branch}")


def _push_branch(repo_dir: str, branch_name: str) -> None:
    """Push *branch_name* to origin, retrying with --force-with-lease if branch already exists."""
    result = subprocess.run(
        ["git", "-C", repo_dir, "push", "--set-upstream", "origin", branch_name],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    ls = subprocess.run(
        ["git", "-C", repo_dir, "ls-remote", "--heads", "origin", branch_name],
        capture_output=True, text=True,
    )
    if ls.returncode == 0 and ls.stdout.strip():
        log("GIT", f"branch '{branch_name}' already exists in origin, retrying with --force-with-lease")
        subprocess.run(
            ["git", "-C", repo_dir, "push", "--force-with-lease", "--set-upstream", "origin", branch_name],
            check=True,
        )
    else:
        raise subprocess.CalledProcessError(
            result.returncode, result.args,
            output=result.stdout, stderr=result.stderr,
        )


def git_add_commit_push(
    repo_root: Path,
    files: list[Path],
    ticket_id: str,
    commit_message: str,
    branch_name: str,
) -> str:
    """Stage *files*, commit with *commit_message*, push *branch_name*, return commit hash."""
    abs_root = Path(repo_root).resolve()
    rel_files = [str(Path(f).resolve().relative_to(abs_root)) for f in files]

    log("GIT", f"staging {len(rel_files)} file(s) on branch '{branch_name}'")
    subprocess.run(["git", "-C", str(abs_root), "add"] + rel_files, check=True)
    msg = f"[{ticket_id}] {commit_message}"
    subprocess.run(["git", "-C", str(abs_root), "commit", "-m", msg], check=True)
    log("GIT", f"pushing feature branch '{branch_name}' to origin")
    _push_branch(str(abs_root), branch_name)
    result = subprocess.run(
        ["git", "-C", str(abs_root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    commit_id = result.stdout.strip()
    log("GIT", f"pushed '{branch_name}' to origin (commit {commit_id[:8]})")
    return commit_id


def create_auxiliary_branch(
    repo_root: Path,
    feature_branch: str,
    target: str,
    files: list[Path],
    ticket_id: str,
    commit_message: str,
) -> str:
    """Create an auxiliary branch from origin/{target} containing only *files*.

    Branch name: {feature_branch}_{target}_auxiliar  (via aux_branch_name())
    Files are extracted from *feature_branch* via 'git show' — no merge, no conflicts.
    Returns the auxiliary branch name.
    """
    aux = aux_branch_name(feature_branch, target)
    r = str(Path(repo_root).resolve())
    abs_root = Path(repo_root).resolve()

    log("GIT", f"fetch origin/{target} for auxiliary branch")
    subprocess.run(["git", "-C", r, "fetch", "origin", target], check=True)
    subprocess.run(
        ["git", "-C", r, "checkout", "-b", aux, f"origin/{target}"],
        check=True,
    )
    log("GIT", f"aux branch '{aux}' created from origin/{target}")

    for f in files:
        rel = str(Path(f).resolve().relative_to(abs_root))
        result = subprocess.run(
            ["git", "-C", r, "show", f"{feature_branch}:{rel}"],
            check=True, capture_output=True,
        )
        dest = abs_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(result.stdout)

    rel_files = [str(Path(f).resolve().relative_to(abs_root)) for f in files]
    subprocess.run(["git", "-C", r, "add"] + rel_files, check=True)
    subprocess.run(["git", "-C", r, "commit", "-m", f"[{ticket_id}] {commit_message}"], check=True)

    log("GIT", f"pushing aux branch '{aux}' to origin")
    _push_branch(r, aux)
    log("GIT", f"pushed aux branch '{aux}' to origin")

    return aux
