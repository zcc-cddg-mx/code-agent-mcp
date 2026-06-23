#!/usr/bin/env bash
# tests/curl/azure.sh — smoke tests manuales contra la Azure DevOps REST API
#
# Uso:
#   source .env.local          # exporta AZURE_PAT, AZURE_USERNAME
#   bash tests/curl/azure.sh
#
# Variables requeridas:
#   AZURE_PAT   — Personal Access Token (scope: Code Read & Write, Pull Request)
#
# Variables opcionales:
#   BASE_URL    — URL del code-agent-mcp  (default: http://localhost:5000)
#   TOKEN_AZURE — token X-Agent-Token para los endpoints del agente

set -euo pipefail

PAT="${AZURE_PAT:?AZURE_PAT is required}"
BASE_URL="${BASE_URL:-http://localhost:5000}"

ORG="ZurichInsurance-EC"
PROJECT="Oficina-Virtual-ZEC"
REPO="ov-arizona-backend-ecuador"

# Azure DevOps usa Basic auth: base64(:<PAT>)
AUTH_HEADER="Authorization: Basic $(echo -n ":${PAT}" | base64 -w 0)"
AZURE_BASE="https://dev.azure.com/${ORG}/${PROJECT}/_apis/git"

sep() { echo ""; echo "─── $* ───"; }

# ─────────────────────────────────────────────────────────────────────────────
# Azure DevOps API directa (debug / exploración)
# ─────────────────────────────────────────────────────────────────────────────

sep "GET repositories — lista todos los repos del proyecto"
curl -s \
  "${AZURE_BASE}/repositories?api-version=7.1" \
  -H "${AUTH_HEADER}" \
  -H "Content-Type: application/json" \
  | jq '[.value[] | {name, defaultBranch, webUrl}]'

sep "GET pull-requests — PRs abiertos en ${REPO}"
curl -s \
  "${AZURE_BASE}/repositories/${REPO}/pullrequests?searchCriteria.status=active&api-version=7.1" \
  -H "${AUTH_HEADER}" \
  | jq '[.value[] | {pullRequestId, title, status, createdBy: .createdBy.displayName}]'

sep "GET pull-request por ID (ajusta el ID)"
PR_ID="${PR_ID:-1}"
curl -s \
  "${AZURE_BASE}/repositories/${REPO}/pullrequests/${PR_ID}?api-version=7.1" \
  -H "${AUTH_HEADER}" \
  | jq '{pullRequestId, title, status, sourceRefName, targetRefName}'

sep "GET statuses de un PR (build CI)"
curl -s \
  "${AZURE_BASE}/repositories/${REPO}/pullrequests/${PR_ID}/statuses?api-version=7.1" \
  -H "${AUTH_HEADER}" \
  | jq '[.value[] | {state, description, context: .context.name}]'

# ─────────────────────────────────────────────────────────────────────────────
# Endpoints del code-agent-mcp
# ─────────────────────────────────────────────────────────────────────────────

if [[ -z "${TOKEN_AZURE:-}" ]]; then
  echo ""
  echo "TOKEN_AZURE no definido — saltando tests del agente"
  exit 0
fi

TOKEN_HEADER="X-Agent-Token: ${TOKEN_AZURE}"

sep "POST /azure/pull-requests — crear feature PR + aux PR"
curl -s -w "\nHTTP %{http_code}" \
  -X POST "${BASE_URL}/azure/pull-requests" \
  -H "${TOKEN_HEADER}" \
  -H "Content-Type: application/json" \
  -d "{
    \"branch\":      \"feature/ZNRX_67108_renov_agosto\",
    \"aux_branch\":  \"feature/ZNRX_67108_renov_agosto_developer_auxiliar\",
    \"title\":       \"ZNRX-67108 — Migración vencimientos agosto 2026\",
    \"description\": \"Datos de renovación motor, 1342 registros\",
    \"repo\":        \"${REPO}\",
    \"target\":      \"developer\"
  }" | jq .

sep "GET /azure/pull-requests/<pr_id> — estado PR + build"
curl -s -w "\nHTTP %{http_code}" \
  "${BASE_URL}/azure/pull-requests/${PR_ID}?repo=${REPO}" \
  -H "${TOKEN_HEADER}" | jq .

sep "POST /azure/pull-requests — sin token (espera 401)"
curl -s -w "\nHTTP %{http_code}" \
  -X POST "${BASE_URL}/azure/pull-requests" \
  -H "Content-Type: application/json" \
  -d '{"branch":"x","aux_branch":"x","title":"x","repo":"x"}' | jq .

sep "POST /azure/pull-requests — campos faltantes (espera 400)"
curl -s -w "\nHTTP %{http_code}" \
  -X POST "${BASE_URL}/azure/pull-requests" \
  -H "${TOKEN_HEADER}" \
  -H "Content-Type: application/json" \
  -d '{"branch": "feat/x"}' | jq .
