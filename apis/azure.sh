#!/usr/bin/env bash
# Azure DevOps — PR creation and status
#
# Usage:
#   ./azure.sh create    — create feature PR + aux PR
#   ./azure.sh status    — get PR status + CI build status (PR_ID, REPO_NAME)

BASE="${BASE_URL:-http://localhost:5001}"
TOKEN="${AGENT_TOKEN:-dev-local}"
H=(-H "X-Agent-Token: $TOKEN" -H "Content-Type: application/json")

case "${1:-status}" in

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
    echo "Usage: $0 {create|status}"
    exit 1
    ;;
esac
