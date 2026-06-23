# TODO — code-agent-mcp

Estado actual: infraestructura base completa, endpoints Azure implementados.
Próximos pasos: integración con `claude-mcp-jira`.

---

## En este repo (`code-agent-mcp`)

- [x] Copiar infraestructura base de `ov-suscripcion-automation` (`app.py`, `task_store.py`, `logger.py`)
- [x] Adaptar `placer.py` — git genérico sin rutas Flyway hardcodeadas
- [x] `src/auth.py` — `X-Agent-Token` en todos los endpoints
- [x] `src/azure_client.py` — `POST /azure/pull-requests` (feature + aux PR)
- [x] `GET /azure/pull-requests/<pr_id>` — estado PR + build CI
- [x] `GET /tasks?ticket=` — filtro por ticket en SQLite
- [x] `callback_url` por request en `POST /run` (reemplaza `N8N_CALLBACK_URL` hardcodeado)

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
