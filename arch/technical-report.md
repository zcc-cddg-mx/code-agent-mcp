# Reporte Técnico — code-agent-mcp

**Versión:** 1.0  
**Fecha:** 2026-06-23  
**Estado:** Funcional — probado end-to-end contra Azure DevOps (Zurich Insurance Ecuador)

---

## 1. Propósito

`code-agent-mcp` es un agente HTTP genérico que ejecuta operaciones git y administra pull requests en Azure DevOps en nombre de un orquestador externo. Su responsabilidad está deliberadamente limitada a la capa de control de versiones:

- **Administra repositorios** — registro, inspección de ramas, clasificación de roles
- **Crea y actualiza ramas auxiliares** — a partir de un feature branch existente
- **Integra archivos** — copia el contenido de archivos desde el feature hacia la rama auxiliar
- **Crea pull requests** — exclusivamente la rama auxiliar hacia la rama de integración elegida

**Principio fundamental:** el agente no toca código. Nunca genera, modifica ni interpreta el contenido de los archivos que mueve. El caller (orquestador) es responsable de generar los archivos antes de invocar este servicio.

---

## 2. Arquitectura

### 2.1 Posición en el sistema

```
claude-mcp-jira (orquestador)
        │
        │  HTTP + X-Agent-Token
        ▼
code-agent-mcp (este servicio)
        │
        ├── git (SSH/HTTPS) ──────► repositorios Azure DevOps
        └── Azure DevOps REST API ► pull requests
```

El orquestador decide qué archivos generar, qué ticket procesar, y cuándo crear el PR. Este agente solo ejecuta las operaciones git/Azure necesarias.

### 2.2 Stack

| Componente | Tecnología |
|---|---|
| Runtime | Python 3.12, Conda env `code-agent-mcp` |
| Framework HTTP | Flask + flasgger (Swagger UI en `/apidocs/`) |
| Persistencia | SQLite (tres tablas: `tasks`, `repos`, `projects`) |
| Git | `subprocess` → CLI git con HTTPS + PAT |
| Azure DevOps | REST API v7.1 (autenticación Basic + PAT) |

### 2.3 Módulos

```
app.py                  — Flask entry point; todos los endpoints; async task pattern
src/
  auth.py               — middleware X-Agent-Token → 401
  task_store.py         — SQLite: tabla tasks (async task pattern)
  repo_store.py         — SQLite: tabla repos (incluyendo branch_roles JSON)
  project_store.py      — SQLite: tabla projects (slug {org}/{name})
  branch_config.py      — diccionario de ramas con hot-reload; campo role por rama
  repo_inspector.py     — parsea URLs Azure DevOps; git ls-remote; auto_assign_roles()
  placer.py             — git: create_feature_branch, ensure_auxiliary_branch, git_add_commit_push
  azure_client.py       — Azure DevOps REST API: _create_pr, _find_existing_pr, blueprints
  logger.py             — log estructurado con prefijo [MÓDULO]
apis/                   — scripts curl de referencia por dominio
tests/                  — pytest (62+ tests)
arch/                   — diseño y documentación técnica
```

---

## 3. API

Todos los endpoints requieren el header `X-Agent-Token` (valor = `TOKEN_AZURE` en `.env`). Único endpoint sin autenticación: `GET /health`.

### 3.1 Tabla de endpoints

| Método | Path | Descripción |
|---|---|---|
| `GET` | `/health` | Liveness check (sin token) |
| `POST` | `/run` | Encolar tarea git: branch + commit + push + aux branch → 202 |
| `GET` | `/status/<task_id>` | Consultar estado de tarea (polling) |
| `GET` | `/tasks` | Tareas recientes (`?ticket=`, `?limit=`) |
| `POST` | `/repos` | Registrar repositorio + inspección inmediata |
| `GET` | `/repos` | Listar repositorios registrados |
| `GET` | `/repos/<name>` | Obtener repo (incluye `branch_roles` y `branches_by_role`) |
| `POST` | `/repos/<name>/refresh` | Re-inspeccionar repositorio |
| `DELETE` | `/repos/<name>` | Eliminar del registro |
| `PATCH` | `/repos/<name>/branches/<branch>` | Corregir rol de una rama |
| `GET` | `/projects` | Listar proyectos Azure DevOps (con repos) |
| `GET` | `/projects/<org>/<name>` | Obtener proyecto por slug |
| `GET` | `/config/branches` | Ver diccionario de ramas |
| `PUT` | `/config/branches` | Actualizar diccionario de ramas (hot-reload) |
| `POST` | `/azure/prepare-and-pr` | **Endpoint principal:** verificar/crear aux branch + PR auxiliar (idempotente) |
| `POST` | `/azure/pull-requests` | Crear PR feature + PR auxiliar simultáneamente (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado PR + build CI |

### 3.2 Endpoint principal: `POST /azure/prepare-and-pr`

Encapsula el flujo completo de rama auxiliar y PR en una sola llamada idempotente.

**Request:**
```json
{
  "repo":        "ov-arizona-backend-ecuador",
  "repo_path":   "/ruta/local/al/clon/del/repo",
  "branch":      "feature/ZNRX_67108_renov_agosto",
  "files":       ["/ruta/local/al/repo/src/migrations/V001__data.sql"],
  "target":      "test",
  "ticket":      "ZNRX-67108",
  "title":       "ZNRX-67108 Renovaciones agosto → test",
  "description": "Generado automáticamente por claude-mcp-jira"
}
```

**Response (201 — creado / actualizado):**
```json
{
  "aux_branch": "feature/ZNRX_67108_renov_agosto_test_auxiliar",
  "action":     "created",
  "pr":         {"pr_id": 2554, "pr_url": "https://dev.azure.com/..."}
}
```

| `action` | Significado |
|---|---|
| `created` | Rama auxiliar no existía; creada desde `origin/{target}` |
| `updated` | Rama existía pero archivos diferían; cambios aplicados |
| `unchanged` | Rama y PR ya existían y están al día; PR devuelto sin duplicar |

---

## 4. Flujos git

### 4.1 Registro de repositorio

```
POST /repos {git_url}
    │
    ├── parse_azure_url()          → {org, project, repo, clean_url}
    ├── requests.get(Azure API)    → metadata del repo (default_branch, project_id, size_kb)
    ├── git ls-remote --heads      → lista de ramas remotas
    ├── classify_branches()        → {integration: [], feature: [], other: []}
    ├── auto_assign_roles()        → {branch: role} por rama
    ├── repo_store.upsert()        → persiste en SQLite (tabla repos)
    └── project_store.upsert()     → upsert del proyecto (tabla projects)
```

### 4.2 Roles de ramas

La asignación sigue esta prioridad:

1. Si la rama está en el diccionario global (`branch_config`) → usa su campo `role`
2. Si empieza con `feature/` o `fix/` → `"feature"`
3. Si está en el conjunto de nombres de integración conocidos → `"integration"`
4. Default → `"other"`

Roles predefinidos en el diccionario global:

| Rama | Rol | Notas |
|---|---|---|
| `develop` | `base` | Origen de features (`is_base=True`) |
| `developer` | `integration` | DEV-UAT |
| `test` | `integration` | Preprod |
| `main` | `integration` | Producción |

Los roles se pueden corregir por repo sin re-inspeccionar: `PATCH /repos/<name>/branches/<branch>`.

### 4.3 Creación de rama auxiliar (`ensure_auxiliary_branch`)

```
1. git fetch origin {target}
2. git fetch origin {feature_branch}
3. git ls-remote --heads origin {aux}

Si NO existe:
    4. git checkout -b {aux} origin/{target}
    5. git show origin/{feature}:{file} → escribe en disco por cada archivo
    6. git add + git commit
    7. git push --set-upstream origin {aux}
    8. git checkout {HEAD original}
    9. git branch -D {aux}              ← limpieza local

Si EXISTS:
    4. git fetch origin {aux}
    5. Comparar contenido de cada archivo: origin/{feature} vs origin/{aux}
    6. Si todos coinciden → retorna "unchanged"
    7. Si hay diferencias:
       git checkout -b {aux}_update_tmp origin/{aux}
       aplicar archivos desactualizados
       git add + git commit (update)
       git branch -m {aux}_update_tmp {aux}
       git push --force-with-lease origin {aux}
       git checkout {HEAD original}
       git branch -D {aux}              ← limpieza local
```

La función siempre restaura el HEAD al estado previo y elimina ramas temporales locales.

---

## 5. Persistencia

### 5.1 Tablas SQLite

**`tasks`** — operaciones git asíncronas
```
task_id, ticket, status, branch, aux_branch, commit_id,
repo, build_status, summary, error, created_at, updated_at
```

**`repos`** — repositorios registrados
```
repo_id, name, git_url, org, project, project_id, azure_repo_id,
default_branch, web_url, branches (JSON), known_branches (JSON),
branch_roles (JSON), size_kb, created_at, updated_at
```

**`projects`** — proyectos Azure DevOps (deduplicados por slug)
```
project_id (slug org/name), org, name, azure_project_id,
description, visibility, state, web_url, created_at, updated_at
```

### 5.2 Migración para instancias existentes

Si la base de datos fue creada antes de la funcionalidad de roles de ramas:
```bash
sqlite3 /tmp/code-agent-mcp.db "ALTER TABLE repos ADD COLUMN branch_roles TEXT;"
```

---

## 6. Configuración

### 6.1 Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `TOKEN_AZURE` | Sí | Secreto compartido con el caller (`X-Agent-Token`) |
| `AZURE_PAT` | Sí | Personal Access Token de Azure DevOps |
| `AZURE_ORG` | Sí | Organización Azure DevOps (ej. `ZurichInsurance-EC`) |
| `AZURE_PROJECT` | Sí | Proyecto default para creación de PRs |
| `GIT_USERNAME` | Sí | Usuario para autenticación git HTTPS |
| `GIT_PAT` | Sí | PAT para autenticación git HTTPS |
| `TASKS_DB` | Sí | Ruta al archivo SQLite |
| `PORT` | No | Puerto del servidor (default: 5000) |
| `BRANCH_CONFIG_PATH` | No | Ruta a JSON de configuración de ramas (hot-reload) |
| `RETENTION_DAYS` | No | Días de retención de tareas en SQLite (default: 90) |
| `CALLBACK_VERIFY_SSL` | No | Verificar SSL en callbacks (default: `true`) |

### 6.2 Arranque local

```bash
# Crear .env.local con las variables anteriores
./run_local.sh   # sourcea .env.local, sets TASKS_DB=/tmp/code-agent-mcp.db
                 # levanta en puerto 5001
```

> **Nota:** `conda run` no hereda variables del shell padre. Siempre usar `run_local.sh` o pasar las variables explícitamente.

---

## 7. Testing

```bash
conda activate code-agent-mcp
pytest tests/          # suite completa
pytest tests/test_placer.py -v            # git operations
pytest tests/test_azure_client.py -v      # Azure DevOps API
pytest tests/test_repo_inspector.py -v    # repo inspection + role assignment
pytest tests/test_repo_endpoints.py -v    # /repos y /projects endpoints
```

Todos los tests mockean subprocess y requests — no requieren conexión a Azure DevOps ni un repo git real.

---

## 8. Pendiente (backlog)

| Prioridad | Ítem |
|---|---|
| Alta | Cliente HTTP en `claude-mcp-jira` + MCP tools (`run_code_agent`, `get_code_agent_status`, `create_azure_pull_request`, `get_pull_request_status`) |
| Media | Registro de PRs en SQLite (`src/pr_store.py`, tabla `prs` separada de `tasks`) con endpoints `GET /prs` y `GET /prs/<pr_id>` |
| Baja | `docker-compose.yml` para desarrollo conjunto con `claude-mcp-jira` |
| Baja | Paginación en `GET /tasks` |
| Baja | UI para editar diccionario de ramas |

---

## 9. Repositorios relacionados

| Repo | Ruta local | Relación |
|---|---|---|
| `claude-mcp-jira` | `/home/idavid/dev/claude/claude-mcp-jira` | Orquestador — consumidor futuro de este servicio |
| `ov-arizona-backend-ecuador` | `/home/idavid/dev/ov/ov-arizona-backend-ecuador` | Repo git destino principal de las operaciones |
| `ov-suscripcion-automation` | `/home/idavid/dev/ov/ov-suscripcion-automation` | Origen de la infraestructura base copiada; no modificar |
