# Code Agent MCP — Plan de integración con claude-mcp-jira

## Contexto

`code-agent-mcp` es un agente HTTP genérico que ejecuta operaciones git y crea PRs en Azure DevOps.
Vive en `/home/idavid/dev/claude/code-agent-mcp`.

Se creó desde cero (no modificando `ov-suscripcion-automation`) tomando solo la infraestructura
genérica del code-agent original y descartando toda lógica específica del dominio Flyway/OV.

---

## Estado actual del `code-agent-mcp` (2026-06-23)

**73 tests pasando.** Probado e2e contra Azure DevOps — PRs #2552–#2554 reales creados.

### Módulos implementados

| Módulo | Responsabilidad |
|---|---|
| `app.py` | Flask HTTP API, todos los endpoints, Swagger UI (`/apidocs/`) |
| `src/auth.py` | `X-Agent-Token` header → 401 si falta/incorrecto; `/health` es el único endpoint libre |
| `src/task_store.py` | SQLite: tabla `tasks` (patrón async 202 + polling) |
| `src/repo_store.py` | SQLite: tabla `repos` con columna `branch_roles` (JSON) |
| `src/project_store.py` | SQLite: tabla `projects` (slug `{org}/{name}`); auto-upsert al registrar repo |
| `src/repo_inspector.py` | Parsea URLs Azure DevOps, `git ls-remote`, clasifica ramas, auto-asigna roles |
| `src/branch_config.py` | Registro dinámico de ramas con hot-reload; defaults del README de `ov-arizona-backend-ecuador` |
| `src/placer.py` | Git genérico: `create_feature_branch`, `git_add_commit_push`, `create_auxiliary_branch`, `ensure_auxiliary_branch` (idempotente) |
| `src/azure_client.py` | Azure DevOps REST API v7.1: crear PR, buscar PR existente, estado PR + build |
| `src/logger.py` | Log estructurado |

### API surface completa

| Método | Path | Descripción |
|---|---|---|
| `GET` | `/health` | Liveness (sin token) |
| `POST` | `/run` | Encolar tarea git → 202 inmediato |
| `GET` | `/status/<task_id>` | Estado de la tarea |
| `GET` | `/tasks` | Últimas N tareas; `?ticket=` filtra por ticket |
| `GET` | `/config/branches` | Ver registro de ramas |
| `PUT` | `/config/branches` | Actualizar registro (hot-reload) |
| `POST` | `/repos` | Registrar repo + inspección inmediata |
| `GET` | `/repos` | Listar repos |
| `GET` | `/repos/<name>` | Repo por nombre (incluye `branch_roles` + `branches_by_role`) |
| `POST` | `/repos/<name>/refresh` | Re-inspeccionar repo |
| `DELETE` | `/repos/<name>` | Eliminar del registro |
| `PATCH` | `/repos/<name>/branches/<branch>` | Corregir rol de una rama (sin re-inspeccionar) |
| `GET` | `/projects` | Listar proyectos con sus repos |
| `GET` | `/projects/<org>/<name>` | Proyecto por slug |
| `POST` | `/azure/prepare-and-pr` | Idempotente: ensure aux branch + find-or-create PR aux ← **endpoint principal** |
| `POST` | `/azure/pull-requests` | Crear feature PR + aux PR simultáneos (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado del PR + build CI |

### Git flow implementado

Basado en el README de `ov-arizona-backend-ecuador`:
- Features se cortan desde `develop` (`is_base=True` en branch_config)
- Rama auxiliar: `{feature_branch}_{target}_auxiliar`, base `origin/{target}`
- `ensure_auxiliary_branch()` — idempotente: crea si no existe, actualiza si los archivos difieren, no hace nada si está al día

### Diccionario de ramas (defaults)

| Rama | Label | Rol | `is_base` |
|---|---|---|---|
| `develop` | producción-pre | `base` | ✅ feature branches se cortan desde aquí |
| `developer` | desarrollo | `integration` | — DEV/UAT |
| `test` | pruebas | `integration` | — Preprod |
| `main` | producción-desplegado | `integration` | — Producción |

---

## Pipeline objetivo (claude-mcp-jira como orquestador)

```
Claude Code (MCP tools)
  → claude-mcp-jira
    → Jira          (ya existe — create, update, transition, comment)
    → code-agent-mcp (nuevo — run_code_agent, get_code_agent_status)
    → Azure DevOps   (nuevo — create_azure_pull_request, get_pull_request_status)
    → Jira           (link PR + transición "In Review")
```

Flujo completo desde Claude Code:

```
1. create_jira_issue           → ZNRX-XXXXX
2. run_code_agent              → task_id  (202 inmediato)
3. get_code_agent_status       → polling → "done" → {branch, aux_branch, commit_id}
4. create_azure_pull_request   → {action, pr_id, pr_url}  (idempotente via prepare-and-pr)
5. get_pull_request_status     → esperar build verde
6. update_jira_issue           → link PR + transición "In Review"
```

---

## Pendiente en `claude-mcp-jira` (Fase 11)

### Nuevo módulo: `service/clients/code_agent_client.py`

Cliente HTTP hacia `code-agent-mcp`. Patrón idéntico a `jira_client.py`.

Variables de entorno a añadir en `claude-mcp-jira`:

```
CODE_AGENT_URL=http://code-agent-mcp:5001   # URL del agente
CODE_AGENT_TOKEN=                            # mismo valor que TOKEN_AZURE del agente
```

### MCP tools a añadir en `jira_mcp/server.py`

| Tool | Rol mínimo | Endpoint que llama | Descripción |
|---|---|---|---|
| `run_code_agent` | lead | `POST /run` | Texto libre → Claude extrae repo/branch/archivos/ticket; retorna `task_id` |
| `get_code_agent_status` | dev | `GET /status/<task_id>` | Estado + branch + commit_id |
| `create_azure_pull_request` | lead | `POST /azure/prepare-and-pr` | Idempotente: ensure aux + find-or-create PR |
| `get_pull_request_status` | dev | `GET /azure/pull-requests/<pr_id>` | Estado PR + build CI |

### Orden de implementación

| Paso | Qué |
|---|---|
| 1 | `service/clients/code_agent_client.py` — funciones: `run_task`, `get_task_status`, `prepare_and_pr`, `get_pr_status` |
| 2 | `jira_mcp/server.py` — añadir 4 tools al schema + dispatch |
| 3 | `jira_mcp/service_client.py` — añadir 4 funciones que llaman al client |
| 4 | `scripts/test-code-agent.sh` — e2e del flujo completo |
| 5 | Actualizar CLAUDE.md, TODO, docs |

---

## Coexistencia con n8n y el code-agent original

- `ov-suscripcion-automation` sigue sin cambios para su dominio (migraciones Flyway OV)
- `code-agent-mcp` es independiente — sin dependencia del anterior
- n8n puede seguir usando `ov-suscripcion-automation` para flujos automáticos (webhook Jira)
- `claude-mcp-jira` usa `code-agent-mcp` para flujos supervisados desde Claude Code
- Retirar n8n es una decisión de equipo, no un prerequisito técnico
