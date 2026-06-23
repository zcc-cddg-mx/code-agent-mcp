# TODO — code-agent-mcp

Estado actual: infraestructura base completa, endpoints Azure implementados.
Se detectaron correcciones necesarias al flujo git tras revisar el repo destino.

---

## En este repo (`code-agent-mcp`)

### Completado
- [x] Copiar infraestructura base de `ov-suscripcion-automation` (`app.py`, `task_store.py`, `logger.py`)
- [x] Adaptar `placer.py` — git genérico sin rutas Flyway hardcodeadas
- [x] `src/auth.py` — `X-Agent-Token` en todos los endpoints
- [x] `src/azure_client.py` — `POST /azure/pull-requests` (feature + aux PR)
- [x] `GET /azure/pull-requests/<pr_id>` — estado PR + build CI
- [x] `GET /tasks?ticket=` — filtro por ticket en SQLite
- [x] `callback_url` por request en `POST /run` (reemplaza `N8N_CALLBACK_URL` hardcodeado)

### Correcciones pendientes (flujo git real — ver README de ov-arizona-backend-ecuador)

Las features/fix se crean desde `develop`, **no** desde `developer`.
La rama auxiliar y su base dependen del destino del PR:

| Destino del PR | Sufijo rama auxiliar | Base de la aux branch |
|---|---|---|
| `developer`    | `_developer_auxiliar` | `origin/developer` |
| `test`         | `_test_aux`           | `origin/test`      |

Ejemplo real (PR-2505):
- Feature: `feature/RITM2521020_relatividades_junio` (desarrollada desde `develop`)
- Aux: `feature/RITM2521020_relatividades_junio_test_aux` (creada desde `origin/test`)
- PR: aux → `test`

- [ ] Corregir `create_feature_branch()` en `placer.py` — default `base_branch` debe ser `develop`
- [ ] Corregir `create_auxiliary_branch()` en `placer.py` — sufijo y base branch deben derivarse del `target`, no estar hardcodeados a `_developer_auxiliar` / `origin/developer`
- [ ] Actualizar `POST /run` — añadir campo `target` (`developer` | `test` | `main`) para parametrizar la aux branch
- [ ] Actualizar tests de `placer.py` para cubrir ambos casos (target=developer, target=test)

---

## En `claude-mcp-jira` (`/home/idavid/dev/claude/claude-mcp-jira`)

- [ ] `service/clients/code_agent_client.py` — HTTP client para los 4 endpoints del agente
- [ ] MCP tools (Fase 11):
  - [ ] `run_code_agent` — llama `POST /run`, retorna `task_id`
  - [ ] `get_code_agent_status` — llama `GET /status/<task_id>`
  - [ ] `create_azure_pull_request` — llama `POST /azure/pull-requests`
  - [ ] `get_pull_request_status` — llama `GET /azure/pull-requests/<pr_id>`
- [ ] e2e test del flujo completo (6 pasos — ver `arch/integration-plan.md`)

---

## Pendiente / nice-to-have

- [ ] `docker-compose.yml` para levantar `code-agent-mcp` + `claude-mcp-jira` juntos en local
- [ ] `GET /tasks` — paginación (actualmente solo `limit`)
