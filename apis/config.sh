#!/usr/bin/env bash
# Branch config registry
#
# Usage:
#   ./config.sh get      — show active branch registry
#   ./config.sh update   — replace/merge branch registry entries

BASE="${BASE_URL:-http://localhost:5001}"
TOKEN="${TOKEN_AZURE:-dev-local}"
H=(-H "X-Agent-Token: $TOKEN" -H "Content-Type: application/json")

case "${1:-get}" in

  get)
    curl -s "$BASE/config/branches" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  update)
    # Edit the JSON body below to override specific branch entries.
    # Unknown keys are merged with defaults; existing entries not in the body are preserved.
    curl -s -X PUT "$BASE/config/branches" "${H[@]}" -d '{
      "developer": {"label": "desarrollo",      "environment": "DEV-UAT"},
      "test":      {"label": "pruebas",          "environment": "Preprod"},
      "develop":   {"label": "produccion-pre",   "environment": "Produccion-Pre", "is_base": true},
      "main":      {"label": "produccion",       "environment": "Produccion"}
    }' | python3 -m json.tool
    ;;

  *)
    echo "Usage: $0 {get|update}"
    exit 1
    ;;
esac
