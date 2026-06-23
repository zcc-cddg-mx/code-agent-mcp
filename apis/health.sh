#!/usr/bin/env bash
# GET /health — liveness check (no token required)

BASE="${BASE_URL:-http://localhost:5001}"

curl -s "$BASE/health" | python3 -m json.tool
