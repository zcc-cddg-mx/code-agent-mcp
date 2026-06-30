#!/usr/bin/env bash
# Repository registry
#
# Usage:
#   ./repos.sh register           — register a new repo by git URL
#   ./repos.sh list               — list all registered repos
#   ./repos.sh get                — get a repo by name (REPO_NAME)
#   ./repos.sh refresh            — re-inspect a repo (REPO_NAME)
#   ./repos.sh delete             — delete a repo from the registry (REPO_NAME)
#   ./repos.sh set-role           — set role of a branch (REPO_NAME, BRANCH, ROLE)

BASE="${BASE_URL:-http://localhost:5001}"
TOKEN="${TOKEN_AZURE:-dev-local}"
H=(-H "X-Agent-Token: $TOKEN" -H "Content-Type: application/json")

case "${1:-list}" in

  register)
    GIT_URL="${GIT_URL:?set GIT_URL env var}"
    LOCAL_PATH="${LOCAL_PATH:-}"
    BODY="{\"git_url\": \"$GIT_URL\""
    [ -n "$LOCAL_PATH" ] && BODY="$BODY, \"local_path\": \"$LOCAL_PATH\""
    BODY="$BODY}"
    curl -s -X POST "$BASE/repos" "${H[@]}" -d "$BODY" | python3 -m json.tool
    ;;

  list)
    curl -s "$BASE/repos" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  get)
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    curl -s "$BASE/repos/$REPO_NAME" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  refresh)
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    curl -s -X POST "$BASE/repos/$REPO_NAME/refresh" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  delete)
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    curl -s -X DELETE "$BASE/repos/$REPO_NAME" -H "X-Agent-Token: $TOKEN" | python3 -m json.tool
    ;;

  set-role)
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    BRANCH="${BRANCH:?set BRANCH env var (e.g. master)}"
    ROLE="${ROLE:?set ROLE env var: base | integration | feature | other}"
    curl -s -X PATCH "$BASE/repos/$REPO_NAME/branches/$BRANCH" "${H[@]}" \
      -d "{\"role\": \"$ROLE\"}" | python3 -m json.tool
    ;;

  set-branch-map)
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    # BRANCH_MAP must be a JSON object, e.g.: '{"developer":"developer","test":"test","prod":"develop"}'
    BRANCH_MAP="${BRANCH_MAP:?set BRANCH_MAP env var as JSON object}"
    curl -s -X PATCH "$BASE/repos/$REPO_NAME/branch-map" "${H[@]}" \
      -d "$BRANCH_MAP" | python3 -m json.tool
    ;;

  set-local-path)
    REPO_NAME="${REPO_NAME:?set REPO_NAME env var}"
    LOCAL_PATH="${LOCAL_PATH:?set LOCAL_PATH env var}"
    curl -s -X PATCH "$BASE/repos/$REPO_NAME/local-path" "${H[@]}" \
      -d "{\"local_path\":\"$LOCAL_PATH\"}" | python3 -m json.tool
    ;;

  *)
    echo "Usage: $0 {register|list|get|refresh|delete|set-role|set-branch-map|set-local-path}"
    exit 1
    ;;
esac
