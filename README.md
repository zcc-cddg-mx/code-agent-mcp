# code-agent-mcp

Generic HTTP agent that executes git operations and creates Azure DevOps pull requests on behalf of an orchestrator (`claude-mcp-jira`). Python/Flask service — all domain logic (file generation, Jira interaction) lives in the caller.

## Quick start

```bash
# Development
./run_local.sh

# Tests
conda activate code-agent-mcp
pytest tests/
```

## API

Swagger UI disponible en `http://localhost:5001/apidocs/` cuando el servidor esté corriendo.

Todos los endpoints requieren el header `X-Agent-Token`.

| Method | Path | Descripción |
|---|---|---|
| `GET` | `/health` | Liveness (sin token) |
| `POST` | `/run` | Encolar tarea git (branch + commit + push + aux branch) → 202 |
| `GET` | `/status/<task_id>` | Consultar estado de tarea |
| `GET` | `/tasks` | Tareas recientes (`?ticket=ZNRX-123`, `?limit=50`) |
| `POST` | `/repos` | Registrar repositorio + inspección inmediata |
| `GET` | `/repos` | Listar repositorios registrados |
| `GET` | `/repos/<name>` | Obtener repositorio por nombre |
| `POST` | `/repos/<name>/refresh` | Re-inspeccionar repositorio |
| `DELETE` | `/repos/<name>` | Eliminar del registro |
| `PATCH` | `/repos/<name>/branches/<branch>` | Corregir rol de una rama (`base`/`integration`/`feature`/`other`) |
| `GET` | `/projects` | Listar proyectos Azure DevOps (con sus repos) |
| `GET` | `/projects/<org>/<name>` | Obtener proyecto por slug |
| `GET` | `/config/branches` | Ver diccionario de ramas |
| `PUT` | `/config/branches` | Actualizar diccionario de ramas |
| `POST` | `/azure/prepare-and-pr/preview` | Dry-run: detectar rama base y archivos sin crear nada |
| `POST` | `/azure/prepare-and-pr` | Verificar/crear aux branch + crear PR auxiliar (idempotente) |
| `POST` | `/azure/pull-requests` | Crear PR feature + PR auxiliar simultáneamente (legacy) |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado PR + build CI |
| `PATCH` | `/azure/pull-requests/<pr_id>` | Completar / abandonar / reactivar PR |
| `GET` | `/prs` | Listar PRs registrados (`?repo=`, `?status=`, `?task_id=`, `?limit=`) |
| `GET` | `/prs/<pr_id>` | PR con estado refrescado desde Azure DevOps |

Scripts de referencia en `apis/`:

```bash
./apis/health.sh
./apis/repos.sh    register|list|get|refresh|delete|set-role
./apis/projects.sh list|get
./apis/tasks.sh    run|status|list|filter
./apis/config.sh   get|update
./apis/azure.sh    prepare-and-pr|create|status
```

## Variables de entorno

Ver `.env.example`. Las obligatorias para arrancar:

```
TOKEN_AZURE=      # secreto compartido con claude-mcp-jira
AZURE_PAT=        # Personal Access Token de Azure DevOps
AZURE_ORG=        # Organización Azure DevOps (ej. ZurichInsurance-EC)
AZURE_PROJECT=    # Proyecto default para PR creation
TASKS_DB=         # Path al archivo SQLite (/data/tasks.db en Docker, /tmp/... en local)
```

## Flujo git

Las feature/fix se cortan desde `develop`. La rama auxiliar se crea desde `origin/<target>` con sufijo `_{target}_auxiliar`.

Ejemplo (PR-2505):
- Feature: `feature/RITM2521020_relatividades_junio` (desde `develop`)
- Aux: `feature/RITM2521020_relatividades_junio_test_auxiliar` (desde `origin/test`)
- PR: aux → `test`

El diccionario de ramas define cuáles son de integración y cuál es la base para cortar features:

| Rama | Label | Rol |
|---|---|---|
| `developer` | desarrollo | integración DEV-UAT |
| `test` | pruebas | integración Preprod |
| `develop` | producción-pre | **base para features** |
| `main` | producción | producción desplegada |

## Preview de cambios (dry-run)

`POST /azure/prepare-and-pr/preview` detecta la rama base y los archivos que se integrarán **sin crear nada** — sin rama auxiliar, sin PR. Útil para mostrar una confirmación al usuario antes de ejecutar.

```
POST /azure/prepare-and-pr/preview
{
  "repo":      "ov-arizona-frontend-ecuador",
  "repo_path": "/ruta/local/al/repo",
  "branch":    "feature/test_mcp_jira_multifile",
  "target":    "test"
}
```

Respuesta:
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

`existing_pr: null` — el PR no existe aún; `prepare-and-pr` lo creará.
`existing_pr: {...}` — ya hay PR activo; `prepare-and-pr` lo devolverá sin duplicar.

## Flujo de PR auxiliar (recomendado)

`POST /azure/prepare-and-pr` es el endpoint principal para crear PRs. Es idempotente: se puede llamar varias veces con los mismos datos sin efectos secundarios.

**Campos requeridos:** `repo`, `repo_path`, `branch`, `target`, `ticket`, `title`
**Campos opcionales:** `files`, `base_branch`, `description`

Si `files` no se envía, el agente detecta automáticamente los archivos cambiados usando `git diff --name-only origin/{base_branch}...origin/{branch}`.

Si tampoco se envía `base_branch`, el agente lo infiere comparando el historial de la rama con todas las ramas registradas en el repo (primero las de rol `base`, luego `integration`) y eligiendo la ancestro más cercana via `git merge-base`.

```
POST /azure/prepare-and-pr
{
  "repo":       "ov-arizona-backend-ecuador",
  "repo_path":  "/ruta/local/al/repo",
  "branch":     "feature/ZNRX_67108_renov_agosto",
  "target":     "test",
  "ticket":     "ZNRX-67108",
  "title":      "ZNRX-67108 Renovaciones agosto → test"
}
```

Para pasar los archivos explícitamente (opcional):
```json
{
  "files": ["/ruta/local/al/repo/src/File.java"]
}
```

Respuesta:
```json
{
  "aux_branch":     "feature/ZNRX_67108_renov_agosto_test_auxiliar",
  "action":         "created",
  "files_detected": ["/ruta/local/al/repo/src/File.java"],
  "pr":             {"pr_id": 2554, "pr_url": "https://dev.azure.com/..."}
}
```

`action` indica qué ocurrió:
- `created` — rama auxiliar no existía; se creó desde `origin/{target}`
- `updated` — rama existía pero tenía archivos desactualizados; se aplicaron los cambios
- `unchanged` — rama y PR ya existían; se devuelve el PR sin crear duplicado

`base_branch` indica qué rama base se usó (inferida o explícita). `files_detected` lista los archivos que se integraron (detectados automáticamente o pasados explícitamente).

## Registro de repositorios y proyectos

Al registrar un repositorio, el servicio:
1. Parsea la URL de Azure DevOps
2. Consulta la API de Azure DevOps para obtener metadata (nombre, default branch, proyecto)
3. Ejecuta `git ls-remote` para descubrir ramas sin clonar
4. Clasifica ramas en integración / feature / other
5. Auto-asigna un **rol** a cada rama: `base`, `integration`, `feature`, u `other`
6. Persiste repo y proyecto en SQLite (tabla `repos` + tabla `projects`)

Los roles se almacenan por repo en `branch_roles` y se pueden corregir con `PATCH /repos/<name>/branches/<branch>`. La respuesta de `GET /repos/<name>` incluye además `branches_by_role` (inverso computado):

```json
"branches_by_role": {
  "base":        ["develop"],
  "integration": ["developer", "test", "main"]
}
```

El proyecto se upserta automáticamente — dos repos del mismo proyecto comparten un único registro.

## Arquitectura

```
app.py                  — Flask entry point, todos los endpoints, Swagger (flasgger)
run_local.sh            — script de arranque para desarrollo local
src/
  auth.py               — X-Agent-Token header validation → 401
  task_store.py         — SQLite: tabla tasks (async task pattern)
  repo_store.py         — SQLite: tabla repos
  project_store.py      — SQLite: tabla projects
  branch_config.py      — diccionario dinámico de ramas (con hot-reload)
  repo_inspector.py     — parsea URLs Azure DevOps, git ls-remote, clasifica ramas
  placer.py             — operaciones git: branch, commit, push, aux branch
  azure_client.py       — Azure DevOps REST API v7.1: PR create + status
  logger.py             — structured logging
apis/                   — scripts curl de referencia por dominio
tests/                  — pytest (124 tests)
arch/                   — diseño y plan de integración
```

## Repositorios relacionados

- `claude-mcp-jira` (`/home/idavid/dev/claude/claude-mcp-jira`) — orquestador que consume este servicio
- `ov-arizona-backend-ecuador` (`/home/idavid/dev/ov/ov-arizona-backend-ecuador`) — repo git de destino principal
- `ov-suscripcion-automation` (`/home/idavid/dev/ov/ov-suscripcion-automation`) — fuente de infraestructura copiada
