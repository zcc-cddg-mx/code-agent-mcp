"""Repository inspection — parses Azure DevOps git URLs and discovers repo metadata.

Two data sources:
  1. git ls-remote  — lists all remote branches without cloning (needs PAT in URL)
  2. Azure DevOps REST API — returns repo metadata (size, defaultBranch, webUrl, etc.)

Parses URLs in these formats:
  https://ZurichInsurance-EC@dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat
  https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat
"""

from __future__ import annotations

import os
import re
import subprocess
from base64 import b64encode
from urllib.parse import urlparse

import requests

import src.branch_config as branch_config
from src.logger import log

_AZURE_API_VERSION = "7.1"
_VERIFY_SSL = os.environ.get("AZURE_VERIFY_SSL", "true").strip().lower() != "false"

# Branches considered "main/integration" branches — used to classify discovered branches
_KNOWN_INTEGRATION_BRANCHES = {"main", "develop", "developer", "test", "desarrollo"}


def parse_azure_url(git_url: str) -> dict:
    """Extract org, project, repo name from an Azure DevOps git URL.

    Returns: {org, project, repo, clean_url}
    Raises ValueError if the URL is not a recognized Azure DevOps format.
    """
    # Strip user@ prefix if present (e.g. ZurichInsurance-EC@dev.azure.com)
    url = re.sub(r"^https://[^@]+@", "https://", git_url.strip())

    parsed = urlparse(url)
    if "dev.azure.com" not in parsed.netloc:
        raise ValueError(f"Not an Azure DevOps URL: {git_url!r}")

    # Path: /<org>/<project>/_git/<repo>
    parts = [p for p in parsed.path.split("/") if p]
    try:
        git_idx = parts.index("_git")
        org = parts[0]
        project = parts[1]
        repo = parts[git_idx + 1]
    except (ValueError, IndexError):
        raise ValueError(f"Cannot parse org/project/repo from URL: {git_url!r}")

    clean_url = f"https://dev.azure.com/{org}/{project}/_git/{repo}"
    return {"org": org, "project": project, "repo": repo, "clean_url": clean_url}


def _auth_header(pat: str) -> dict:
    token = b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def fetch_azure_metadata(org: str, project: str, repo: str, pat: str) -> dict:
    """Call Azure DevOps REST API and return raw repo metadata."""
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/git/repositories"
        f"/{repo}?api-version={_AZURE_API_VERSION}"
    )
    log("REPO", f"fetching Azure metadata for {org}/{project}/{repo}")
    resp = requests.get(url, headers=_auth_header(pat), verify=_VERIFY_SSL, timeout=15)
    if not resp.ok:
        raise RuntimeError(
            f"Azure DevOps API error {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def list_remote_branches(git_url: str, pat: str) -> list[str]:
    """Run git ls-remote to list all branches without cloning.

    Injects PAT into the URL as Basic auth credential.
    Returns a sorted list of branch names (refs/heads/ prefix stripped).
    """
    # Inject PAT: https://PAT@dev.azure.com/...
    auth_url = re.sub(r"^https://", f"https://:{pat}@", git_url)
    log("REPO", f"git ls-remote {git_url}")
    result = subprocess.run(
        ["git", "ls-remote", "--heads", auth_url],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-remote failed: {result.stderr.strip()[:300]}")

    branches = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            branches.append(parts[1].removeprefix("refs/heads/"))
    return sorted(branches)


def classify_branches(branches: list[str]) -> dict:
    """Classify discovered branches into integration vs feature/fix.

    Cross-references with branch_config registry and the known integration set.
    Returns:
      {
        "integration": [...],   # main, develop, developer, test, etc.
        "feature":     [...],   # feature/* fix/* etc.
        "other":       [...],   # anything else
      }
    """
    registry_keys = set(branch_config.get_registry().keys())
    all_known = _KNOWN_INTEGRATION_BRANCHES | registry_keys

    integration, feature, other = [], [], []
    for b in branches:
        if b in all_known:
            integration.append(b)
        elif b.startswith(("feature/", "fix/")):
            feature.append(b)
        else:
            other.append(b)

    return {"integration": integration, "feature": feature, "other": other}


def auto_assign_roles(branches: list[str]) -> dict[str, str]:
    """Auto-detect the logical role of each branch for a specific repo.

    Priority:
    1. Branch is in the global branch_config registry → use its role field
    2. Branch starts with feature/ or fix/ → "feature"
    3. Branch is in _KNOWN_INTEGRATION_BRANCHES → "integration"
    4. Otherwise → "other"

    Returns a dict mapping branch name → role string.
    """
    roles: dict[str, str] = {}
    for b in branches:
        global_role = branch_config.role(b)
        if global_role:
            roles[b] = global_role
        elif b.startswith(("feature/", "fix/")):
            roles[b] = "feature"
        elif b in _KNOWN_INTEGRATION_BRANCHES:
            roles[b] = "integration"
        else:
            roles[b] = "other"
    return roles


def extract_project_info(org: str, project_name: str, metadata: dict) -> dict:
    """Extract project-level fields from a repo metadata response.

    The Azure DevOps repo endpoint embeds the parent project object, so no
    extra API call is needed.
    Returns a dict ready to be stored via project_store.upsert().
    """
    from src.project_store import slug
    p = metadata.get("project", {})
    return {
        "project_id":       slug(org, project_name),
        "org":               org,
        "name":              project_name,
        "azure_project_id":  p.get("id"),
        "description":       p.get("description"),
        "visibility":        p.get("visibility"),
        "state":             p.get("state"),
        "web_url":           f"https://dev.azure.com/{org}/{project_name}",
        "last_update_time":  p.get("lastUpdateTime"),
    }


def inspect(git_url: str, pat: str) -> dict:
    """Full inspection of a repo: parse URL, fetch metadata, list branches.

    Returns:
      {
        "repo":    dict ready for repo_store.upsert(),
        "project": dict ready for project_store.upsert(),
      }
    """
    parsed = parse_azure_url(git_url)
    org, project_name, repo_name = parsed["org"], parsed["project"], parsed["repo"]

    # Azure DevOps metadata (includes embedded project object)
    metadata = fetch_azure_metadata(org, project_name, repo_name, pat)

    # Remote branches via git ls-remote
    clean_url = parsed["clean_url"]
    try:
        branches = list_remote_branches(clean_url, pat)
    except RuntimeError as exc:
        log("REPO", f"ls-remote failed ({exc}) — branches will be empty")
        branches = []

    known = classify_branches(branches)
    branch_roles = auto_assign_roles(branches)

    from src.project_store import slug as project_slug
    repo_info = {
        "name":            repo_name,
        "git_url":         clean_url,
        "org":             org,
        "project":         project_name,
        "project_id":      project_slug(org, project_name),
        "azure_repo_id":   metadata.get("id"),
        "default_branch":  metadata.get("defaultBranch", "").removeprefix("refs/heads/"),
        "web_url":         metadata.get("webUrl"),
        "branches":        known,
        "known_branches":  known["integration"],
        "branch_roles":    branch_roles,
        "size_kb":         metadata.get("size"),
    }
    project_info = extract_project_info(org, project_name, metadata)

    return {"repo": repo_info, "project": project_info}
