"""HTTP API for the Code Agent MCP.

Endpoints:

  GET  /health                        — liveness check
  POST /run                           — enqueue a git task (returns 202 immediately)
  GET  /status/<task_id>              — poll task status
  GET  /tasks                         — list recent tasks (last 50, newest first)
  POST /azure/pull-requests           — create feature PR + aux PR in Azure DevOps
  GET  /azure/pull-requests/<pr_id>   — PR status + CI build status

POST /run — JSON body:
  {
    "repo":           "/repos/ov-arizona-backend-ecuador",  # absolute path on container
    "branch":         "feature/ZNRX_67108_renov_agosto",
    "base_branch":    "developer",                          # optional, default: developer
    "files":          ["/path/to/file1", "/path/to/file2"], # already-generated files
    "ticket":         "ZNRX-67108",
    "commit_message": "Migración vencimientos agosto 2026",
    "callback_url":   "https://caller/webhook/done"         # optional
  }

All endpoints require X-Agent-Token header (TOKEN_AZURE env var).
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests as http_requests
from flask import Flask, jsonify, request
from flasgger import Swagger

from src.auth import require_token
from src.logger import log
from src import task_store
from src import repo_store
from src import project_store
import src.pr_store as pr_store
import src.branch_config as branch_config

app = Flask(__name__)
task_store.init_db()
repo_store.init_db()
project_store.init_db()
pr_store.init_db()

Swagger(app, template={
    "swagger": "2.0",
    "info": {
        "title": "Code Agent MCP",
        "description": (
            "Generic HTTP agent for git operations and Azure DevOps PR management.\n\n"
            "**Auth:** incluir header `X-Agent-Token` en cada request "
            "(valor de la variable de entorno `TOKEN_AZURE` del servidor)."
        ),
        "version": "1.0.0",
    },
    "basePath": "/",
    "securityDefinitions": {
        "AgentToken": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Agent-Token",
        }
    },
    "security": [{"AgentToken": []}],
})

_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))
task_store.cleanup_old_records(days=_RETENTION_DAYS)
pr_store.cleanup_old_records(days=_RETENTION_DAYS)

_CALLBACK_RETRIES = 3
_CALLBACK_BACKOFF_BASE = 2  # seconds: 2, 4, 8

_raw_verify = os.environ.get("CALLBACK_VERIFY_SSL", "true").strip()
_CALLBACK_VERIFY: bool | str = (
    False if _raw_verify.lower() == "false"
    else True if _raw_verify.lower() == "true"
    else _raw_verify
)

# ─────────────────────────────────────────────────────────────────────────────
# Concurrency control — one task at a time
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_current_task: dict | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _invert_roles(branch_roles: dict) -> dict:
    """Invert {branch: role} → {role: [branches]} for readable display."""
    result: dict[str, list] = {}
    for branch, role in branch_roles.items():
        result.setdefault(role, []).append(branch)
    return result


def _notify_callback(callback_url: str, task: dict) -> None:
    """POST task result to *callback_url* with up to 3 retries (exponential backoff).

    Never raises — logs and swallows errors after all retries.
    """
    body = {
        "ticket":       task.get("ticket"),
        "status":       "success" if task.get("status") == "done" else "error",
        "task_id":      task.get("task_id"),
        "branch":       task.get("branch"),
        "aux_branch":   task.get("aux_branch"),
        "commit_id":    task.get("commit_id"),
        "repo":         task.get("repo"),
        "build_status": task.get("build_status"),
        "summary":      task.get("summary"),
        "error":        task.get("error"),
        "completed_at": _now_iso(),
    }
    body = {k: v for k, v in body.items() if v is not None}
    log("CB", f"callback payload → {json.dumps(body, ensure_ascii=False)}")

    for attempt in range(1, _CALLBACK_RETRIES + 1):
        try:
            resp = http_requests.post(callback_url, json=body, timeout=10, verify=_CALLBACK_VERIFY)
            log("CB", f"callback → {callback_url} status={resp.status_code} (attempt {attempt})")
            return
        except Exception as exc:
            if attempt < _CALLBACK_RETRIES:
                delay = _CALLBACK_BACKOFF_BASE ** attempt
                log("CB", f"callback attempt {attempt} failed ({exc}) — retrying in {delay}s")
                time.sleep(delay)
            else:
                log("CB", f"callback failed after {_CALLBACK_RETRIES} attempts ({exc})")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness check.
    ---
    tags: [System]
    security: []
    responses:
      200:
        description: Service is up
        schema:
          type: object
          properties:
            status: {type: string, example: ok}
            service: {type: string, example: code-agent-mcp}
    """
    return jsonify({"status": "ok", "service": "code-agent-mcp"})


@app.post("/run")
@require_token
def run():
    """Enqueue a git task (branch + commit + push + aux branch).
    ---
    tags: [Tasks]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [repo, branch, files, ticket, commit_message]
          properties:
            repo:
              type: string
              description: Absolute path to the local repo clone
              example: /repos/ov-arizona-backend-ecuador
            branch:
              type: string
              example: feature/ZNRX_67108_renov_agosto
            base_branch:
              type: string
              description: Branch to cut from (default from branch config)
              example: develop
            target:
              type: string
              description: Integration branch to create aux branch against
              example: developer
            files:
              type: array
              items: {type: string}
              example: [/repos/ov-arizona-backend-ecuador/src/File.java]
            ticket:
              type: string
              example: ZNRX-67108
            commit_message:
              type: string
              example: "Migración vencimientos agosto 2026"
            callback_url:
              type: string
              description: Webhook called when the task completes
              example: https://caller/webhook/done
    responses:
      202:
        description: Task accepted (or rejected if another is running)
        schema:
          type: object
          properties:
            status: {type: string, example: queued}
            task_id: {type: string, example: a1b2c3d4}
      400:
        description: Missing required fields
    """
    body = request.get_json(silent=True) or {}

    missing = [f for f in ("repo", "branch", "files", "ticket", "commit_message") if not body.get(f)]
    if missing:
        return jsonify({"status": "error", "error": f"Missing required field(s): {', '.join(missing)}"}), 400

    files = body["files"]
    if not isinstance(files, list) or not files:
        return jsonify({"status": "error", "error": "'files' must be a non-empty list"}), 400

    task_id = str(uuid.uuid4())[:8]
    now = _now_iso()
    callback_url: str = body.get("callback_url", "")

    if not _lock.acquire(blocking=False):
        active = _current_task or {}
        log("RECV", (
            f"task_id={task_id} ticket={body['ticket']} REJECTED — "
            f"task {active.get('task_id')} ({active.get('ticket')}) already running"
        ))
        task = {
            "task_id": task_id,
            "ticket": body["ticket"],
            "status": "rejected",
            "error": f"Task {active.get('task_id')} ({active.get('ticket')}) is already running",
            "active_task": active,
            "created_at": now,
        }
        task_store.upsert(task, now)
        return jsonify({"status": "rejected", "task_id": task_id, "active_task": active}), 202

    task = {
        "task_id": task_id,
        "ticket": body["ticket"],
        "status": "queued",
        "created_at": now,
    }
    task_store.upsert(task, now)
    log("RECV", f"task_id={task_id} ticket={body['ticket']} ACCEPTED — lock acquired")

    def worker():
        global _current_task
        from src.placer import create_feature_branch, git_add_commit_push, create_auxiliary_branch

        _current_task = {"task_id": task_id, "ticket": body["ticket"], "started_at": _now_iso()}
        task_store.upsert({"task_id": task_id, "status": "running"}, _now_iso())

        try:
            repo_root = Path(body["repo"])
            branch = body["branch"]
            base_branch = body.get("base_branch") or branch_config.base_branch()
            target = body.get("target", "developer")
            file_paths = [Path(f) for f in body["files"]]
            ticket = body["ticket"]
            commit_msg = body["commit_message"]

            create_feature_branch(repo_root, branch, base_branch)
            commit_id = git_add_commit_push(repo_root, file_paths, ticket, commit_msg, branch)
            aux_branch = create_auxiliary_branch(repo_root, branch, target, file_paths, ticket, commit_msg)

            repo_name = repo_root.name
            task_store.upsert({
                "task_id": task_id,
                "status": "done",
                "branch": branch,
                "aux_branch": aux_branch,
                "commit_id": commit_id,
                "repo": repo_name,
                "build_status": "success",
                "summary": f"Branch {branch} pushed, aux branch {aux_branch} pushed",
            }, _now_iso())

        except Exception as exc:
            traceback.print_exc()
            log("ERROR", f"task_id={task_id} failed: {exc}")
            task_store.upsert({
                "task_id": task_id,
                "status": "error",
                "error": str(exc),
            }, _now_iso())
        finally:
            _current_task = None
            _lock.release()
            if callback_url:
                _notify_callback(callback_url, task_store.get(task_id) or {"task_id": task_id, "status": "error"})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"status": "queued", "task_id": task_id}), 202


@app.get("/status/<task_id>")
@require_token
def status(task_id: str):
    """Poll task status.
    ---
    tags: [Tasks]
    parameters:
      - in: path
        name: task_id
        type: string
        required: true
        example: a1b2c3d4
    responses:
      200:
        description: Task record
        schema:
          type: object
          properties:
            task_id: {type: string}
            status: {type: string, enum: [queued, running, done, error, rejected]}
            ticket: {type: string}
            branch: {type: string}
            aux_branch: {type: string}
            commit_id: {type: string}
            error: {type: string}
      404:
        description: Task not found
    """
    task = task_store.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


@app.get("/tasks")
@require_token
def tasks():
    """List recent tasks.
    ---
    tags: [Tasks]
    parameters:
      - in: query
        name: limit
        type: integer
        default: 50
        description: Max number of results (cap 200)
      - in: query
        name: ticket
        type: string
        description: Filter by ticket ID
        example: ZNRX-67108
    responses:
      200:
        description: List of task records (newest first)
        schema:
          type: array
          items: {type: object}
    """
    limit = min(int(request.args.get("limit", 50)), 200)
    ticket = request.args.get("ticket") or None
    return jsonify(task_store.get_recent(limit, ticket=ticket))


# ─────────────────────────────────────────────────────────────────────────────
# Branch config endpoints (future UI layer)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/config/branches")
@require_token
def get_branch_config():
    """Get the active branch registry.
    ---
    tags: [Configuration]
    responses:
      200:
        description: Branch registry keyed by branch name
        schema:
          type: object
          example:
            developer: {label: desarrollo, environment: DEV-UAT}
            develop:   {label: "producción-pre", is_base: true}
    """
    return jsonify(branch_config.get_registry())


@app.put("/config/branches")
@require_token
def put_branch_config():
    """Update the branch registry (merged with defaults, persisted to disk).
    ---
    tags: [Configuration]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          description: >
            Keys are branch names. Each entry may include: label, environment,
            url, is_base. Unknown keys are merged with defaults.
          example:
            developer: {label: desarrollo, environment: DEV-UAT}
            test:      {label: pruebas, environment: Preprod}
    responses:
      200:
        description: Full updated registry
        schema: {type: object}
      400:
        description: Body is not a JSON object
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Body must be a JSON object"}), 400
    branch_config.save(body)
    log("CFG", f"branch config updated: {list(body.keys())}")
    return jsonify(branch_config.get_registry())


# ─────────────────────────────────────────────────────────────────────────────
# Repository registry endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/repos")
@require_token
def register_repo():
    """Register a new repository and inspect it via Azure DevOps API + git ls-remote.
    ---
    tags: [Repositories]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [git_url]
          properties:
            git_url:
              type: string
              example: "https://ZurichInsurance-EC@dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/ov-arizona-restat"
    responses:
      201:
        description: Repository and project registered
        schema:
          type: object
          properties:
            repo:    {type: object}
            project: {type: object}
      400:
        description: Missing or invalid git_url
      409:
        description: Repository already registered
      502:
        description: Azure API or git ls-remote call failed
    """
    from src.repo_inspector import inspect, parse_azure_url
    import uuid as _uuid

    body = request.get_json(silent=True) or {}
    git_url = (body.get("git_url") or "").strip()
    if not git_url:
        return jsonify({"error": "git_url is required"}), 400

    pat = os.environ.get("AZURE_PAT", "")
    if not pat:
        return jsonify({"error": "AZURE_PAT not configured on server"}), 500

    # Parse URL first to get the canonical name
    try:
        parsed = parse_azure_url(git_url)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    repo_name = parsed["repo"]

    # Reject duplicates
    existing = repo_store.get_by_name(repo_name)
    if existing:
        return jsonify({"error": f"Repository '{repo_name}' already registered", "repo": existing}), 409

    now = _now_iso()
    try:
        result = inspect(git_url, pat)
    except Exception as exc:
        log("REPO", f"inspection failed for {repo_name}: {exc}")
        return jsonify({"error": f"Inspection failed: {exc}"}), 502

    # Upsert project (idempotent — same project may already exist from another repo)
    project_store.upsert(result["project"], now)
    log("REPO", f"project '{result['project']['project_id']}' registered/updated")

    repo_id = str(_uuid.uuid4())[:8]
    record = {"repo_id": repo_id, "last_inspected_at": now, "created_at": now, **result["repo"]}
    repo_store.upsert(record, now)
    log("REPO", f"registered '{repo_name}' (id={repo_id})")
    return jsonify({
        "repo":    repo_store.get(repo_id),
        "project": project_store.get(result["project"]["project_id"]),
    }), 201


@app.get("/repos")
@require_token
def list_repos():
    """List all registered repositories.
    ---
    tags: [Repositories]
    responses:
      200:
        description: Array of repository records
        schema:
          type: array
          items: {type: object}
    """
    return jsonify(repo_store.list_all())


@app.get("/repos/<repo_name>")
@require_token
def get_repo(repo_name: str):
    """Get a registered repository by name.
    ---
    tags: [Repositories]
    parameters:
      - in: path
        name: repo_name
        type: string
        required: true
        example: ov-arizona-restat
    responses:
      200:
        description: Repository record
        schema: {type: object}
      404:
        description: Not found
    """
    repo = repo_store.get_by_name(repo_name)
    if not repo:
        return jsonify({"error": f"Repository '{repo_name}' not found"}), 404
    repo["branches_by_role"] = _invert_roles(repo.get("branch_roles") or {})
    return jsonify(repo)


@app.patch("/repos/<repo_name>/branches/<path:branch_name>")
@require_token
def set_branch_role(repo_name: str, branch_name: str):
    """Set the logical role of a branch for a specific repository.
    ---
    tags: [Repositories]
    parameters:
      - in: path
        name: repo_name
        type: string
        required: true
        example: ensurance-old-web
      - in: path
        name: branch_name
        type: string
        required: true
        example: master
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [role]
          properties:
            role:
              type: string
              enum: [base, integration, feature, other]
              example: integration
    responses:
      200:
        description: Updated branch roles for the repo
        schema: {type: object}
      400:
        description: Invalid role value
      404:
        description: Repository not found
    """
    _VALID_ROLES = {"base", "integration", "feature", "other"}
    body = request.get_json(silent=True) or {}
    role = (body.get("role") or "").strip()
    if role not in _VALID_ROLES:
        return jsonify({"error": f"role must be one of: {', '.join(sorted(_VALID_ROLES))}"}), 400

    repo = repo_store.get_by_name(repo_name)
    if not repo:
        return jsonify({"error": f"Repository '{repo_name}' not found"}), 404

    now = _now_iso()
    repo_store.set_branch_role(repo["repo_id"], branch_name, role, now)
    log("REPO", f"'{repo_name}' branch '{branch_name}' role set to '{role}'")

    updated = repo_store.get(repo["repo_id"])
    updated["branches_by_role"] = _invert_roles(updated.get("branch_roles") or {})
    return jsonify(updated)


@app.post("/repos/<repo_name>/refresh")
@require_token
def refresh_repo(repo_name: str):
    """Re-inspect a registered repository and update its record.
    ---
    tags: [Repositories]
    parameters:
      - in: path
        name: repo_name
        type: string
        required: true
        example: ov-arizona-restat
    responses:
      200:
        description: Updated repo and project records
        schema:
          type: object
          properties:
            repo:    {type: object}
            project: {type: object}
      404:
        description: Repository not registered
      502:
        description: Inspection failed
    """
    from src.repo_inspector import inspect

    repo = repo_store.get_by_name(repo_name)
    if not repo:
        return jsonify({"error": f"Repository '{repo_name}' not found"}), 404

    pat = os.environ.get("AZURE_PAT", "")
    if not pat:
        return jsonify({"error": "AZURE_PAT not configured on server"}), 500

    now = _now_iso()
    try:
        result = inspect(repo["git_url"], pat)
    except Exception as exc:
        log("REPO", f"refresh failed for {repo_name}: {exc}")
        return jsonify({"error": f"Inspection failed: {exc}"}), 502

    project_store.upsert(result["project"], now)
    record = {"repo_id": repo["repo_id"], "last_inspected_at": now, **result["repo"]}
    repo_store.upsert(record, now)
    log("REPO", f"refreshed '{repo_name}'")
    return jsonify({
        "repo":    repo_store.get(repo["repo_id"]),
        "project": project_store.get(result["project"]["project_id"]),
    })


@app.delete("/repos/<repo_name>")
@require_token
def delete_repo(repo_name: str):
    """Remove a repository from the registry.
    ---
    tags: [Repositories]
    parameters:
      - in: path
        name: repo_name
        type: string
        required: true
        example: ov-arizona-restat
    responses:
      200:
        description: Deleted
        schema:
          type: object
          properties:
            deleted: {type: string, example: ov-arizona-restat}
      404:
        description: Not found
    """
    repo = repo_store.get_by_name(repo_name)
    if not repo:
        return jsonify({"error": f"Repository '{repo_name}' not found"}), 404
    repo_store.delete(repo["repo_id"])
    log("REPO", f"deleted '{repo_name}'")
    return jsonify({"deleted": repo_name})


# ─────────────────────────────────────────────────────────────────────────────
# Project registry endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/projects")
@require_token
def list_projects():
    """List all registered projects (each with its repos).
    ---
    tags: [Projects]
    responses:
      200:
        description: Array of project records
        schema:
          type: array
          items:
            type: object
            properties:
              project_id: {type: string, example: "ZurichInsurance-EC/Oficina-Virtual-ZEC"}
              org:        {type: string, example: ZurichInsurance-EC}
              name:       {type: string, example: Oficina-Virtual-ZEC}
              repos:
                type: array
                items: {type: string}
    """
    projects = project_store.list_all()
    # Enrich each project with its repo list
    all_repos = repo_store.list_all()
    repos_by_project: dict[str, list] = {}
    for r in all_repos:
        pid = r.get("project_id")
        if pid:
            repos_by_project.setdefault(pid, []).append(r["name"])
    for p in projects:
        p["repos"] = repos_by_project.get(p["project_id"], [])
    return jsonify(projects)


@app.get("/projects/<path:project_id>")
@require_token
def get_project(project_id: str):
    """Get a project by its slug ({org}/{name}).
    ---
    tags: [Projects]
    parameters:
      - in: path
        name: project_id
        type: string
        required: true
        description: "{org}/{project_name}"
        example: ZurichInsurance-EC/Oficina-Virtual-ZEC
    responses:
      200:
        description: Project record with repos list
        schema: {type: object}
      404:
        description: Not found
    """
    project = project_store.get(project_id)
    if not project:
        return jsonify({"error": f"Project '{project_id}' not found"}), 404
    repos = [r["name"] for r in repo_store.list_all() if r.get("project_id") == project_id]
    project["repos"] = repos
    return jsonify(project)


# Azure endpoints are defined in src/azure_client.py and registered here
try:
    from src.azure_client import azure_bp
    app.register_blueprint(azure_bp)
except ImportError:
    pass  # azure_client not yet implemented


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
