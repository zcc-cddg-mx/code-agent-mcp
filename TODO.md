# TODO — code-agent-mcp

Estado actual: servicio funcional con registro de repos/proyectos, Swagger UI, y scripts curl de referencia. Probado manualmente contra Azure DevOps (Ensurance-ZEC).

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

### Documentación y tooling
- [x] Swagger UI via flasgger (`/apidocs/`)
- [x] `run_local.sh` — arranque local sin Docker
- [x] `apis/` — scripts curl de referencia (health, repos, projects, tasks, config, azure)

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

## Nice-to-have

- [ ] `docker-compose.yml` para levantar `code-agent-mcp` + `claude-mcp-jira` juntos en local
- [ ] `GET /tasks` — paginación (actualmente solo `limit`)
- [ ] UI para editar el diccionario de ramas (`PUT /config/branches`)
