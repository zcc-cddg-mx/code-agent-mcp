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
| `GET` | `/projects` | Listar proyectos Azure DevOps (con sus repos) |
| `GET` | `/projects/<org>/<name>` | Obtener proyecto por slug |
| `GET` | `/config/branches` | Ver diccionario de ramas |
| `PUT` | `/config/branches` | Actualizar diccionario de ramas |
| `POST` | `/azure/pull-requests` | Crear PR feature + PR auxiliar simultáneamente |
| `GET` | `/azure/pull-requests/<pr_id>` | Estado PR + build CI |

Scripts de referencia en `apis/`:

```bash
./apis/health.sh
./apis/repos.sh   register|list|get|refresh|delete
./apis/projects.sh list|get
./apis/tasks.sh   run|status|list|filter
./apis/config.sh  get|update
./apis/azure.sh   create|status
```

## Variables de entorno

Ver `.env.example`. Las obligatorias para arrancar:

```
AGENT_TOKEN=      # secreto compartido con claude-mcp-jira
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

## Registro de repositorios y proyectos

Al registrar un repositorio, el servicio:
1. Parsea la URL de Azure DevOps
2. Consulta la API de Azure DevOps para obtener metadata (nombre, default branch, proyecto)
3. Ejecuta `git ls-remote` para descubrir ramas sin clonar
4. Clasifica ramas en integración / feature / other
5. Persiste repo y proyecto en SQLite (tabla `repos` + tabla `projects`)

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
tests/                  — pytest (57 tests)
arch/                   — diseño y plan de integración
```

## Repositorios relacionados

- `claude-mcp-jira` (`/home/idavid/dev/claude/claude-mcp-jira`) — orquestador que consume este servicio
- `ov-arizona-backend-ecuador` (`/home/idavid/dev/ov/ov-arizona-backend-ecuador`) — repo git de destino principal
- `ov-suscripcion-automation` (`/home/idavid/dev/ov/ov-suscripcion-automation`) — fuente de infraestructura copiada
