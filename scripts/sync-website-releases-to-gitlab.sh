#!/usr/bin/env bash
# After a GitHub Release, commit website/releases.json to GitLab main so
# deploy-site picks it up (GitLab-first source of truth).
#
# Required env:
#   TAG           — release tag (e.g. v0.1.0-alpha.16)
#   GITLAB_TOKEN  — GitLab PAT / project token with api + write_repository
#
# Optional:
#   GITLAB_HOST         — default http://10.0.0.10:8929
#   GITLAB_PROJECT_ID   — numeric id or URL-encoded path (default lightfootcloud%2Fvoid-browser)
#   RELEASES_JSON       — path to generated file (default artifacts/releases.json)
#   BRANCH              — default main

set -euo pipefail

TAG="${TAG:?TAG required}"
TOKEN="${GITLAB_TOKEN:-}"
HOST="${GITLAB_HOST:-http://10.0.0.10:8929}"
PROJECT="${GITLAB_PROJECT_ID:-lightfootcloud%2Fvoid-browser}"
SRC="${RELEASES_JSON:-artifacts/releases.json}"
BRANCH="${BRANCH:-main}"
FILE_PATH="website/releases.json"

if [[ -z "$TOKEN" ]]; then
  echo "[sync] GITLAB_TOKEN not set — skipping GitLab commit of $FILE_PATH"
  echo "[sync] Set GitHub Environment secret GITLAB_TOKEN (api+write_repository) on 'release'"
  echo "[sync] Fallback: GitLab job sync-releases-from-github (schedule or manual)"
  exit 0
fi

if [[ ! -f "$SRC" ]]; then
  echo "[sync] missing $SRC" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

API="$HOST/api/v4/projects/$PROJECT/repository/files/$(printf '%s' "$FILE_PATH" | sed 's|/|%2F|g')"

echo "[sync] Fetching current $FILE_PATH on $BRANCH ..."
HTTP=$(curl -sS -o /tmp/gl-file.json -w '%{http_code}' \
  -H "PRIVATE-TOKEN: $TOKEN" \
  "$API?ref=$BRANCH" || true)

COMMIT_MSG="chore(website): sync releases.json for ${TAG}

Automated after GitHub Release ${TAG}."

if [[ "$HTTP" = "200" ]]; then
  OLD_SHA="$(jq -r '.blob_id // .content // empty' /tmp/gl-file.json)"
  # Compare decoded content when present
  if jq -e '.content' /tmp/gl-file.json >/dev/null 2>&1; then
    echo "$(jq -r '.content' /tmp/gl-file.json)" | base64 -d > /tmp/gl-old.json 2>/dev/null || true
    if [[ -f /tmp/gl-old.json ]] && cmp -s "$SRC" /tmp/gl-old.json; then
      echo "[sync] $FILE_PATH already up to date for $TAG — no commit"
      exit 0
    fi
  fi
  echo "[sync] Updating $FILE_PATH (HTTP 200, sha hint=${OLD_SHA:-n/a}) ..."
  code=$(curl -sS -o /tmp/gl-put.json -w '%{http_code}' \
    -X PUT \
    -H "PRIVATE-TOKEN: $TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -n \
      --arg branch "$BRANCH" \
      --arg content "$(cat "$SRC")" \
      --arg msg "$COMMIT_MSG" \
      '{branch:$branch, content:$content, commit_message:$msg, encoding:"text"}')" \
    "$API")
else
  echo "[sync] Creating $FILE_PATH (HTTP $HTTP) ..."
  code=$(curl -sS -o /tmp/gl-put.json -w '%{http_code}' \
    -X POST \
    -H "PRIVATE-TOKEN: $TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -n \
      --arg branch "$BRANCH" \
      --arg content "$(cat "$SRC")" \
      --arg msg "$COMMIT_MSG" \
      '{branch:$branch, content:$content, commit_message:$msg, encoding:"text"}')" \
    "$API")
fi

echo "[sync] GitLab API HTTP $code"
cat /tmp/gl-put.json 2>/dev/null || true
if [[ "$code" != "200" && "$code" != "201" ]]; then
  echo "[sync] FAILED to write $FILE_PATH" >&2
  exit 1
fi
echo "[sync] Committed $FILE_PATH for $TAG → GitLab $BRANCH (deploy-site should follow)"
