# Azure DevOps REST API — referencia para code-agent-mcp

Organización: `ZurichInsurance-EC`  
Proyecto: `Oficina-Virtual-ZEC`  
API version: `7.1`  
Base URL: `https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_apis/git`

Autenticación: Basic auth con PAT — `Authorization: Basic base64(:<PAT>)`

---

## Repositorios disponibles

Respuesta real de `GET /repositories?api-version=7.1`:

| Nombre | Rama por defecto | ID |
|---|---|---|
| `ov-arizona-backend-ecuador` | `developer` | `ba5cfbbc-d623-46d7-98b6-005e17683a28` |
| `ov-arizona-core` | `develop` | `3cde9636-f45d-4a2e-8b91-6642334aef11` |
| `ov-arizona-frontend-core` | `develop` | `af1c0a9e-a1e8-499b-ae35-5f47c7655fce` |
| `ov-arizona-frontend-ecuador` | `developer` | `12853df0-5ac0-485d-81b0-252d61479d2a` |
| `ov-arizona-restat` | `develop` | `dbaf2c53-87de-49be-a085-c8eb508e1a35` |
| `ov-arizona-scripts-ecuador` | `develop` | `730039ba-9a12-4a78-b73e-abcc4db1405b` |
| `ov-base-images` | `main` | `08993b0a-0c0b-4c78-9e41-d1cc70f8a9d0` |
| `ov-code-agent` | `main` | `3551561d-dfc6-421c-becf-f11c4481ada3` |
| `ov-qa-agent` | `main` | `2d1d8706-3762-4c5a-b042-5d4db67c6d9a` |
| `ov-virtual-office-connector` | `developer` | `a7cec222-4d24-4185-8bc8-e74501428b11` |
| `ov-zec-handover` | `developer` | `a531c08e-acd3-42ce-88e7-35e536e8649f` |

El repo objetivo de este agente es **`ov-arizona-backend-ecuador`** (rama base: `developer`).

---

## Crear Pull Request

```
POST /repositories/{repo}/pullrequests?api-version=7.1
```

**Body:**
```json
{
  "sourceRefName": "refs/heads/feature/ZNRX_67108_renov_agosto",
  "targetRefName": "refs/heads/developer",
  "title":        "ZNRX-67108 — Migración vencimientos agosto 2026",
  "description":  "Datos de renovación motor, 1342 registros"
}
```

**Response 201:**
```json
{
  "pullRequestId": 123,
  "title":         "ZNRX-67108 — Migración vencimientos agosto 2026",
  "status":        "active",
  "sourceRefName": "refs/heads/feature/ZNRX_67108_renov_agosto",
  "targetRefName": "refs/heads/developer",
  "url":           "https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_apis/git/repositories/ba5cfbbc-.../pullRequests/123"
}
```

La URL navegable del PR es:
```
https://dev.azure.com/ZurichInsurance-EC/Oficina-Virtual-ZEC/_git/{repo}/pullrequest/{pr_id}
```

**Errores comunes:**

| Código | Causa |
|---|---|
| `400` / `TF401179` | La rama source no existe en origin — hay que hacer push antes de crear el PR |
| `400` / `TF401178` | PR duplicado: ya existe un PR activo con la misma source → target |
| `401` | PAT inválido o expirado |
| `403` | PAT sin permisos de Code Write o Pull Request |

---

## Consultar estado de un PR

```
GET /repositories/{repo}/pullrequests/{pr_id}?api-version=7.1
```

**Response:**
```json
{
  "pullRequestId": 123,
  "status":        "active",
  "title":         "ZNRX-67108 — Migración vencimientos agosto 2026",
  "sourceRefName": "refs/heads/feature/ZNRX_67108_renov_agosto",
  "targetRefName": "refs/heads/developer",
  "createdBy": {
    "displayName": "carlos.duarte2"
  }
}
```

Valores posibles de `status`: `active` | `completed` | `abandoned`

---

## Consultar estado del build (statuses)

```
GET /repositories/{repo}/pullrequests/{pr_id}/statuses?api-version=7.1
```

**Response:**
```json
{
  "value": [
    {
      "state":       "succeeded",
      "description": "Build completado sin errores",
      "context": {
        "name":  "ov-arizona-backend-ecuador-CI",
        "genre": "continuous-integration"
      }
    }
  ],
  "count": 1
}
```

Valores posibles de `state`: `pending` | `succeeded` | `failed` | `error` | `notApplicable` | `notSet`

El agente normaliza estos valores al contrato del endpoint `GET /azure/pull-requests/<pr_id>`:

| Azure `state` | `build_status` del agente |
|---|---|
| `succeeded` | `succeeded` |
| `failed` / `error` | `failed` |
| `pending` | `pending` |
| sin statuses | `pending` |
| `notApplicable` / `notSet` | `unknown` |

---

## Smoke tests

Ver `tests/curl/azure.sh` — requiere `AZURE_PAT` en el entorno:

```bash
source .env.local
bash tests/curl/azure.sh
```

Para testear también los endpoints del agente, añadir `TOKEN_AZURE`:

```bash
source .env.local
TOKEN_AZURE=changeme PR_ID=123 bash tests/curl/azure.sh
```
