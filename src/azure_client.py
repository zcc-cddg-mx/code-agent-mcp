"""Azure DevOps REST API client — PR creation and status.

Endpoints registered as a Flask Blueprint:

  POST /azure/pull-requests
    Body:
      branch, aux_branch, title, description, repo, target (default: developer)
    Creates feature PR and auxiliary PR simultaneously.
    Returns: {feature_pr: {pr_id, pr_url}, aux_pr: {pr_id, pr_url}}

  GET /azure/pull-requests/<pr_id>
    Returns: {pr_id, status, build_status, pr_url}
    status:       active | completed | abandoned
    build_status: pending | succeeded | failed | unknown

Required env vars: TOKEN_AZURE, AZURE_ORG, AZURE_PROJECT
Optional env var:  AZURE_REPO (default repo name when not provided in body)
"""

from __future__ import annotations

import os
import time
from base64 import b64encode

import requests
from flask import Blueprint, jsonify, request

from src.auth import require_token
from src.logger import log
import src.pr_store as _prs

azure_bp = Blueprint("azure", __name__)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


_ORG = os.environ.get("AZURE_ORG", "")
_PROJECT = os.environ.get("AZURE_PROJECT", "")
_PAT = os.environ.get("TOKEN_AZURE", "")
_API_VERSION = "7.1"

_VERIFY_SSL = os.environ.get("AZURE_VERIFY_SSL", "true").strip().lower() != "false"


def _auth_header() -> dict[str, str]:
    token = b64encode(f":{_PAT}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _pr_url(org: str, project: str, repo: str, pr_id: int) -> str:
    return f"https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{pr_id}"


def _create_pr(repo: str, source_branch: str, target_branch: str, title: str, description: str) -> dict:
    """Call Azure DevOps REST API to create a single PR. Returns {pr_id, pr_url}."""
    if not _PAT:
        raise RuntimeError("TOKEN_AZURE is not configured")
    if not _ORG or not _PROJECT:
        raise RuntimeError("AZURE_ORG and AZURE_PROJECT must be configured")

    url = (
        f"https://dev.azure.com/{_ORG}/{_PROJECT}/_apis/git/repositories"
        f"/{repo}/pullrequests?api-version={_API_VERSION}"
    )
    body = {
        "sourceRefName": f"refs/heads/{source_branch}",
        "targetRefName": f"refs/heads/{target_branch}",
        "title": title,
        "description": description,
    }
    log("AZURE", f"POST PR {source_branch} → {target_branch} in {repo}")
    resp = requests.post(url, json=body, headers=_auth_header(), verify=_VERIFY_SSL, timeout=30)

    if not resp.ok:
        raise RuntimeError(
            f"Azure DevOps PR creation failed: {resp.status_code} {resp.text[:300]}"
        )

    data = resp.json()
    pr_id = data["pullRequestId"]
    pr_url = _pr_url(_ORG, _PROJECT, repo, pr_id)
    log("AZURE", f"PR created: {pr_url}")
    return {"pr_id": pr_id, "pr_url": pr_url}


def _get_pr(repo: str, pr_id: int) -> dict:
    """Fetch PR details from Azure DevOps. Returns raw JSON."""
    url = (
        f"https://dev.azure.com/{_ORG}/{_PROJECT}/_apis/git/repositories"
        f"/{repo}/pullrequests/{pr_id}?api-version={_API_VERSION}"
    )
    resp = requests.get(url, headers=_auth_header(), verify=_VERIFY_SSL, timeout=15)
    if resp.status_code == 404:
        return {}
    if not resp.ok:
        raise RuntimeError(f"Azure DevOps PR fetch failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def _get_build_status(repo: str, pr_id: int) -> str:
    """Query the latest build status for a PR via the statuses API."""
    url = (
        f"https://dev.azure.com/{_ORG}/{_PROJECT}/_apis/git/repositories"
        f"/{repo}/pullrequests/{pr_id}/statuses?api-version={_API_VERSION}"
    )
    resp = requests.get(url, headers=_auth_header(), verify=_VERIFY_SSL, timeout=15)
    if not resp.ok:
        return "unknown"
    statuses = resp.json().get("value", [])
    if not statuses:
        return "pending"
    # Most recent status first (API returns chronological — take last)
    latest = statuses[-1]
    state = latest.get("state", "").lower()
    mapping = {
        "succeeded": "succeeded",
        "failed": "failed",
        "error": "failed",
        "pending": "pending",
        "notapplicable": "unknown",
        "notset": "unknown",
    }
    return mapping.get(state, "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

import subprocess as _sp
from pathlib import Path as _Path
from src.placer import detect_changed_files, detect_base_branch
import src.branch_config as _bc
import src.repo_store as _rs


def _check_repo_registered(repo_name: str):
    """Return (repo_record, None) if registered, or (None, error_response) if not."""
    record = _rs.get_by_name(repo_name)
    if not record:
        return None, (
            jsonify({"error": f"Repository '{repo_name}' is not registered. "
                              "Register it first with POST /repos."}),
            403,
        )
    return record, None


def _resolve_repo_path(body: dict, repo_record: dict) -> _Path:
    """Return repo_path from request body, falling back to local_path in registry."""
    raw = (body.get("repo_path") or "").strip()
    if raw:
        return _Path(raw)
    local = repo_record.get("local_path")
    if local:
        return _Path(local)
    raise ValueError(
        "repo_path is required (or register the repo with local_path via POST /repos)"
    )


def _resolve_base_and_files(
    body: dict,
    repo_path: _Path,
    repo: str,
    branch: str,
) -> tuple[list[_Path], list[str], str]:
    """Resolve (file_paths, files_detected, base_branch) from request body.

    If body['files'] is provided, uses them directly.
    Otherwise fetches branches and auto-detects via detect_base_branch +
    detect_changed_files.

    Raises ValueError (→ 400) or RuntimeError (→ 502) on failure.
    """
    from src.placer import detect_changed_files, detect_base_branch

    raw_files = body.get("files")
    if raw_files is not None:
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("'files' must be a non-empty list")
        file_paths = [_Path(f) for f in raw_files]
        files_detected = [str(f) for f in file_paths]
        base_branch: str = body.get("base_branch") or _bc.base_branch()
        return file_paths, files_detected, base_branch

    r = str(repo_path.resolve())
    try:
        _sp.run(["git", "-C", r, "fetch", "origin", branch], check=True,
                capture_output=True, text=True)
    except _sp.CalledProcessError as exc:
        raise RuntimeError(f"git fetch failed: {exc.stderr.strip()}")

    if body.get("base_branch"):
        base_branch = body["base_branch"]
        candidates = [base_branch]
    else:
        repo_record = _rs.get_by_name(repo)
        if repo_record and repo_record.get("branch_roles"):
            roles = repo_record["branch_roles"]
            base_candidates = [b for b, role in roles.items() if role == "base"]
            integ_candidates = [b for b, role in roles.items() if role == "integration"]
            candidates = base_candidates + integ_candidates or [_bc.base_branch()]
        else:
            candidates = [_bc.base_branch()]

        for c in candidates:
            _sp.run(["git", "-C", r, "fetch", "origin", c],
                    capture_output=True, text=True)

        base_branch = detect_base_branch(repo_path, branch, candidates)

    try:
        _sp.run(["git", "-C", r, "fetch", "origin", base_branch], check=True,
                capture_output=True, text=True)
    except _sp.CalledProcessError as exc:
        raise RuntimeError(f"git fetch failed: {exc.stderr.strip()}")

    try:
        file_paths = detect_changed_files(repo_path, branch, base_branch)
    except RuntimeError:
        raise

    if not file_paths:
        raise ValueError(
            f"No changed files detected between origin/{base_branch}...origin/{branch}"
        )

    files_detected = [str(f) for f in file_paths]
    log("AZURE", f"base_branch='{base_branch}', auto-detected {len(file_paths)} file(s)")
    return file_paths, files_detected, base_branch


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _find_existing_pr(repo: str, source_branch: str, target_branch: str) -> dict | None:
    """Return {pr_id, pr_url} of the first active PR for source→target, or None."""
    if not _PAT or not _ORG or not _PROJECT:
        return None
    url = (
        f"https://dev.azure.com/{_ORG}/{_PROJECT}/_apis/git/repositories"
        f"/{repo}/pullrequests"
    )
    params = {
        "searchCriteria.sourceRefName": f"refs/heads/{source_branch}",
        "searchCriteria.targetRefName": f"refs/heads/{target_branch}",
        "searchCriteria.status": "active",
        "api-version": _API_VERSION,
    }
    resp = requests.get(url, params=params, headers=_auth_header(), verify=_VERIFY_SSL, timeout=15)
    if not resp.ok:
        return None
    items = resp.json().get("value", [])
    if not items:
        return None
    pr = items[0]
    pr_id = pr["pullRequestId"]
    return {"pr_id": pr_id, "pr_url": _pr_url(_ORG, _PROJECT, repo, pr_id)}


@azure_bp.post("/azure/prepare-and-pr/preview")
@require_token
def prepare_and_pr_preview():
    """Dry-run: detect base branch and changed files without creating anything.
    ---
    tags: [Azure DevOps]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [repo, repo_path, branch, target]
          properties:
            repo:
              type: string
              example: ov-arizona-frontend-ecuador
            repo_path:
              type: string
              example: /home/idavid/dev/ov/ov-arizona-frontend-ecuador
            branch:
              type: string
              example: feature/test_mcp_jira_multifile
            target:
              type: string
              example: test
            files:
              type: array
              items: {type: string}
              description: Explicit file list. If omitted, auto-detected via git diff.
            base_branch:
              type: string
              description: Base branch for diff. If omitted, auto-detected via merge-base.
    responses:
      200:
        description: Preview of what prepare-and-pr would do
        schema:
          type: object
          properties:
            branch:         {type: string}
            target:         {type: string}
            base_branch:    {type: string}
            aux_branch:     {type: string}
            files_detected: {type: array, items: {type: string}}
            existing_pr:
              type: object
              nullable: true
              properties:
                pr_id:  {type: integer}
                pr_url: {type: string}
      400:
        description: Missing required fields or no changed files detected
      502:
        description: Git fetch failed
    """
    from src.placer import aux_branch_name

    body = request.get_json(silent=True) or {}
    required = ("repo", "branch", "target")
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    repo: str = body["repo"]
    repo_record, err = _check_repo_registered(repo)
    if err:
        return err

    try:
        repo_path = _resolve_repo_path(body, repo_record)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    branch: str = body["branch"]
    target: str = body["target"]
    real_target: str = _bc.resolve_target_branch(target, repo_record.get("branch_map"))

    try:
        file_paths, files_detected, base_branch = _resolve_base_and_files(
            body, repo_path, repo, branch
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    aux_branch = aux_branch_name(branch, real_target)
    existing_pr = _find_existing_pr(repo, aux_branch, real_target)

    return jsonify({
        "branch":         branch,
        "target":         target,
        "real_target":    real_target,
        "base_branch":    base_branch,
        "aux_branch":     aux_branch,
        "files_detected": files_detected,
        "existing_pr":    existing_pr,
    }), 200


@azure_bp.post("/azure/prepare-and-pr")
@require_token
def prepare_and_pr():
    """Ensure aux branch exists/is up-to-date, then create (or return existing) aux PR.
    ---
    tags: [Azure DevOps]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [repo, repo_path, branch, target, ticket, title]
          properties:
            repo:
              type: string
              description: Azure DevOps repository name
              example: ov-arizona-backend-ecuador
            repo_path:
              type: string
              description: Absolute local path to the git clone
              example: /home/idavid/dev/ov/ov-arizona-backend-ecuador
            branch:
              type: string
              description: Feature branch (source of files)
              example: feature/test_mcp_server
            files:
              type: array
              items: {type: string}
              description: Absolute paths of files to integrate. If omitted, auto-detected via git diff.
              example: [/home/idavid/dev/ov/ov-arizona-backend-ecuador/README.md]
            base_branch:
              type: string
              description: Base branch for auto-detecting changed files (default from branch config)
              example: develop
            target:
              type: string
              description: Integration branch (aux PR target)
              example: test
            ticket:
              type: string
              example: ZNRX-12345
            title:
              type: string
              example: "ZNRX-12345 Renovaciones junio → test"
            description:
              type: string
              example: "Generado automáticamente por code-agent-mcp"
    responses:
      200:
        description: PR already existed
      201:
        description: Aux branch created/updated and PR created
        schema:
          type: object
          properties:
            aux_branch:     {type: string}
            action:         {type: string, enum: [created, updated, unchanged]}
            base_branch:    {type: string}
            files_detected: {type: array, items: {type: string}}
            pr:
              type: object
              properties:
                pr_id:  {type: integer}
                pr_url: {type: string}
      400:
        description: Missing required fields or no changed files detected
      502:
        description: Git or Azure DevOps error
    """
    from src.placer import ensure_auxiliary_branch

    body = request.get_json(silent=True) or {}
    required = ("repo", "branch", "target", "ticket", "title")
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    repo: str = body["repo"]
    repo_record, err = _check_repo_registered(repo)
    if err:
        return err

    try:
        repo_path = _resolve_repo_path(body, repo_record)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    branch: str = body["branch"]
    target: str = body["target"]
    real_target: str = _bc.resolve_target_branch(target, repo_record.get("branch_map"))
    ticket: str = body["ticket"]
    title: str = body["title"]
    description: str = body.get("description", "")

    try:
        file_paths, files_detected, base_branch = _resolve_base_and_files(
            body, repo_path, repo, branch
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    try:
        aux_branch, action = ensure_auxiliary_branch(
            repo_path, branch, real_target, file_paths, ticket, title
        )
    except Exception as exc:
        log("AZURE", f"ensure_auxiliary_branch error: {exc}")
        return jsonify({"error": str(exc)}), 502

    existing_pr = _find_existing_pr(repo, aux_branch, real_target)
    if existing_pr:
        log("AZURE", f"PR already exists for {aux_branch} → {real_target}: {existing_pr['pr_id']}")
        _prs.upsert({
            "pr_id": existing_pr["pr_id"], "pr_url": existing_pr["pr_url"],
            "repo": repo, "source_branch": aux_branch, "target_branch": real_target,
            "title": title, "status": "active",
            "task_id": body.get("task_id"),
        }, _now_iso())
        return jsonify({
            "aux_branch":  aux_branch,
            "action":      action,
            "target":      target,
            "real_target": real_target,
            "base_branch": base_branch,
            "files_detected": files_detected,
            "pr":          existing_pr,
        }), 200

    try:
        pr = _create_pr(repo, aux_branch, real_target, title, description)
    except RuntimeError as exc:
        log("AZURE", f"PR creation error: {exc}")
        return jsonify({"error": str(exc)}), 502

    _prs.upsert({
        "pr_id": pr["pr_id"], "pr_url": pr["pr_url"],
        "repo": repo, "source_branch": aux_branch, "target_branch": real_target,
        "title": title, "status": "active",
        "task_id": body.get("task_id"),
    }, _now_iso())

    return jsonify({
        "aux_branch":  aux_branch,
        "action":      action,
        "target":      target,
        "real_target": real_target,
        "base_branch": base_branch,
        "files_detected": files_detected,
        "pr":          pr,
    }), 201


@azure_bp.post("/azure/pull-requests")
@require_token
def create_pull_requests():
    """Create feature PR and auxiliary PR simultaneously in Azure DevOps.
    ---
    tags: [Azure DevOps]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [branch, aux_branch, title, repo]
          properties:
            repo:
              type: string
              description: Azure DevOps repository name
              example: ov-arizona-backend-ecuador
            branch:
              type: string
              description: Feature branch (source of feature PR)
              example: feature/ZNRX_67108_renov_agosto
            aux_branch:
              type: string
              description: Auxiliary branch (source of aux PR)
              example: feature/ZNRX_67108_renov_agosto_developer_auxiliar
            title:
              type: string
              example: "ZNRX-67108 Migración vencimientos agosto 2026"
            description:
              type: string
              example: "Generado automáticamente por code-agent-mcp"
            target:
              type: string
              description: Target integration branch
              default: developer
              example: developer
    responses:
      201:
        description: Both PRs created
        schema:
          type: object
          properties:
            feature_pr:
              type: object
              properties:
                pr_id:  {type: integer}
                pr_url: {type: string}
            aux_pr:
              type: object
              properties:
                pr_id:  {type: integer}
                pr_url: {type: string}
      400:
        description: Missing required fields
      502:
        description: Azure DevOps API error
    """
    body = request.get_json(silent=True) or {}

    missing = [f for f in ("branch", "aux_branch", "title", "repo") if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    repo: str = body["repo"]
    branch: str = body["branch"]
    aux_branch: str = body["aux_branch"]
    title: str = body["title"]
    description: str = body.get("description", "")
    target: str = body.get("target", "developer")

    try:
        feature_pr = _create_pr(repo, branch, target, title, description)
        aux_pr = _create_pr(
            repo, aux_branch, target,
            f"{title} [auxiliar]",
            description,
        )
    except RuntimeError as exc:
        log("AZURE", f"PR creation error: {exc}")
        return jsonify({"error": str(exc)}), 502

    now = _now_iso()
    _prs.upsert({
        "pr_id": feature_pr["pr_id"], "pr_url": feature_pr["pr_url"],
        "repo": repo, "source_branch": branch, "target_branch": target,
        "title": title, "status": "active",
    }, now)
    _prs.upsert({
        "pr_id": aux_pr["pr_id"], "pr_url": aux_pr["pr_url"],
        "repo": repo, "source_branch": aux_branch, "target_branch": target,
        "title": f"{title} [auxiliar]", "status": "active",
    }, now)

    return jsonify({"feature_pr": feature_pr, "aux_pr": aux_pr}), 201


@azure_bp.get("/azure/pull-requests/<int:pr_id>")
@require_token
def get_pull_request(pr_id: int):
    """Get PR status and CI build status.
    ---
    tags: [Azure DevOps]
    parameters:
      - in: path
        name: pr_id
        type: integer
        required: true
        example: 2505
      - in: query
        name: repo
        type: string
        required: true
        description: Azure DevOps repository name (or set AZURE_REPO env var)
        example: ov-arizona-backend-ecuador
    responses:
      200:
        description: PR status
        schema:
          type: object
          properties:
            pr_id:        {type: integer, example: 2505}
            status:       {type: string, enum: [active, completed, abandoned], example: active}
            build_status: {type: string, enum: [pending, succeeded, failed, unknown], example: succeeded}
            pr_url:       {type: string}
      400:
        description: repo param missing
      404:
        description: PR not found
      502:
        description: Azure DevOps API error
    """
    repo = request.args.get("repo", os.environ.get("AZURE_REPO", ""))
    if not repo:
        return jsonify({"error": "repo query param required (or set AZURE_REPO env var)"}), 400

    try:
        data = _get_pr(repo, pr_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    if not data:
        return jsonify({"error": "PR not found"}), 404

    status = data.get("status", "unknown")
    pr_url = _pr_url(_ORG, _PROJECT, repo, pr_id)
    build_status = _get_build_status(repo, pr_id)

    return jsonify({
        "pr_id": pr_id,
        "status": status,
        "build_status": build_status,
        "pr_url": pr_url,
    })


@azure_bp.patch("/azure/pull-requests/<int:pr_id>")
@require_token
def update_pull_request(pr_id: int):
    """Complete, abandon, or reactivate a pull request.
    ---
    tags: [Azure DevOps]
    parameters:
      - in: path
        name: pr_id
        type: integer
        required: true
        example: 2560
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [repo, status]
          properties:
            repo:
              type: string
              description: Azure DevOps repository name
              example: ov-arizona-frontend-ecuador
            status:
              type: string
              enum: [completed, abandoned, active]
              description: >
                completed — merge and close the PR;
                abandoned — close without merging;
                active    — reactivate an abandoned PR
              example: completed
    responses:
      200:
        description: PR updated
        schema:
          type: object
          properties:
            pr_id:   {type: integer}
            status:  {type: string}
            pr_url:  {type: string}
      400:
        description: Missing or invalid fields
      404:
        description: PR not found
      502:
        description: Azure DevOps API error
    """
    body = request.get_json(silent=True) or {}
    repo = body.get("repo") or request.args.get("repo", os.environ.get("AZURE_REPO", ""))
    status_req = body.get("status", "").lower()

    if not repo:
        return jsonify({"error": "repo is required (body or query param)"}), 400

    valid_statuses = ("completed", "abandoned", "active")
    if status_req not in valid_statuses:
        return jsonify({"error": f"status must be one of: {', '.join(valid_statuses)}"}), 400

    # Fetch current PR to get lastMergeSourceCommit (required for completion)
    try:
        pr_data = _get_pr(repo, pr_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    if not pr_data:
        return jsonify({"error": "PR not found"}), 404

    patch_body: dict = {"status": status_req}
    if status_req == "completed":
        last_commit = (pr_data.get("lastMergeSourceCommit") or {}).get("commitId")
        if last_commit:
            patch_body["lastMergeSourceCommit"] = {"commitId": last_commit}

    url = (
        f"https://dev.azure.com/{_ORG}/{_PROJECT}/_apis/git/repositories"
        f"/{repo}/pullrequests/{pr_id}?api-version={_API_VERSION}"
    )
    log("AZURE", f"PATCH PR #{pr_id} → status={status_req}")
    resp = requests.patch(url, json=patch_body, headers=_auth_header(),
                          verify=_VERIFY_SSL, timeout=30)
    if not resp.ok:
        return jsonify({"error": f"Azure DevOps error: {resp.status_code} {resp.text[:300]}"}), 502

    updated = resp.json()
    final_status = updated.get("status", status_req)
    _prs.update_status(pr_id, final_status, _now_iso())
    return jsonify({
        "pr_id":  pr_id,
        "status": final_status,
        "pr_url": _pr_url(_ORG, _PROJECT, repo, pr_id),
    }), 200


@azure_bp.get("/prs")
@require_token
def list_prs():
    """List pull requests stored in the local registry.
    ---
    tags: [Pull Requests]
    parameters:
      - in: query
        name: repo
        type: string
        description: Filter by repository name
      - in: query
        name: status
        type: string
        description: Filter by status (active|completed|abandoned)
      - in: query
        name: task_id
        type: string
        description: Filter by originating task_id
      - in: query
        name: limit
        type: integer
        default: 50
    responses:
      200:
        description: List of PR records
    """
    repo = request.args.get("repo") or None
    status = request.args.get("status") or None
    task_id = request.args.get("task_id") or None
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    return jsonify(_prs.list_all(repo=repo, status=status, task_id=task_id, limit=limit))


@azure_bp.get("/prs/<int:pr_id>")
@require_token
def get_pr_record(pr_id: int):
    """Get a stored PR record, refreshing its status from Azure DevOps.
    ---
    tags: [Pull Requests]
    parameters:
      - in: path
        name: pr_id
        type: integer
        required: true
        example: 2560
      - in: query
        name: repo
        type: string
        description: Repository name (required if not already stored)
    responses:
      200:
        description: PR record with refreshed status
      404:
        description: PR not found in registry
      502:
        description: Azure DevOps API error
    """
    record = _prs.get(pr_id)
    repo = (record or {}).get("repo") or request.args.get("repo", os.environ.get("AZURE_REPO", ""))

    if not repo:
        return jsonify({"error": "repo is required (query param or register the PR first)"}), 400

    try:
        pr_data = _get_pr(repo, pr_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    if not pr_data:
        if not record:
            return jsonify({"error": "PR not found"}), 404
        return jsonify(record)

    live_status = pr_data.get("status", "active")
    if record and record.get("status") != live_status:
        _prs.update_status(pr_id, live_status, _now_iso())

    if record:
        record["status"] = live_status
        return jsonify(record)

    # PR not yet in registry — return live data without persisting
    return jsonify({
        "pr_id":        pr_id,
        "pr_url":       _pr_url(_ORG, _PROJECT, repo, pr_id),
        "repo":         repo,
        "source_branch": pr_data.get("sourceRefName", "").removeprefix("refs/heads/"),
        "target_branch": pr_data.get("targetRefName", "").removeprefix("refs/heads/"),
        "title":        pr_data.get("title"),
        "status":       live_status,
    })
