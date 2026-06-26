# Technical Report — code-agent-mcp

**Version:** 1.5  
**Date:** 2026-06-26  
**Status:** Production-ready — verified end-to-end against Azure DevOps (Zurich Insurance Ecuador). 150 tests.

---

## 1. Purpose

`code-agent-mcp` is a generic HTTP agent that executes git operations and manages pull requests in Azure DevOps on behalf of an external orchestrator. Its responsibility is deliberately limited to the version control layer:

- **Manages repositories** — registration, branch inspection, role classification
- **Creates and updates auxiliary branches** — from an existing feature branch
- **Integrates files** — copies file content from the feature branch into the auxiliary branch
- **Creates pull requests** — exclusively the auxiliary branch toward the chosen integration branch

**Core principle:** the agent never touches code. It never generates, modifies, or interprets the content of the files it moves. The caller (orchestrator) is responsible for generating files before invoking this service.

---

## 2. Architecture

### 2.1 System position

```
claude-mcp-jira (orchestrator)
        │
        │  HTTP + X-Agent-Token
        ▼
code-agent-mcp (this service)
        │
        ├── git (HTTPS + PAT) ──────► Azure DevOps repositories
        └── Azure DevOps REST API ──► pull requests
```

The orchestrator decides which files to generate, which ticket to process, and when to create the PR. This agent only executes the required git/Azure operations.

### 2.2 Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.12, Conda env `code-agent-mcp` |
| HTTP framework | Flask + flasgger (Swagger UI at `/apidocs/`) |
| Persistence | SQLite (five tables: `tasks`, `repos`, `projects`, `branch_config`, `prs`) |
| Git | `subprocess` → git CLI via HTTPS + PAT |
| Azure DevOps | REST API v7.1 (Basic auth + PAT) |

### 2.3 Modules

```
app.py                  — Flask entry point; all endpoints; async task pattern
src/
  auth.py               — X-Agent-Token middleware → 401
  task_store.py         — SQLite: tasks table (async task pattern + step tracking)
  repo_store.py         — SQLite: repos table (branch_roles JSON, branch_map JSON, local_path)
  project_store.py      — SQLite: projects table (slug {org}/{name})
  branch_config.py      — branch dictionary persisted in SQLite; hot-reload;
                          resolve_target_branch(target, branch_map) → real branch
  pr_store.py           — SQLite: prs table; populated from prepare-and-pr and pull-requests
  repo_inspector.py     — parses Azure DevOps URLs; git ls-remote; auto_assign_roles()
  placer.py             — git: create_feature_branch, ensure_auxiliary_branch,
                          detect_changed_files, detect_base_branch, git_add_commit_push
  azure_client.py       — Azure DevOps REST API: _create_pr, _find_existing_pr, blueprints
  logger.py             — structured logging with [MODULE] prefix
apis/                   — curl reference scripts by domain
tests/                  — pytest (150 tests)
arch/                   — design and technical documentation
```

---

## 3. API

All endpoints require the `X-Agent-Token` header (value = `TOKEN_AZURE` env var). The only unauthenticated endpoint is `GET /health`.

### 3.1 Endpoint table

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check (no token required) |
| `POST` | `/run` | Enqueue git task: branch + commit + push + aux branch → 202 |
| `GET` | `/status/<task_id>` | Poll task status; includes `steps` field |
| `GET` | `/tasks` | Recent tasks (`?ticket=`, `?limit=`) |
| `POST` | `/repos` | Register repo + inspection (idempotent — re-register preserves `local_path` and `branch_map`) |
| `GET` | `/repos` | List registered repositories |
| `GET` | `/repos/<name>` | Get repo (includes `branch_roles` and `branches_by_role`) |
| `POST` | `/repos/<name>/refresh` | Re-inspect repository |
| `DELETE` | `/repos/<name>` | Remove from registry |
| `PATCH` | `/repos/<name>/branches/<branch>` | Override branch role |
| `PATCH` | `/repos/<name>/branch-map` | Set target→real branch mapping (e.g. `{"prod":"develop"}`) |
| `GET` | `/projects` | List Azure DevOps projects (with their repos) |
| `GET` | `/projects/<org>/<name>` | Get project by slug |
| `GET` | `/config/branches` | View branch dictionary |
| `PUT` | `/config/branches` | Update branch dictionary (persisted to SQLite) |
| `POST` | `/azure/prepare-and-pr/preview` | Dry-run: detect base branch and files without creating anything |
| `POST` | `/azure/prepare-and-pr` | **Main endpoint:** ensure aux branch + find-or-create aux PR (idempotent) |
| `POST` | `/azure/pull-requests` | Create feature PR + aux PR simultaneously (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | PR status + CI build status |
| `PATCH` | `/azure/pull-requests/<pr_id>` | Complete / abandon / reactivate PR (`status`: `completed\|abandoned\|active`) |
| `GET` | `/prs` | List stored PRs (`?repo=`, `?status=`, `?task_id=`, `?limit=`) |
| `GET` | `/prs/<pr_id>` | PR record with status refreshed from Azure DevOps |

### 3.2 Main endpoint: `POST /azure/prepare-and-pr`

Encapsulates the full auxiliary branch and PR flow in a single idempotent call.

**Required fields:** `repo`, `branch`, `target`, `ticket`, `title`  
**Optional fields:** `repo_path`, `files`, `base_branch`, `description`

```json
{
  "repo":   "ov-arizona-backend-ecuador",
  "branch": "feature/ZNRX_67108_renov_agosto",
  "target": "test",
  "ticket": "ZNRX-67108",
  "title":  "ZNRX-67108 Renovaciones agosto → test"
}
```

`repo_path` is optional if the repo was registered with `local_path` — resolved automatically from the registry. If provided in the body, it takes precedence (backward compatible).  
If `files` is omitted: auto-detected via `git diff --name-only origin/{base_branch}...origin/{branch}`.  
If `base_branch` is omitted: inferred via `git merge-base` against the repo's registered branch candidates (base-role branches first, then integration).  
`target` is resolved through `resolve_target_branch(target, repo.branch_map)` — the per-repo `branch_map` translates logical names (e.g. `"prod"`) to real branch names (e.g. `"develop"`).

**Response (201):**
```json
{
  "aux_branch":     "feature/ZNRX_67108_renov_agosto_test_auxiliar",
  "action":         "created",
  "target":         "test",
  "real_target":    "test",
  "base_branch":    "develop",
  "files_detected": ["/local/path/to/repo/src/File.java"],
  "pr":             {"pr_id": 2554, "pr_url": "https://dev.azure.com/..."}
}
```

| `action` | Meaning |
|---|---|
| `created` | Auxiliary branch did not exist; created from `origin/{real_target}` |
| `updated` | Branch existed but files were outdated; changes applied |
| `unchanged` | Branch and PR already existed and are up to date; PR returned without duplication |

### 3.3 Preview (dry-run): `POST /azure/prepare-and-pr/preview`

Same fields as `prepare-and-pr` but `ticket` and `title` are optional. Creates nothing — only runs the detection phase and checks whether a PR already exists.

**Response (200):**
```json
{
  "branch":         "feature/test_mcp_jira_multifile",
  "target":         "test",
  "real_target":    "test",
  "base_branch":    "develop",
  "aux_branch":     "feature/test_mcp_jira_multifile_test_auxiliar",
  "files_detected": ["...avisos.component.css", "...avisos.component.html"],
  "existing_pr":    {"pr_id": 2560, "pr_url": "..."} | null
}
```

`existing_pr: null` → running `prepare-and-pr` will create the PR.  
`existing_pr: {...}` → active PR already exists; `prepare-and-pr` will return it without duplication.

---

## 4. Git flows

### 4.1 Repository registration

```
POST /repos {git_url}
    │
    ├── parse_azure_url()          → {org, project, repo, clean_url}
    ├── requests.get(Azure API)    → repo metadata (default_branch, project_id, size_kb)
    ├── git ls-remote --heads      → list of remote branches
    ├── classify_branches()        → {integration: [], feature: [], other: []}
    ├── auto_assign_roles()        → {branch: role} per branch
    ├── repo_store.upsert()        → persisted to SQLite (repos table)
    └── project_store.upsert()     → project upserted (projects table)
```

Re-registering an existing repo re-inspects it and preserves `local_path` and `branch_map` if not provided in the new request.

### 4.2 Branch roles

Assignment follows this priority:

1. Branch name is in the global dictionary (`branch_config`) → use its `role` field
2. Starts with `feature/` or `fix/` → `"feature"`
3. In the set of known integration branch names → `"integration"`
4. Default → `"other"`

Default roles in the global dictionary (persisted in SQLite, configurable via API):

| Branch | Role | Notes |
|---|---|---|
| `develop` | `base` | Feature branch origin (`is_base=True`); PR target for production |
| `developer` | `integration` | DEV-UAT |
| `test` | `integration` | Preprod |
| `main` | `integration` | Production (DevOps integrates develop→main manually) |

Roles can be overridden per-repo without re-inspecting: `PATCH /repos/<name>/branches/<branch>`.

### 4.3 Per-repo branch map

`PATCH /repos/<name>/branch-map` stores a `{logical_target: real_branch}` mapping. Used by `resolve_target_branch()` to translate caller-supplied logical names to real branch names:

```
{"developer": "developer", "test": "test", "prod": "develop"}
```

Resolution order: per-repo `branch_map` → global registry → `base_branch()` fallback.

### 4.4 Auto-detection of files and base branch

**`detect_changed_files(repo_root, feature_branch, base_branch)` → `list[Path]`**  
Runs `git diff --name-only origin/{base_branch}...origin/{feature_branch}`. Both branches must be fetched. Raises `RuntimeError` on git failure.

**`detect_base_branch(repo_root, feature_branch, candidates)` → `str`**  
For each candidate runs `git merge-base origin/{candidate} origin/{feature_branch}` + `git rev-list --count {hash}`. Picks the closest ancestor. On tie, the first candidate wins (put base-role branches first). Falls back to the first candidate if all merge-bases fail.

### 4.5 Auxiliary branch creation (`ensure_auxiliary_branch`)

```
1. git fetch origin {target}
2. git fetch origin {feature_branch}
3. git ls-remote --heads origin {aux}

If NOT found:
    4. git checkout -b {aux} origin/{target}
    5. git show origin/{feature}:{file} → write to disk for each file
    6. git add + git commit
    7. git push --set-upstream origin {aux}
    8. git checkout {original HEAD}
    9. git branch -D {aux}              ← local cleanup

If found:
    4. git fetch origin {aux}
    5. Compare content: origin/{feature}:{file} vs origin/{aux}:{file}
    6. All match → return "unchanged"
    7. Differences found:
       git checkout -b {aux}_update_tmp origin/{aux}
       apply outdated files
       git add + git commit (update)
       git branch -m {aux}_update_tmp {aux}
       git push --force-with-lease origin {aux}
       git checkout {original HEAD}
       git branch -D {aux}              ← local cleanup
```

The function always restores HEAD to its previous state and deletes any temporary local branches.

### 4.6 Step tracking in `POST /run`

The worker initializes all three steps as `pending` on startup and updates them in real time:

```
create_branch     → pending → running → done | failed
commit_push       → pending → running → done | failed
create_aux_branch → pending → running → done | failed
```

The `steps` field (JSON) is returned by `GET /status/<task_id>`. If a step fails, subsequent steps remain `pending`.

---

## 5. Persistence

### 5.1 SQLite tables

**`tasks`** — asynchronous git operations
```
task_id, ticket, status, branch, aux_branch, commit_id,
repo, build_status, summary, error, steps (JSON), created_at, updated_at
```

**`repos`** — registered repositories
```
repo_id, name, git_url, org, project, project_id, azure_repo_id,
default_branch, web_url, branches (JSON), known_branches (JSON),
branch_roles (JSON), branch_map (JSON), local_path, size_kb, created_at, updated_at
```

**`projects`** — Azure DevOps projects (deduplicated by slug)
```
project_id (slug org/name), org, name, azure_project_id,
description, visibility, state, web_url, created_at, updated_at
```

**`branch_config`** — branch dictionary
```
branch (PK), meta (JSON: label, environment, url, is_base, role)
```
Seeded with 4 defaults on the first `init_db()` call. Configurable via `PUT /config/branches`.

**`prs`** — Azure DevOps pull requests
```
pr_id (PK), pr_url, repo, source_branch, target_branch,
title, status, task_id (nullable FK), created_at, updated_at
```
Populated from `prepare-and-pr`, `pull-requests`, and `PATCH pull-requests/<id>`.

### 5.2 Security — registry as allowlist

The repo registry acts as a user-defined allowlist. `POST /azure/prepare-and-pr`, `POST /azure/prepare-and-pr/preview`, and `POST /run` return **403** if the repo is not registered. The Azure DevOps PAT provides a second authorization layer for all REST calls.

### 5.3 Migration for existing installs

```bash
sqlite3 /tmp/code-agent-mcp.db \
  "ALTER TABLE repos ADD COLUMN branch_roles TEXT;
   ALTER TABLE repos ADD COLUMN local_path TEXT;
   ALTER TABLE repos ADD COLUMN branch_map TEXT;
   ALTER TABLE tasks ADD COLUMN steps TEXT;"
```

All migrations are also applied automatically by `init_db()` on startup via `PRAGMA table_info`.

---

## 6. Configuration

### 6.1 Environment variables

| Variable | Required | Description |
|---|---|---|
| `TOKEN_AZURE` | Yes | Shared secret with the caller (`X-Agent-Token` header) |
| `AGENT_TOKEN` | No | Alias for `TOKEN_AZURE` — same value, for callers that use this name |
| `AZURE_PAT` | Yes | Azure DevOps Personal Access Token |
| `AZURE_ORG` | Yes | Azure DevOps organization (e.g. `ZurichInsurance-EC`) |
| `AZURE_PROJECT` | Yes | Default project for PR creation |
| `GIT_USERNAME` | Yes | Username for HTTPS git authentication |
| `GIT_PAT` | Yes | PAT for HTTPS git authentication |
| `TASKS_DB` | Yes | Path to the SQLite file (all tables share this file) |
| `PORT` | No | Server port (default: 5000) |
| `RETENTION_DAYS` | No | Task retention in SQLite in days (default: 90) |
| `CALLBACK_VERIFY_SSL` | No | Verify SSL on callback POSTs (default: `true`) |

### 6.2 Local startup

```bash
# Create .env.local with the variables above
./run_local.sh   # kills any existing process on the port, sources .env.local,
                 # sets TASKS_DB=/tmp/code-agent-mcp.db, starts on port 5001
```

> **Note:** `conda run` does not inherit parent shell exports. Always use `run_local.sh` or pass variables explicitly.

The SQLite database at `TASKS_DB` persists across server restarts — it is not deleted or reset on startup.

---

## 7. Testing

```bash
conda activate code-agent-mcp
pytest tests/                             # full suite (150 tests)
pytest tests/test_placer.py -v            # git operations + auto-detection
pytest tests/test_azure_client.py -v      # Azure DevOps API + registry validation
pytest tests/test_repo_inspector.py -v    # repo inspection + role assignment
pytest tests/test_repo_endpoints.py -v    # /repos, /projects, /run endpoints
pytest tests/test_branch_config.py -v     # branch dictionary SQLite
pytest tests/test_pr_store.py -v          # PR persistence + endpoints
```

All tests mock `subprocess` and `requests` — no Azure DevOps connection or real git repository required.

---

## 8. Backlog

| Priority | Item |
|---|---|
| High | HTTP client in `claude-mcp-jira` + MCP tools (`run_code_agent`, `get_code_agent_status`, `create_azure_pull_request`, `get_pull_request_status`) — implementation owned by that project |
| Low | `docker-compose.yml` for joint local development with `claude-mcp-jira` |
| Low | Pagination in `GET /tasks` |
| Low | UI to edit the branch dictionary |
| Future | PR votes (`PUT /azure/pull-requests/<pr_id>/vote` — approve/reject/abstain/reset) |
| Future | Remote repo management: volume mode (`POST /repos {clone:true}` — auto-clones to `/data/repos/{name}`) or REST API mode (replace subprocess with Azure DevOps REST calls) — see `TODO.md` |

---

## 9. Related repositories

| Repo | Local path | Relationship |
|---|---|---|
| `claude-mcp-jira` | `/home/idavid/dev/claude/claude-mcp-jira` | Orchestrator — future consumer of this service |
| `ov-arizona-backend-ecuador` | `/home/idavid/dev/ov/ov-arizona-backend-ecuador` | Primary git target repository |
| `ov-arizona-frontend-ecuador` | `/home/idavid/dev/ov/ov-arizona-frontend-ecuador` | Secondary git target repository |
| `ov-arizona-core` | `/home/idavid/dev/ov/ov-arizona-core` | Secondary git target repository |
| `ov-suscripcion-automation` | `/home/idavid/dev/ov/ov-suscripcion-automation` | Source of copied base infrastructure; do not modify |
