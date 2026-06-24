#!/usr/bin/env bash
# Azure DevOps — PR creation and status
#
# Usage:
#   ./azure.sh preview        — dry-run: detect base branch + files, no side effects
#   ./azure.sh prepare-and-pr — ensure aux branch exists/updated, create aux PR only
#   ./azure.sh create         — create feature PR + aux PR (legacy)
#   ./azure.sh status         — get PR status + CI build status (PR_ID, REPO_NAME)

BASE="${BASE_URL:-http://localhost:5001}"
TOKEN="${TOKEN_AZURE:-dev-local}"
H=(-H "X-Agent-Token: $TOKEN" -H "Content-Type: application/json")

case "${1:-status}" in

  preview)
    REPO="${REPO:?set REPO env var (Azure DevOps repo name)}"
    REPO_PATH="${REPO_PATH:?set REPO_PATH env var (absolute local path to git clone)}"
    BRANCH="${BRANCH:?set BRANCH env var (feature branch)}"
    TARGET="${TARGET:-test}"
    curl -s -X POST "$BASE/azure/prepare-and-pr/preview" "${H[@]}" \
      -d "{
        \"repo\":      \"$REPO\",
        \"repo_path\": \"$REPO_PATH\",
        \"branch\":    \"$BRANCH\",
        \"target\":    \"$TARGET\"
      }" | python3 -m json.tool
    ;;

  prepare-and-pr)
    REPO="${REPO:?set REPO env var (Azure DevOps repo name)}"
    REPO_PATH="${REPO_PATH:?set REPO_PATH env var (absolute local path to git clone)}"
    BRANCH="${BRANCH:?set BRANCH env var (feature branch)}"
    TARGET="${TARGET:-test}"
    TICKET="${TICKET:?set TICKET env var}"
    TITLE="${TITLE:?set TITLE env var}"
    DESCRIPTION="${DESCRIPTION:-Generado automáticamente por code-agent-mcp}"
    curl -s -X POST "$BASE/azure/prepare-and-pr" "${H[@]}" \
      -d "{
        \"repo\":        \"$REPO\",
        \"repo_path\":   \"$REPO_PATH\",
        \"branch\":      \"$BRANCH\",
        \"target\":      \"$TARGET\",
        \"ticket\":      \"$TICKET\",
        \"title\":       \"$TITLE\",
        \"description\": \"$DESCRIPTION\"
      }" | python3 -m json.tool
    ;;

  create)
    # Adjust repo, branch, aux_branch, title, target as needed
    curl -s -X POST "$BASE/azure/pull-requests" "${H[@]}" -d '{
      "repo":        "ov-arizona-backend-ecuador",
      "branch":      "feature/ZNRX_67108_renov_agosto",
      "aux_branch":  "feature/ZNRX_67108_renov_agosto_developer_auxiliar",
      "title":       "ZNRX-67108 Migración vencimientos agosto 2026",
      "description": "Generado automáticamente por code-agent-mcp",
      "target":      "developer"
    }' | python3 -m json.tool
    ;;

  status)
    PR_ID="${PR_ID:?set PR_ID env var}"
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    curl -s "$BASE/azure/pull-requests/$PR_ID?repo=$REPO_NAME" \
      -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  *)
    echo "Usage: $0 {preview|prepare-and-pr|create|status}"
    exit 1
    ;;
esac
