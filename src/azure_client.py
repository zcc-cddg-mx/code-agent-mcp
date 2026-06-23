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

Required env vars: AZURE_PAT, AZURE_ORG, AZURE_PROJECT
Optional env var:  AZURE_REPO (default repo name when not provided in body)
"""

from __future__ import annotations

import os
from base64 import b64encode

import requests
from flask import Blueprint, jsonify, request

from src.auth import require_token
from src.logger import log

azure_bp = Blueprint("azure", __name__)

_ORG = os.environ.get("AZURE_ORG", "")
_PROJECT = os.environ.get("AZURE_PROJECT", "")
_PAT = os.environ.get("AZURE_PAT", "")
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
        raise RuntimeError("AZURE_PAT is not configured")
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
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

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
