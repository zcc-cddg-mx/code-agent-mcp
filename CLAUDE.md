# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`code-agent-mcp` is a generic HTTP agent that executes git operations and creates Azure DevOps pull requests on behalf of an orchestrator (`claude-mcp-jira`). It is a Python/Flask service — **not** a domain-specific tool. All domain logic (file generation, Jira interaction) lives in the caller.

The full design is in `arch/integration-plan.md`. Read it before starting any implementation work.

## Environment

```bash
conda activate code-agent-mcp
pytest tests/
python app.py
```

## Related repositories

- `ov-suscripcion-automation` (code-agent): `/home/idavid/dev/ov/ov-suscripcion-automation` — source to copy infrastructure from
- `claude-mcp-jira`: `/home/idavid/dev/claude/claude-mcp-jira` — caller/orchestrator that will consume this service
- `ov-arizona-backend-ecuador`: `/home/idavid/dev/ov/ov-arizona-backend-ecuador` — git repo this agent operates on

## Architecture

### Service structure (target)

```
app.py                  — Flask entry point; all HTTP endpoints
src/
  task_store.py         — SQLite task persistence (async task pattern)
  logger.py             — structured logging
  placer.py             — generic git: branch, commit, push, aux branch
  azure_client.py       — Azure DevOps REST API (PR create + status)
  auth.py               — X-Agent-Token header validation → 401 if missing/wrong
Dockerfile
.env.example
tests/
```

### Async task pattern

`POST /run` returns `202` immediately with a `task_id`. The caller polls `GET /status/<task_id>`. An optional `callback_url` in the request body triggers a POST when the task completes. Tasks are persisted in SQLite at `TASKS_DB`.

### Authentication

Every endpoint requires `X-Agent-Token` header. Value is set via `AGENT_TOKEN` env var. Missing or wrong token → 401. No other auth mechanism.

## API surface

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness |
| `POST` | `/run` | JSON body: repo, branch, files, commit message, optional `callback_url` |
| `GET` | `/status/<task_id>` | Poll for task result |
| `GET` | `/tasks` | Recent tasks; `?ticket=ZNRX-123` filter |
| `POST` | `/azure/pull-requests` | Create feature PR + aux PR simultaneously |
| `GET` | `/azure/pull-requests/<pr_id>` | PR status + CI build status |

`POST /azure/pull-requests` creates **two** PRs per call (feature branch → developer, aux branch → developer). See `arch/integration-plan.md` for full request/response contracts.

## Environment variables

```
AGENT_TOKEN=          # shared secret with claude-mcp-jira
AZURE_PAT=            # Azure DevOps Personal Access Token
AZURE_ORG=            # Azure DevOps organization
AZURE_PROJECT=        # Azure DevOps project
TASKS_DB=/data/tasks.db
UPLOADS_DIR=/data/uploads
N8N_CALLBACK_URL=     # leave empty; superseded by per-request callback_url
```

## Implementation order

Follow the sequence in `arch/integration-plan.md` §"Orden de implementación":
1. Copy `task_store.py`, `logger.py`, `app.py` from `ov-suscripcion-automation`
2. Adapt `placer.py` — remove all Flyway/OV-specific hardcoded paths
3. Add `src/auth.py` + enforce token on all endpoints
4. Add `src/azure_client.py` + `POST /azure/pull-requests`
5. Add `GET /azure/pull-requests/<pr_id>`
6. Add `?ticket=` filter on `GET /tasks`
7. Make `callback_url` a per-request param (remove hardcoded `N8N_CALLBACK_URL` dependency)

## Key constraints

- Do **not** modify `ov-suscripcion-automation` — it continues serving its own domain unchanged.
- `POST /run` accepts JSON (not `multipart/form-data`). The caller provides already-generated file paths or pure git instructions.
- This service never talks to Jira — that is exclusively `claude-mcp-jira`'s responsibility.
- Azure DevOps API version: `7.1`.
