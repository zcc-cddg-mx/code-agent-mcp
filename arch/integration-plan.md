# Code Agent MCP — Plan de integración con claude-mcp-jira

## Contexto

`ov-suscripcion-automation` (code-agent) es un agente especializado que genera archivos de migración
Flyway para el repositorio `ov-arizona-backend-ecuador`. Tiene lógica muy acoplada a ese dominio
(generadores `.xlsx`/`.java`, rutas Flyway, convenciones de naming específicas).

**Decisión de diseño:** no se modifica el code-agent existente. Se crea un nuevo repo
`code-agent-mcp` que toma solo la infraestructura genérica del code-agent y expone las
capacidades necesarias para acoplarse con `claude-mcp-jira` como orquestador.

---

## Qué se copia vs qué se descarta

### Se copia (infraestructura genérica)

| Módulo | Por qué |
|---|---|
| `app.py` — HTTP API Flask (POST /run, GET /status, GET /tasks, GET /health) | Patrón probado: 202 inmediato + polling + SQLite + callback |
| `src/task_store.py` — persistencia SQLite | Reutilizable sin cambios |
| `src/logger.py` — log estructurado | Reutilizable sin cambios |
| `src/placer.py` — git: branch, commit, push, auxiliar | Lógica git genérica; solo quitar las rutas hardcodeadas de Flyway |
| `src/build_check.py` — verificación de compilación | Adaptable a otros lenguajes/builds |
| Dockerfile + docker-entrypoint.sh | Base del container |
| `environment.yml` / `requirements.txt` | Dependencias Python |

### Se descarta (lógica específica de ov-suscripcion)

| Módulo | Por qué |
|---|---|
| `src/generator_ren_data.py` | Específico: migración vencimientos motor ams-policy |
| `src/generator_rules.py` | Específico: reglas de tarificación ams-rule |
| `src/java_template.py` | Específico: template Java Flyway |
| `src/description.py` | Específico: naming convention VH_ren_data_{mes} |
| `src/config.py` — `load_config()` con config.json | Reemplazar por `.env` estándar |
| `fixtures/` — LOV estáticos ams-policy/ams-rule | Específicos del dominio |
| `requirements/` — archivos Excel de negocio | Datos del dominio |
| `gradle/`, `site/` — build tools Java | Específicos del stack |

---

## Nuevo repo: `code-agent-mcp`

### Responsabilidad

Agente HTTP genérico que:
1. Recibe órdenes de `claude-mcp-jira` (o cualquier caller con token)
2. Ejecuta operaciones git sobre repositorios registrados (branch, commit, push)
3. Crea PRs en Azure DevOps
4. Reporta estado al caller (polling o callback)

**No** genera archivos de dominio — eso lo hace el caller o un submódulo especializado.
**No** conoce Jira — eso es responsabilidad exclusiva de `claude-mcp-jira`.

### Estructura propuesta

```
code-agent-mcp/
├── app.py                  — HTTP API (copiado + extendido)
├── src/
│   ├── task_store.py       — persistencia SQLite (copiado sin cambios)
│   ├── logger.py           — log estructurado (copiado sin cambios)
│   ├── placer.py           — git genérico: branch, commit, push, aux branch
│   ├── azure_client.py     — Azure DevOps REST API (nuevo)
│   └── auth.py             — validación X-Agent-Token (nuevo)
├── Dockerfile
├── .env.example
├── environment.yml
└── tests/
```

---

## Endpoints del `code-agent-mcp`

### Existentes (adaptados del code-agent)

| Método | Path | Descripción |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/run` | Ejecutar operación git: branch + archivos + commit + push; acepta `callback_url` en body |
| `GET` | `/status/<task_id>` | Estado de la tarea |
| `GET` | `/tasks` | Últimas N tareas; acepta `?ticket=ZNRX-123` para filtrar |

**Cambio en `POST /run`:** en vez de `multipart/form-data` con archivo Excel, recibe JSON con
paths de archivos ya generados por el caller, o solo instrucciones git (branch + commit message).
El body `callback_url` opcional reemplaza el `N8N_CALLBACK_URL` hardcodeado.

### Nuevos

| Método | Path | Descripción |
|---|---|---|
| `POST` | `/azure/pull-requests` | Crear PR en Azure DevOps (feature branch + aux branch) |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado del PR + estado del pipeline CI/CD |

---

## Contratos de los endpoints nuevos

### `POST /azure/pull-requests`

```json
Input:
{
  "branch":      "feature/ZNRX_67108_renov_agosto",
  "aux_branch":  "feature/ZNRX_67108_renov_agosto_developer_auxiliar",
  "title":       "ZNRX-67108 — Migración vencimientos agosto 2026",
  "description": "Datos de renovación motor, 1342 registros",
  "repo":        "ov-arizona-backend-ecuador",
  "target":      "developer"
}

Output:
{
  "feature_pr": {"pr_id": 123, "pr_url": "https://dev.azure.com/..."},
  "aux_pr":     {"pr_id": 124, "pr_url": "https://dev.azure.com/..."}
}
```

Credencial: `AZURE_PAT` + `AZURE_ORG` + `AZURE_PROJECT` en `.env`.
API: `https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}/pullrequests?api-version=7.1`

### `GET /azure/pull-requests/<pr_id>`

```json
Output:
{
  "pr_id": 123,
  "status":       "active" | "completed" | "abandoned",
  "build_status": "pending" | "succeeded" | "failed" | "unknown",
  "pr_url": "https://dev.azure.com/..."
}
```

---

## Autenticación

Todos los endpoints requieren `X-Agent-Token` header.
Variable `TOKEN_AZURE` en `.env`. Sin token → 401.

Variables de entorno del `code-agent-mcp`:

```
TOKEN_AZURE=<secreto compartido con claude-mcp-jira>
AZURE_PAT=<Personal Access Token de Azure DevOps>
AZURE_ORG=<organización Azure DevOps>
AZURE_PROJECT=<proyecto Azure DevOps>
TASKS_DB=/data/tasks.db
UPLOADS_DIR=/data/uploads
N8N_CALLBACK_URL=              # vacío — coexistencia opcional con n8n
```

---

## MCP tools a añadir en `claude-mcp-jira` (Fase 11)

| Tool | Rol mínimo | Qué hace |
|---|---|---|
| `run_code_agent` | lead | Llama `POST /run`; texto libre → Claude extrae repo/branch/archivos/mensaje |
| `get_code_agent_status` | dev | Llama `GET /status/<task_id>`; retorna estado + branch + commit_id |
| `create_azure_pull_request` | lead | Llama `POST /azure/pull-requests`; crea PR feature + aux |
| `get_pull_request_status` | dev | Llama `GET /azure/pull-requests/<pr_id>`; retorna estado PR + build |

Variables de entorno nuevas en `claude-mcp-jira`:

```
CODE_AGENT_URL=http://code-agent-mcp:5000
CODE_TOKEN_AZURE=<mismo valor que TOKEN_AZURE del code-agent-mcp>
```

---

## Flujo completo objetivo

```
1. create_jira_issue          → ZNRX-XXXXX
2. run_code_agent             → task_id  (202 inmediato)
3. get_code_agent_status      → polling → "done" → {branch, aux_branch, commit_id}
4. create_azure_pull_request  → {feature_pr, aux_pr}
5. get_pull_request_status    → esperar build verde
6. update_jira_issue          → link PR + transición "In Review"
```

---

## Orden de implementación

| Paso | Dónde | Qué |
|---|---|---|
| 1 | `code-agent-mcp` (nuevo repo) | Crear repo; copiar infraestructura base del code-agent |
| 2 | `code-agent-mcp` | Adaptar `placer.py` para git genérico (sin rutas Flyway hardcodeadas) |
| 3 | `code-agent-mcp` | `src/auth.py` + `X-Agent-Token` en todos los endpoints |
| 4 | `code-agent-mcp` | `src/azure_client.py` + `POST /azure/pull-requests` |
| 5 | `code-agent-mcp` | `GET /azure/pull-requests/<pr_id>` |
| 6 | `code-agent-mcp` | `GET /tasks?ticket=` |
| 7 | `code-agent-mcp` | `callback_url` en `POST /run` (reemplaza `N8N_CALLBACK_URL` hardcodeado) |
| 8 | `claude-mcp-jira` | `service/clients/code_agent_client.py` + MCP tools (Fase 11) |
| 9 | `claude-mcp-jira` | e2e test del flujo completo |

---

## Coexistencia con n8n y el code-agent original

- `ov-suscripcion-automation` sigue funcionando sin cambios para su dominio específico (migraciones Flyway OV)
- `code-agent-mcp` es un agente nuevo, independiente, sin dependencia del anterior
- n8n puede seguir usando `ov-suscripcion-automation` para los flujos automáticos (webhook Jira)
- `claude-mcp-jira` usa `code-agent-mcp` para flujos supervisados desde Claude Code
- La decisión de retirar n8n es independiente y no es prerequisito técnico
