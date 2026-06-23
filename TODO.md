# TODO — code-agent-mcp

Estado actual: servicio funcional con registro de repos/proyectos, roles de ramas, verificación idempotente de rama auxiliar, creación de PR (solo auxiliar), Swagger UI, y scripts curl de referencia. Probado end-to-end contra Azure DevOps (`ov-arizona-backend-ecuador`, PRs #2552–#2554).

---

## Completado

### Infraestructura base
- [x] `task_store.py`, `logger.py`, `app.py` copiados y adaptados desde `ov-suscripcion-automation`
- [x] `src/auth.py` — `X-Agent-Token` en todos los endpoints
- [x] `src/placer.py` — git genérico sin rutas Flyway hardcodeadas
- [x] `callback_url` por request en `POST /run` (reemplaza `N8N_CALLBACK_URL` hardcodeado)
- [x] `GET /tasks?ticket=` — filtro por ticket en SQLite

### Flujo git corregido (según README de ov-arizona-backend-ecuador)
- [x] Features se cortan desde `develop` (no desde `developer`)
- [x] Rama auxiliar: sufijo `_{target}_auxiliar`, base `origin/{target}`
- [x] `POST /run` acepta campo `target` para parametrizar la aux branch
- [x] `aux_branch_name(feature_branch, target)` — función pura reutilizable

### Azure DevOps
- [x] `src/azure_client.py` — `POST /azure/pull-requests` (feature PR + aux PR simultáneos)
- [x] `GET /azure/pull-requests/<pr_id>` — estado PR + build CI

### Diccionario de ramas
- [x] `src/branch_config.py` — registro dinámico con defaults del README
- [x] `GET /config/branches` + `PUT /config/branches` — configurable vía API
- [x] Hot-reload al guardar (sin reiniciar el servidor)

### Registro de repositorios
- [x] `src/repo_inspector.py` — parsea URL Azure DevOps, `git ls-remote`, clasifica ramas
- [x] `src/repo_store.py` — tabla `repos` en SQLite
- [x] `POST /repos` — registro + inspección inmediata
- [x] `GET /repos` / `GET /repos/<name>` / `POST /repos/<name>/refresh` / `DELETE /repos/<name>`

### Registro de proyectos
- [x] `src/project_store.py` — tabla `projects` en SQLite (slug `{org}/{name}`)
- [x] Proyecto se upserta automáticamente al registrar un repo (idempotente)
- [x] `GET /projects` — lista proyectos con sus repos
- [x] `GET /projects/<org>/<name>` — proyecto por slug

### Roles de ramas por repo
- [x] `branch_config.py` — campo `role` en defaults (`base` / `integration`); función `role(branch)`
- [x] `repo_inspector.py` — `auto_assign_roles()`: detecta rol de cada rama al inspeccionar
- [x] `repo_store.py` — columna `branch_roles` (JSON); `set_branch_role()` para actualización puntual
- [x] `GET /repos/<name>` — incluye `branch_roles` y `branches_by_role` (inverso computado)
- [x] `PATCH /repos/<name>/branches/<branch>` — corregir rol de una rama sin re-inspeccionar
- [x] `apis/repos.sh set-role` — subcomando curl para corrección manual
- [x] Probado: `master` de `ensurance-old-web` corregido a `integration` manualmente

### Verificación y PR de rama auxiliar
- [x] `src/placer.py` — `ensure_auxiliary_branch()`: verifica si la aux existe en origin, la crea si no, aplica archivos faltantes si está desactualizada; limpia ramas locales temporales
- [x] `src/azure_client.py` — `_find_existing_pr()`: busca PR activo para `source→target` en Azure DevOps
- [x] `POST /azure/prepare-and-pr` — endpoint completo: ensure aux + find-or-create PR → `{aux_branch, action, pr}`
- [x] `action` ∈ `{"created", "updated", "unchanged"}` — idempotente, sin duplicar PRs
- [x] `apis/azure.sh prepare-and-pr` — subcomando curl de referencia
- [x] Probado: `feature/test_mcp_server` → `test` (PR #2554); segunda llamada devuelve mismo PR sin duplicar

### Documentación y tooling
- [x] Swagger UI via flasgger (`/apidocs/`)
- [x] `run_local.sh` — arranque local sin Docker
- [x] `apis/` — scripts curl de referencia (health, repos, projects, tasks, config, azure)
- [x] Renombrado `AGENT_TOKEN` → `TOKEN_AZURE` (usa el PAT del sistema)

---

## Futura implementación — `claude-mcp-jira`

> Pendiente hasta terminar verificación funcional de este agente.

- [ ] `service/clients/code_agent_client.py` — HTTP client para los endpoints del agente
- [ ] MCP tools:
  - [ ] `run_code_agent` — llama `POST /run`, retorna `task_id`
  - [ ] `get_code_agent_status` — llama `GET /status/<task_id>`
  - [ ] `create_azure_pull_request` — llama `POST /azure/pull-requests`
  - [ ] `get_pull_request_status` — llama `GET /azure/pull-requests/<pr_id>`
- [ ] e2e test del flujo completo (ver `arch/integration-plan.md`)

---

## Antes de producción

- [ ] **Seguridad `repo_path`** — variable de entorno `ALLOWED_REPO_PATHS` (lista de prefijos permitidos); validar en `POST /azure/prepare-and-pr` y `POST /run` antes de ejecutar cualquier operación git
  ```
  ALLOWED_REPO_PATHS=/home/idavid/dev/ov,/repos
  ```

## Próximas fases

- [ ] **Registro de PRs en SQLite** (`src/pr_store.py`, tabla `prs` separada de `tasks`):
  - Campos: `pr_id` (PK, Azure DevOps ID), `pr_url`, `repo`, `source_branch`, `target_branch`, `title`, `status`, `task_id` (nullable FK a tasks), `created_at`, `updated_at`
  - Poblar desde `POST /azure/prepare-and-pr` y `POST /azure/pull-requests`
  - Endpoints: `GET /prs` (con filtros `?repo=`, `?status=`, `?task_id=`), `GET /prs/<pr_id>` (con refresh de estado desde Azure DevOps)

- [ ] **Step tracking en tareas** — campo `steps` (JSON) en tabla `tasks` para exponer progreso granular al orquestador/UI:
  ```json
  {"steps": [
    {"name": "fetch",         "status": "done"},
    {"name": "create_branch", "status": "done"},
    {"name": "push",          "status": "running"}
  ]}
  ```
  Prerrequisito para cualquier UI que muestre progreso en tiempo real.

## Nice-to-have

- [ ] `docker-compose.yml` para levantar `code-agent-mcp` + `claude-mcp-jira` juntos en local
- [ ] `GET /tasks` — paginación (actualmente solo `limit`)
- [ ] UI para editar el diccionario de ramas (`PUT /config/branches`)
- [ ] PostgreSQL — migrar desde SQLite si se necesita concurrencia real (múltiples repos simultáneos)
- [ ] Rate limiting en `POST /run` y `POST /azure/prepare-and-pr` (por token o por repo)
