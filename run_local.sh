#!/usr/bin/env bash
# Arranque local — carga .env.local y sobreescribe TASKS_DB para desarrollo
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env.local ]]; then
  echo "ERROR: .env.local not found. Copy .env.example and fill in credentials." >&2
  exit 1
fi

set -a
source .env.local
set +a

# Override para desarrollo local (no Docker)
export TASKS_DB="${TASKS_DB_LOCAL:-/tmp/code-agent-mcp.db}"

echo "Starting code-agent-mcp on port ${PORT:-5001} (DB: $TASKS_DB)"
exec conda run --no-capture-output -n code-agent-mcp python app.py
