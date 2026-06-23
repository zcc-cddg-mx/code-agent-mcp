#!/usr/bin/env bash
# Tasks — enqueue, poll, list
#
# Usage:
#   ./tasks.sh run       — enqueue a git task
#   ./tasks.sh status    — poll task status by TASK_ID
#   ./tasks.sh list      — list recent tasks
#   ./tasks.sh filter    — list tasks filtered by ticket

BASE="${BASE_URL:-http://localhost:5001}"
TOKEN="${TOKEN_AZURE:-dev-local}"
H=(-H "X-Agent-Token: $TOKEN" -H "Content-Type: application/json")

case "${1:-list}" in

  run)
    curl -s -X POST "$BASE/run" "${H[@]}" -d '{
      "repo":           "/repos/ov-arizona-backend-ecuador",
      "branch":         "feature/ZNRX_67108_renov_agosto",
      "base_branch":    "develop",
      "target":         "developer",
      "files":          ["/repos/ov-arizona-backend-ecuador/src/Renovacion.java"],
      "ticket":         "ZNRX-67108",
      "commit_message": "Migración vencimientos agosto 2026",
      "callback_url":   ""
    }' | python3 -m json.tool
    ;;

  status)
    TASK_ID="${TASK_ID:?set TASK_ID env var}"
    curl -s "$BASE/status/$TASK_ID" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  list)
    LIMIT="${LIMIT:-20}"
    curl -s "$BASE/tasks?limit=$LIMIT" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  filter)
    TICKET="${TICKET:?set TICKET env var (e.g. ZNRX-67108)}"
    curl -s "$BASE/tasks?ticket=$TICKET" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  *)
    echo "Usage: $0 {run|status|list|filter}"
    exit 1
    ;;
esac
