#!/usr/bin/env bash
# Project registry
#
# Usage:
#   ./projects.sh list   — list all registered projects (with their repos)
#   ./projects.sh get    — get a project by slug (PROJECT_ID = org/name)

BASE="${BASE_URL:-http://localhost:5001}"
TOKEN="${AGENT_TOKEN:-dev-local}"

case "${1:-list}" in

  list)
    curl -s "$BASE/projects" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  get)
    PROJECT_ID="${PROJECT_ID:?set PROJECT_ID env var (e.g. ZurichInsurance-EC/Ensurance-ZEC)}"
    curl -s "$BASE/projects/$PROJECT_ID" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  *)
    echo "Usage: $0 {list|get}"
    exit 1
    ;;
esac
