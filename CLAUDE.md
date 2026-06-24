# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`code-agent-mcp` is a generic HTTP agent that executes git operations and creates Azure DevOps pull requests on behalf of an orchestrator (`claude-mcp-jira`). It is a Python/Flask service — **not** a domain-specific tool. All domain logic (file generation, Jira interaction) lives in the caller.

The full design is in `arch/integration-plan.md`. Read it before starting any implementation work.

## Environment

```bash
conda activate code-agent-mcp
pytest tests/          # 57 tests
./run_local.sh         # starts server on port 5001, reads .env.local
```

## Related repositories

- `ov-suscripcion-automation`: `/home/idavid/dev/ov/ov-suscripcion-automation` — source infrastructure was copied from
- `claude-mcp-jira`: `/home/idavid/dev/claude/claude-mcp-jira` — caller/orchestrator (future consumer)
- `ov-arizona-backend-ecuador`: `/home/idavid/dev/ov/ov-arizona-backend-ecuador` — primary target git repo; its README defines the canonical branch/PR flow

## Architecture

```
app.py                  — Flask entry point, all HTTP endpoints, Swagger (flasgger /apidocs/)
run_local.sh            — dev launcher: sources .env.local, sets TASKS_DB=/tmp/..., passes all vars to conda run
src/
  auth.py               — X-Agent-Token header validation → 401
  task_store.py         — SQLite: tasks table (async task pattern)
  repo_store.py         — SQLite: repos table
  project_store.py      — SQLite: projects table (slug = {org}/{name})
  branch_config.py      — dynamic branch registry with hot-reload; BRANCH_CONFIG_PATH override
  repo_inspector.py     — parse Azure DevOps URLs, git ls-remote, classify branches
  placer.py             — git: create_feature_branch, git_add_commit_push, create_auxiliary_branch
  azure_client.py       — Azure DevOps REST API v7.1: PR create + status (Flask Blueprint)
  logger.py             — structured logging
apis/                   — curl reference scripts (health, repos, projects, tasks, config, azure)
tests/                  — pytest suite
arch/                   — design and integration plan
```

### Async task pattern

`POST /run` returns 202 immediately with `task_id`. Caller polls `GET /status/<task_id>`. Optional `callback_url` in body triggers a POST on completion. Tasks persisted in SQLite at `TASKS_DB`.

### Authentication

Every endpoint requires `X-Agent-Token` header (value = `TOKEN_AZURE` env var). Missing or wrong → 401. `/health` is the only unauthenticated endpoint.

## API surface

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness (no token) |
| `POST` | `/run` | Enqueue git task → 202 |
| `GET` | `/status/<task_id>` | Poll task result |
| `GET` | `/tasks` | Recent tasks; `?ticket=` filter, `?limit=` |
| `POST` | `/repos` | Register repo + immediate inspection |
| `GET` | `/repos` | List all repos |
| `GET` | `/repos/<name>` | Get repo by name |
| `POST` | `/repos/<name>/refresh` | Re-inspect repo |
| `DELETE` | `/repos/<name>` | Remove from registry |
| `PATCH` | `/repos/<name>/branches/<path:branch>` | Override branch role: `base`/`integration`/`feature`/`other` |
| `GET` | `/projects` | List projects (with repos list) |
| `GET` | `/projects/<org>/<name>` | Get project by slug |
| `GET` | `/config/branches` | Get branch registry |
| `PUT` | `/config/branches` | Update branch registry (hot-reload) |
| `POST` | `/azure/prepare-and-pr` | Ensure aux branch + create aux PR only (idempotent) |
| `POST` | `/azure/pull-requests` | Create feature PR + aux PR simultaneously (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | PR status + CI build status |

## Environment variables

```
TOKEN_AZURE=          # shared secret with claude-mcp-jira
AZURE_PAT=            # Azure DevOps Personal Access Token
AZURE_ORG=            # Azure DevOps organization
AZURE_PROJECT=        # Azure DevOps project (default for PR creation)
GIT_USERNAME=         # git credential username
GIT_PAT=              # git credential PAT
TASKS_DB=/data/tasks.db
PORT=5000
CALLBACK_VERIFY_SSL=true
RETENTION_DAYS=90
BRANCH_CONFIG_PATH=   # optional: path to branch config JSON override
```

## Git flow (from ov-arizona-backend-ecuador README)

Feature branches are cut from `develop` (not `developer`). Auxiliary branches are created from `origin/<target>` with suffix `_{target}_auxiliar`.

- `aux_branch_name(branch, target)` → `{branch}_{target}_auxiliar`
- `create_feature_branch(repo_root, branch, base_branch=None)` — default base from `branch_config.base_branch()` (the entry with `is_base=True`, defaults to `develop`)
- `create_auxiliary_branch(repo_root, feature_branch, target, files, ticket, commit_message)` — checks out from `origin/{target}`, cherry-picks files from the feature branch
- `ensure_auxiliary_branch(repo_root, feature_branch, target, files, ticket, commit_message)` → `(aux_branch, action)` — idempotent: creates if absent, updates if files differ, skips if up to date; always cleans up local temporary branches
- `detect_changed_files(repo_root, feature_branch, base_branch)` → `list[Path]` — returns absolute paths of files changed between `origin/{base_branch}` and `origin/{feature_branch}` via `git diff --name-only`; both branches must be fetched first; raises `RuntimeError` on git failure
- `detect_base_branch(repo_root, feature_branch, candidates)` → `str` — picks the closest ancestor branch from `candidates` using `git merge-base` + `rev-list --count`; base-role branches should appear first in candidates to win ties; falls back to first candidate if merge-base fails for all

## Branch registry defaults and roles

| Branch | Label | Role | Notes |
|---|---|---|---|
| `developer` | desarrollo | `integration` | DEV-UAT |
| `test` | pruebas | `integration` | Preprod |
| `develop` | producción-pre | `base` | **cut features from here** (`is_base=True`) |
| `main` | producción-desplegado | `integration` | production |

### Per-repo branch roles

`inspect()` calls `auto_assign_roles(branches)` to populate `branch_roles` (JSON column in `repos` table):
1. Branch name in global `branch_config` → use its `role` field
2. Starts with `feature/` or `fix/` → `"feature"`
3. In `_KNOWN_INTEGRATION_BRANCHES` set → `"integration"`
4. Otherwise → `"other"`

`GET /repos/<name>` returns `branch_roles` (persisted) plus `branches_by_role` (computed inverse, not persisted).
`PATCH /repos/<name>/branches/<branch>` lets callers override a single branch role without re-inspecting.

**DB migration for existing installs:** `sqlite3 /tmp/code-agent-mcp.db "ALTER TABLE repos ADD COLUMN branch_roles TEXT;"`

## Key constraints

- **This agent never touches code.** It receives already-generated file paths from the caller and moves them between branches. It never generates, modifies, or interprets file contents.
- Do **not** modify `ov-suscripcion-automation` — it continues serving its own domain unchanged.
- `POST /run` accepts JSON (not `multipart/form-data`). The caller provides already-generated file paths.
- This service never talks to Jira — that is exclusively `claude-mcp-jira`'s responsibility.
- Azure DevOps API version: `7.1`.
- `conda run` does **not** inherit parent shell exports — use `run_local.sh` or pass vars explicitly with `env VAR=val conda run ...`.
