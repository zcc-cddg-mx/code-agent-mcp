# Code Agent MCP — Plan de integración con claude-mcp-jira

## Contexto

`code-agent-mcp` es un agente HTTP genérico que ejecuta operaciones git y crea PRs en Azure DevOps.
Vive en `/home/idavid/dev/claude/code-agent-mcp`.

Se creó desde cero (no modificando `ov-suscripcion-automation`) tomando solo la infraestructura
genérica del code-agent original y descartando toda lógica específica del dominio Flyway/OV.

---

## Estado actual del `code-agent-mcp` (2026-06-25)

**141 tests pasando.** Probado e2e contra Azure DevOps — PRs #2552–#2561 reales creados. Todas las tablas SQLite verificadas con datos reales.

### Módulos implementados

| Módulo | Responsabilidad |
|---|---|
| `app.py` | Flask HTTP API, todos los endpoints, Swagger UI (`/apidocs/`) |
| `src/auth.py` | `X-Agent-Token` header → 401 si falta/incorrecto; `/health` es el único endpoint libre |
| `src/task_store.py` | SQLite: tabla `tasks` (patrón async 202 + polling); campo `steps` (JSON) para step tracking |
| `src/repo_store.py` | SQLite: tabla `repos` con columnas `branch_roles` (JSON) y `local_path` (TEXT) |
| `src/project_store.py` | SQLite: tabla `projects` (slug `{org}/{name}`); auto-upsert al registrar repo |
| `src/branch_config.py` | Diccionario de ramas persistido en SQLite (tabla `branch_config`); hot-reload; defaults del README de `ov-arizona-backend-ecuador` |
| `src/pr_store.py` | SQLite: tabla `prs`; poblada desde `prepare-and-pr`, `pull-requests` y `PATCH pull-requests/<id>` |
| `src/repo_inspector.py` | Parsea URLs Azure DevOps, `git ls-remote`, clasifica ramas, auto-asigna roles |
| `src/placer.py` | Git genérico: `create_feature_branch`, `git_add_commit_push`, `ensure_auxiliary_branch` (idempotente), `detect_changed_files`, `detect_base_branch` |
| `src/azure_client.py` | Azure DevOps REST API v7.1: crear PR, buscar PR existente, estado PR + build, PATCH PR |
| `src/logger.py` | Log estructurado |

### API surface completa

| Método | Path | Descripción |
|---|---|---|
| `GET` | `/health` | Liveness (sin token) |
| `POST` | `/run` | Encolar tarea git → 202 inmediato; 403 si repo no registrado |
| `GET` | `/status/<task_id>` | Estado de la tarea (incluye campo `steps`) |
| `GET` | `/tasks` | Últimas N tareas; `?ticket=` filtra por ticket |
| `GET` | `/config/branches` | Ver registro de ramas |
| `PUT` | `/config/branches` | Actualizar registro (persiste en SQLite, hot-reload) |
| `POST` | `/repos` | Registrar repo + inspección inmediata |
| `GET` | `/repos` | Listar repos |
| `GET` | `/repos/<name>` | Repo por nombre (incluye `branch_roles` + `branches_by_role`) |
| `POST` | `/repos/<name>/refresh` | Re-inspeccionar repo |
| `DELETE` | `/repos/<name>` | Eliminar del registro |
| `PATCH` | `/repos/<name>/branches/<branch>` | Corregir rol de una rama (sin re-inspeccionar) |
| `GET` | `/projects` | Listar proyectos con sus repos |
| `GET` | `/projects/<org>/<name>` | Proyecto por slug |
| `POST` | `/azure/prepare-and-pr/preview` | Dry-run: detectar rama base y archivos sin crear nada; devuelve `existing_pr`; `repo_path` opcional si `local_path` en registry |
| `POST` | `/azure/prepare-and-pr` | Idempotente: ensure aux branch + find-or-create PR aux ← **endpoint principal**; `repo_path` opcional si `local_path` en registry |
| `POST` | `/azure/pull-requests` | Crear feature PR + aux PR simultáneos (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado del PR + build CI |
| `PATCH` | `/azure/pull-requests/<pr_id>` | Completar / abandonar / reactivar PR |
| `GET` | `/prs` | Listar PRs persistidos (`?repo=`, `?status=`, `?task_id=`, `?limit=`) |
| `GET` | `/prs/<pr_id>` | PR con estado refrescado desde Azure DevOps |

### Git flow implementado

Basado en el README de `ov-arizona-backend-ecuador`:
- Features se cortan desde `develop` (`is_base=True` en branch_config)
- Rama auxiliar: `{feature_branch}_{target}_auxiliar`, base `origin/{target}`
- `ensure_auxiliary_branch()` — idempotente: crea si no existe, actualiza si los archivos difieren, no hace nada si está al día
- `detect_changed_files()` — auto-detección de archivos via `git diff --name-only`
- `detect_base_branch()` — infiere la rama base más cercana via `git merge-base` + candidatos del repo registrado

### Diccionario de ramas (defaults — persiste en SQLite)

| Rama | Label | Rol | `is_base` |
|---|---|---|---|
| `develop` | producción-pre | `base` | ✅ feature branches se cortan desde aquí |
| `developer` | desarrollo | `integration` | — DEV/UAT |
| `test` | pruebas | `integration` | — Preprod |
| `main` | producción-desplegado | `integration` | — Producción |

### Seguridad

El registro de repos actúa como allowlist: `prepare-and-pr`, `preview` y `POST /run` retornan **403** si el repo no está registrado. El Azure DevOps PAT provee autorización sobre todos los recursos REST.

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
3. get_code_agent_status       → polling → "done" → {branch, aux_branch, commit_id, steps}
4. create_azure_pull_request   → {action, pr_id, pr_url}  (idempotente via prepare-and-pr)
5. get_pull_request_status     → esperar build verde
6. update_jira_issue           → link PR + transición "In Review"
```

---

## Integración en `claude-mcp-jira`

> La implementación del cliente y los MCP tools se gestiona desde el proyecto `claude-mcp-jira`
> (`/home/idavid/dev/claude/claude-mcp-jira`). Este servicio está listo para ser consumido.

### Contrato de API que expone este servicio

Variables de entorno que necesita el caller:

```
CODE_AGENT_URL=http://localhost:5001   # URL del agente (5001 en local)
CODE_AGENT_TOKEN=                      # mismo valor que TOKEN_AZURE del agente
```

### MCP tools que consume `claude-mcp-jira`

| Tool | Endpoint | Descripción |
|---|---|---|
| `run_code_agent` | `POST /run` | Retorna `task_id`; 403 si repo no registrado |
| `get_code_agent_status` | `GET /status/<task_id>` | Estado + branch + commit_id + steps |
| `preview_pull_request` | `POST /azure/prepare-and-pr/preview` | Dry-run: muestra rama base + archivos antes de crear (opcional) |
| `create_azure_pull_request` | `POST /azure/prepare-and-pr` | Idempotente: ensure aux + find-or-create PR |
| `get_pull_request_status` | `GET /azure/pull-requests/<pr_id>` | Estado PR + build CI |

---

## Coexistencia con n8n y el code-agent original

- `ov-suscripcion-automation` sigue sin cambios para su dominio (migraciones Flyway OV)
- `code-agent-mcp` es independiente — sin dependencia del anterior
- n8n puede seguir usando `ov-suscripcion-automation` para flujos automáticos (webhook Jira)
- `claude-mcp-jira` usa `code-agent-mcp` para flujos supervisados desde Claude Code
- Retirar n8n es una decisión de equipo, no un prerequisito técnico
