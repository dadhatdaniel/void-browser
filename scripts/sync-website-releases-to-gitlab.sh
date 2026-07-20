#!/usr/bin/env bash
# Ops-only helper: commit website/releases.json to GitLab via API.
# NOT used by GitHub Actions (GHA cannot reach LAN GitLab at 10.0.0.10).
# Production sync is GitLab CI job sync-releases-from-github — see
# docs/RELEASE_PIPELINE.md.
#
# After a GitHub Release, commit website/releases.json to GitLab main so
# deploy-site picks it up (GitLab-first source of truth), then trigger the
# post-release RPA pipeline on GitLab (Unraid → WinRM → rpa-win).
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
#   SKIP_RPA_TRIGGER    — set to 1 to skip triggering RPA_AFTER_RELEASE pipeline

set -euo pipefail

TAG="${TAG:?TAG required}"
TOKEN="${GITLAB_TOKEN:-}"
HOST="${GITLAB_HOST:-http://10.0.0.10:8929}"
PROJECT="${GITLAB_PROJECT_ID:-lightfootcloud%2Fvoid-browser}"
SRC="${RELEASES_JSON:-artifacts/releases.json}"
BRANCH="${BRANCH:-main}"
FILE_PATH="website/releases.json"
SKIP_RPA_TRIGGER="${SKIP_RPA_TRIGGER:-0}"
# GitHub-hosted runners cannot reach private LAN GitLab (e.g. 10.0.0.10).
# Keep timeouts short so a missing tunnel fails in seconds, not minutes.
CURL_OPTS=(--connect-timeout 15 --max-time 45 -sS)

# Soft-exit when GitLab is unreachable from this runner. The GitHub Release
# already published; website sync falls back to GitLab job
# sync-releases-from-github (or a manual commit on main).
soft_unreachable() {
  local why="$1"
  echo "[sync] WARN: GitLab unreachable from this runner ($why)" >&2
  echo "[sync] GitHub Release for $TAG is still valid — do NOT treat as a build failure." >&2
  echo "[sync] Fallback: run GitLab job sync-releases-from-github, or commit website/releases.json on main." >&2
  exit 0
}

trigger_post_release_pipeline() {
  if [[ "$SKIP_RPA_TRIGGER" = "1" ]]; then
    echo "[sync] SKIP_RPA_TRIGGER=1 — not starting GitLab RPA pipeline"
    return 0
  fi
  if [[ -z "$TOKEN" ]]; then
    echo "[sync] no GITLAB_TOKEN — cannot trigger RPA pipeline"
    return 0
  fi
  # Prefer letting the releases.json commit pipeline run rpa-windows (changes: rule).
  # Only POST a dedicated pipeline when we did not commit (already up to date),
  # so auto-cancel on new main pushes does not kill the RPA job mid-queue.
  if [[ "${DID_COMMIT:-0}" = "1" ]]; then
    echo "[sync] releases.json committed — rpa-windows runs on that push (website/releases.json changes)"
    return 0
  fi
  echo "[sync] Triggering GitLab pipeline (RPA_AFTER_RELEASE=1 RELEASE_TAG=$TAG) ..."
  code=$(curl "${CURL_OPTS[@]}" -o /tmp/gl-pipe.json -w '%{http_code}' \
    -X POST \
    -H "PRIVATE-TOKEN: $TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -n \
      --arg ref "$BRANCH" \
      --arg tag "$TAG" \
      '{
        ref: $ref,
        variables: [
          {key:"RPA_AFTER_RELEASE", value:"1"},
          {key:"RELEASE_TAG", value:$tag},
          {key:"SYNC_RELEASES", value:"0"}
        ]
      }')" \
    "$HOST/api/v4/projects/$PROJECT/pipeline" || true)
  echo "[sync] pipeline trigger HTTP $code"
  cat /tmp/gl-pipe.json 2>/dev/null || true
  if [[ "$code" = "000" ]]; then
    echo "[sync] WARN: could not reach GitLab to trigger RPA (HTTP 000)" >&2
    return 0
  fi
  if [[ "$code" != "201" && "$code" != "200" ]]; then
    echo "[sync] WARN: failed to trigger post-release pipeline (HTTP $code)" >&2
    return 0
  fi
  echo "[sync] Post-release RPA pipeline started"
}

DID_COMMIT=0
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
HTTP=$(curl "${CURL_OPTS[@]}" -o /tmp/gl-file.json -w '%{http_code}' \
  -H "PRIVATE-TOKEN: $TOKEN" \
  "$API?ref=$BRANCH" || true)

if [[ "$HTTP" = "000" ]]; then
  soft_unreachable "connect/timeout to $HOST"
fi

COMMIT_MSG="chore(website): sync releases.json for ${TAG}

Automated after GitHub Release ${TAG}."

if [[ "$HTTP" = "200" ]]; then
  OLD_SHA="$(jq -r '.blob_id // .content // empty' /tmp/gl-file.json)"
  if jq -e '.content' /tmp/gl-file.json >/dev/null 2>&1; then
    echo "$(jq -r '.content' /tmp/gl-file.json)" | base64 -d > /tmp/gl-old.json 2>/dev/null || true
    if [[ -f /tmp/gl-old.json ]] && cmp -s "$SRC" /tmp/gl-old.json; then
      echo "[sync] $FILE_PATH already up to date for $TAG — no commit"
      DID_COMMIT=0
      trigger_post_release_pipeline
      exit 0
    fi
  fi
  echo "[sync] Updating $FILE_PATH (HTTP 200, sha hint=${OLD_SHA:-n/a}) ..."
  code=$(curl "${CURL_OPTS[@]}" -o /tmp/gl-put.json -w '%{http_code}' \
    -X PUT \
    -H "PRIVATE-TOKEN: $TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -n \
      --arg branch "$BRANCH" \
      --arg content "$(cat "$SRC")" \
      --arg msg "$COMMIT_MSG" \
      '{branch:$branch, content:$content, commit_message:$msg, encoding:"text"}')" \
    "$API" || true)
else
  echo "[sync] Creating $FILE_PATH (HTTP $HTTP) ..."
  code=$(curl "${CURL_OPTS[@]}" -o /tmp/gl-put.json -w '%{http_code}' \
    -X POST \
    -H "PRIVATE-TOKEN: $TOKEN" \
    -H "Content-Type: application/json" \
    --data "$(jq -n \
      --arg branch "$BRANCH" \
      --arg content "$(cat "$SRC")" \
      --arg msg "$COMMIT_MSG" \
      '{branch:$branch, content:$content, commit_message:$msg, encoding:"text"}')" \
    "$API" || true)
fi

echo "[sync] GitLab API HTTP $code"
cat /tmp/gl-put.json 2>/dev/null || true
if [[ "$code" = "000" ]]; then
  soft_unreachable "write to $HOST failed (HTTP 000)"
fi
if [[ "$code" != "200" && "$code" != "201" ]]; then
  # Auth/permission errors are real failures when GitLab is reachable.
  echo "[sync] FAILED to write $FILE_PATH (HTTP $code)" >&2
  DID_COMMIT=0
  trigger_post_release_pipeline
  exit 1
fi
DID_COMMIT=1
echo "[sync] Committed $FILE_PATH for $TAG → GitLab $BRANCH (deploy-site + rpa-windows via changes rules)"
trigger_post_release_pipeline
exit 0
