# Reporte Técnico — code-agent-mcp

**Versión:** 1.2  
**Fecha:** 2026-06-24  
**Estado:** Funcional — verificado end-to-end contra Azure DevOps (Zurich Insurance Ecuador). 133 tests.

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
| Persistencia | SQLite (cinco tablas: `tasks`, `repos`, `projects`, `branch_config`, `prs`) |
| Git | `subprocess` → CLI git con HTTPS + PAT |
| Azure DevOps | REST API v7.1 (autenticación Basic + PAT) |

### 2.3 Módulos

```
app.py                  — Flask entry point; todos los endpoints; async task pattern
src/
  auth.py               — middleware X-Agent-Token → 401
  task_store.py         — SQLite: tabla tasks (async task pattern + step tracking)
  repo_store.py         — SQLite: tabla repos (incluyendo branch_roles JSON)
  project_store.py      — SQLite: tabla projects (slug {org}/{name})
  branch_config.py      — diccionario de ramas persistido en SQLite; hot-reload; campo role por rama
  pr_store.py           — SQLite: tabla prs; poblada desde prepare-and-pr y pull-requests
  repo_inspector.py     — parsea URLs Azure DevOps; git ls-remote; auto_assign_roles()
  placer.py             — git: create_feature_branch, ensure_auxiliary_branch,
                          detect_changed_files, detect_base_branch, git_add_commit_push
  azure_client.py       — Azure DevOps REST API: _create_pr, _find_existing_pr, blueprints
  logger.py             — log estructurado con prefijo [MÓDULO]
apis/                   — scripts curl de referencia por dominio
tests/                  — pytest (133 tests)
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
| `GET` | `/status/<task_id>` | Consultar estado de tarea (polling); incluye campo `steps` |
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
| `PUT` | `/config/branches` | Actualizar diccionario de ramas (persiste en SQLite) |
| `POST` | `/azure/prepare-and-pr/preview` | Dry-run: detectar rama base y archivos sin crear nada |
| `POST` | `/azure/prepare-and-pr` | **Endpoint principal:** verificar/crear aux branch + PR auxiliar (idempotente) |
| `POST` | `/azure/pull-requests` | Crear PR feature + PR auxiliar simultáneamente (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado PR + build CI |
| `PATCH` | `/azure/pull-requests/<pr_id>` | Completar / abandonar / reactivar PR |
| `GET` | `/prs` | Listar PRs persistidos (`?repo=`, `?status=`, `?task_id=`, `?limit=`) |
| `GET` | `/prs/<pr_id>` | PR con estado refrescado desde Azure DevOps |

### 3.2 Endpoint principal: `POST /azure/prepare-and-pr`

Encapsula el flujo completo de rama auxiliar y PR en una sola llamada idempotente.

**Request (campos `files` y `base_branch` opcionales):**
```json
{
  "repo":        "ov-arizona-backend-ecuador",
  "repo_path":   "/ruta/local/al/clon/del/repo",
  "branch":      "feature/ZNRX_67108_renov_agosto",
  "target":      "test",
  "ticket":      "ZNRX-67108",
  "title":       "ZNRX-67108 Renovaciones agosto → test",
  "files":       ["/ruta/local/al/repo/src/File.java"],
  "base_branch": "develop",
  "description": "Generado automáticamente por claude-mcp-jira"
}
```

Si `files` se omite: auto-detectado via `git diff --name-only origin/{base_branch}...origin/{branch}`.  
Si `base_branch` se omite: inferido via `git merge-base` comparando con los candidatos del repo registrado (base-role primero, luego integration).

**Response (201):**
```json
{
  "aux_branch":     "feature/ZNRX_67108_renov_agosto_test_auxiliar",
  "action":         "created",
  "base_branch":    "develop",
  "files_detected": ["/ruta/local/al/repo/src/File.java"],
  "pr":             {"pr_id": 2554, "pr_url": "https://dev.azure.com/..."}
}
```

| `action` | Significado |
|---|---|
| `created` | Rama auxiliar no existía; creada desde `origin/{target}` |
| `updated` | Rama existía pero archivos diferían; cambios aplicados |
| `unchanged` | Rama y PR ya existían y están al día; PR devuelto sin duplicar |

### 3.3 Preview (dry-run): `POST /azure/prepare-and-pr/preview`

Mismos campos que `prepare-and-pr` pero `ticket` y `title` son opcionales. No crea nada — solo ejecuta la fase de detección y consulta si ya existe un PR.

**Response (200):**
```json
{
  "branch":         "feature/test_mcp_jira_multifile",
  "target":         "test",
  "base_branch":    "develop",
  "aux_branch":     "feature/test_mcp_jira_multifile_test_auxiliar",
  "files_detected": ["...avisos.component.css", "...avisos.component.html", "...avisos.component.ts"],
  "existing_pr":    {"pr_id": 2560, "pr_url": "..."} | null
}
```

`existing_pr: null` → ejecutar `prepare-and-pr` creará el PR.  
`existing_pr: {...}` → ya existe PR activo; `prepare-and-pr` lo devolverá sin duplicar.

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

Roles predefinidos en el diccionario global (persiste en SQLite, configurable vía API):

| Rama | Rol | Notas |
|---|---|---|
| `develop` | `base` | Origen de features (`is_base=True`) |
| `developer` | `integration` | DEV-UAT |
| `test` | `integration` | Preprod |
| `main` | `integration` | Producción |

Los roles se pueden corregir por repo sin re-inspeccionar: `PATCH /repos/<name>/branches/<branch>`.

### 4.3 Detección automática de archivos y rama base

**`detect_changed_files(repo_root, feature_branch, base_branch)` → `list[Path]`**  
Ejecuta `git diff --name-only origin/{base_branch}...origin/{feature_branch}`. Ambas ramas deben estar fetched. Lanza `RuntimeError` en caso de fallo git.

**`detect_base_branch(repo_root, feature_branch, candidates)` → `str`**  
Para cada candidato ejecuta `git merge-base origin/{candidate} origin/{feature_branch}` + `git rev-list --count {hash}`. Elige el de menor distancia. En empate, gana el primero de la lista (poner base-role primero). Fallback al primer candidato si todos los merge-base fallan.

### 4.4 Creación de rama auxiliar (`ensure_auxiliary_branch`)

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

Si existe:
    4. git fetch origin {aux}
    5. Comparar contenido: origin/{feature}:{file} vs origin/{aux}:{file}
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

### 4.5 Step tracking en `POST /run`

El worker inicializa los tres pasos como `pending` al arrancar y los actualiza en tiempo real:

```
create_branch   → pending → running → done | failed
commit_push     → pending → running → done | failed
create_aux_branch → pending → running → done | failed
```

El campo `steps` (JSON) se devuelve en `GET /status/<task_id>`. Si un paso falla, los siguientes quedan en `pending`.

---

## 5. Persistencia

### 5.1 Tablas SQLite

**`tasks`** — operaciones git asíncronas
```
task_id, ticket, status, branch, aux_branch, commit_id,
repo, build_status, summary, error, steps (JSON), created_at, updated_at
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

**`branch_config`** — diccionario de ramas
```
branch (PK), meta (JSON: label, environment, url, is_base, role)
```
Sembrado con 4 defaults en el primer `init_db()`. Configurable vía `PUT /config/branches`.

**`prs`** — pull requests Azure DevOps
```
pr_id (PK), pr_url, repo, source_branch, target_branch,
title, status, task_id (nullable FK), created_at, updated_at
```
Poblada desde `prepare-and-pr`, `pull-requests` y `PATCH pull-requests/<id>`.

### 5.2 Seguridad — registro como allowlist

El registro de repos en SQLite actúa como allowlist definido por el usuario. `POST /azure/prepare-and-pr`, `POST /azure/prepare-and-pr/preview` y `POST /run` devuelven **403** si el repo no está registrado. El Azure DevOps PAT provee una segunda capa de autorización en todas las llamadas REST.

### 5.3 Migración para instancias existentes

```bash
sqlite3 /tmp/code-agent-mcp.db \
  "ALTER TABLE repos ADD COLUMN branch_roles TEXT;
   ALTER TABLE tasks ADD COLUMN steps TEXT;"
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
| `TASKS_DB` | Sí | Ruta al archivo SQLite (todas las tablas comparten este archivo) |
| `PORT` | No | Puerto del servidor (default: 5000) |
| `RETENTION_DAYS` | No | Días de retención de tareas en SQLite (default: 90) |
| `CALLBACK_VERIFY_SSL` | No | Verificar SSL en callbacks (default: `true`) |

### 6.2 Arranque local

```bash
# Crear .env.local con las variables anteriores
./run_local.sh   # mata proceso existente en el puerto, sourcea .env.local,
                 # sets TASKS_DB=/tmp/code-agent-mcp.db, levanta en puerto 5001
```

> **Nota:** `conda run` no hereda variables del shell padre. Siempre usar `run_local.sh` o pasar las variables explícitamente.

---

## 7. Testing

```bash
conda activate code-agent-mcp
pytest tests/                             # suite completa (133 tests)
pytest tests/test_placer.py -v            # git operations + detección automática
pytest tests/test_azure_client.py -v      # Azure DevOps API + registry validation
pytest tests/test_repo_inspector.py -v    # repo inspection + role assignment
pytest tests/test_repo_endpoints.py -v    # /repos, /projects, /run endpoints
pytest tests/test_branch_config.py -v     # diccionario de ramas SQLite
pytest tests/test_pr_store.py -v          # PR persistence + endpoints
```

Todos los tests mockean subprocess y requests — no requieren conexión a Azure DevOps ni un repo git real.

---

## 8. Backlog

| Prioridad | Ítem |
|---|---|
| Alta | Cliente HTTP en `claude-mcp-jira` + MCP tools (`run_code_agent`, `get_code_agent_status`, `create_azure_pull_request`, `get_pull_request_status`) |
| Baja | `docker-compose.yml` para desarrollo conjunto con `claude-mcp-jira` |
| Baja | Paginación en `GET /tasks` |
| Baja | UI para editar diccionario de ramas |
| Futura | Votos en PRs (`PUT /azure/pull-requests/<pr_id>/vote` — approve/reject/abstain/reset) |

---

## 9. Repositorios relacionados

| Repo | Ruta local | Relación |
|---|---|---|
| `claude-mcp-jira` | `/home/idavid/dev/claude/claude-mcp-jira` | Orquestador — consumidor futuro de este servicio |
| `ov-arizona-backend-ecuador` | `/home/idavid/dev/ov/ov-arizona-backend-ecuador` | Repo git destino principal de las operaciones |
| `ov-suscripcion-automation` | `/home/idavid/dev/ov/ov-suscripcion-automation` | Origen de la infraestructura base copiada; no modificar |
