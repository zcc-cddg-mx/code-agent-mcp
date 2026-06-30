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

# Configure git credentials from TOKEN_AZURE (mirrors docker-entrypoint.sh behavior)
if [[ -n "${TOKEN_AZURE:-}" ]]; then
  CRED_FILE="$HOME/.git-credentials"
  touch "$CRED_FILE" && chmod 600 "$CRED_FILE"
  # Remove stale entries for dev.azure.com then re-add with current PAT
  grep -v "dev\.azure\.com" "$CRED_FILE" > "$CRED_FILE.tmp" 2>/dev/null && mv "$CRED_FILE.tmp" "$CRED_FILE" || true
  printf "https://ZurichInsurance-EC:%s@dev.azure.com\n" "${TOKEN_AZURE}" >> "$CRED_FILE"
  printf "https://%s:%s@dev.azure.com\n" "${GIT_USERNAME:-carlos.duarte2}" "${TOKEN_AZURE}" >> "$CRED_FILE"
  git config --global credential.helper store 2>/dev/null || true
fi

PORT="${PORT:-5001}"

# Kill any process already listening on the port
if lsof -ti :"$PORT" &>/dev/null; then
  echo "Stopping existing process on port $PORT..."
  lsof -ti :"$PORT" | xargs kill -TERM 2>/dev/null || true
  sleep 1
fi

echo "Starting code-agent-mcp on port $PORT (DB: $TASKS_DB)"
exec conda run --no-capture-output -n code-agent-mcp python app.py
