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


def _get_current_head(repo_dir: str) -> str:
    """Return the current branch name (or commit hash if detached HEAD)."""
    result = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _file_content_on_branch(repo_dir: str, branch: str, rel_path: str) -> bytes | None:
    """Return file content from a branch via git show, or None if the file doesn't exist there."""
    result = subprocess.run(
        ["git", "-C", repo_dir, "show", f"{branch}:{rel_path}"],
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def ensure_auxiliary_branch(
    repo_root: Path,
    feature_branch: str,
    target: str,
    files: list[Path],
    ticket_id: str,
    commit_message: str,
) -> tuple[str, str]:
    """Ensure the auxiliary branch exists on origin and contains all *files* from *feature_branch*.

    Returns (aux_branch_name, action) where action is one of:
      "created"   — branch did not exist; created from origin/{target}
      "updated"   — branch existed but was missing some files; files were applied
      "unchanged" — branch existed and already had all files up to date
    """
    aux = aux_branch_name(feature_branch, target)
    r = str(Path(repo_root).resolve())
    abs_root = Path(repo_root).resolve()

    log("GIT", f"fetch origin/{target} and origin/{feature_branch}")
    subprocess.run(["git", "-C", r, "fetch", "origin", target], check=True)
    subprocess.run(["git", "-C", r, "fetch", "origin", feature_branch], check=True)

    ls = subprocess.run(
        ["git", "-C", r, "ls-remote", "--heads", "origin", aux],
        capture_output=True, text=True,
    )
    aux_exists = bool(ls.returncode == 0 and ls.stdout.strip())

    def _apply_files(source_ref: str, dest_files: list[Path]) -> None:
        for f in dest_files:
            rel = str(Path(f).resolve().relative_to(abs_root))
            content = _file_content_on_branch(r, f"origin/{feature_branch}", rel)
            if content is None:
                raise RuntimeError(f"File '{rel}' not found on branch '{feature_branch}'")
            dest = abs_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

    original_head = _get_current_head(r)

    if not aux_exists:
        log("GIT", f"aux branch '{aux}' does not exist — creating from origin/{target}")
        subprocess.run(["git", "-C", r, "checkout", "-b", aux, f"origin/{target}"], check=True)
        _apply_files(feature_branch, files)
        rel_files = [str(Path(f).resolve().relative_to(abs_root)) for f in files]
        subprocess.run(["git", "-C", r, "add"] + rel_files, check=True)
        subprocess.run(["git", "-C", r, "commit", "-m", f"[{ticket_id}] {commit_message}"], check=True)
        _push_branch(r, aux)
        subprocess.run(["git", "-C", r, "checkout", original_head], check=True)
        subprocess.run(["git", "-C", r, "branch", "-D", aux], check=True)
        return aux, "created"

    # Branch exists — fetch it and check which files differ
    subprocess.run(["git", "-C", r, "fetch", "origin", aux], check=True)

    outdated: list[Path] = []
    for f in files:
        rel = str(Path(f).resolve().relative_to(abs_root))
        feature_content = _file_content_on_branch(r, f"origin/{feature_branch}", rel)
        aux_content = _file_content_on_branch(r, f"origin/{aux}", rel)
        if feature_content != aux_content:
            outdated.append(f)

    if not outdated:
        log("GIT", f"aux branch '{aux}' already up to date — nothing to do")
        return aux, "unchanged"

    log("GIT", f"aux branch '{aux}' exists but {len(outdated)} file(s) differ — updating")
    local_tmp = f"{aux}_update_tmp"
    subprocess.run(["git", "-C", r, "checkout", "-b", local_tmp, f"origin/{aux}"], check=True)
    _apply_files(feature_branch, outdated)
    rel_outdated = [str(Path(f).resolve().relative_to(abs_root)) for f in outdated]
    subprocess.run(["git", "-C", r, "add"] + rel_outdated, check=True)
    subprocess.run(["git", "-C", r, "commit", "-m", f"[{ticket_id}] {commit_message} (update)"], check=True)
    # Rename local branch to the canonical aux name so _push_branch pushes to the right remote ref
    subprocess.run(["git", "-C", r, "branch", "-m", local_tmp, aux], check=True)
    _push_branch(r, aux)
    subprocess.run(["git", "-C", r, "checkout", original_head], check=True)
    subprocess.run(["git", "-C", r, "branch", "-D", aux], check=True)
    return aux, "updated"


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
