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

All endpoints require X-Agent-Token header (AGENT_TOKEN env var).
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

from src.auth import require_token
from src.logger import log
from src import task_store
import src.branch_config as branch_config

app = Flask(__name__)
task_store.init_db()

_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))
task_store.cleanup_old_records(days=_RETENTION_DAYS)

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
    return jsonify({"status": "ok", "service": "code-agent-mcp"})


@app.post("/run")
@require_token
def run():
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
    task = task_store.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task)


@app.get("/tasks")
@require_token
def tasks():
    limit = min(int(request.args.get("limit", 50)), 200)
    ticket = request.args.get("ticket") or None
    return jsonify(task_store.get_recent(limit, ticket=ticket))


# ─────────────────────────────────────────────────────────────────────────────
# Branch config endpoints (future UI layer)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/config/branches")
@require_token
def get_branch_config():
    """Return the active branch registry."""
    return jsonify(branch_config.get_registry())


@app.put("/config/branches")
@require_token
def put_branch_config():
    """Replace the branch registry and persist to disk.

    Body: JSON object with branch names as keys. Unknown fields are merged with
    defaults so existing entries not in the body are preserved.
    Each entry supports: label, environment, url, is_base (all optional).
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Body must be a JSON object"}), 400
    branch_config.save(body)
    log("CFG", f"branch config updated: {list(body.keys())}")
    return jsonify(branch_config.get_registry())


# Azure endpoints are defined in src/azure_client.py and registered here
try:
    from src.azure_client import azure_bp
    app.register_blueprint(azure_bp)
except ImportError:
    pass  # azure_client not yet implemented


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
