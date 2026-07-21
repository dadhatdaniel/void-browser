#!/usr/bin/env bash
# Poll GitHub for the newest published release and rewrite website/releases.json.
# GitLab CI owns this path (Unraid runner can reach api.github.com; GHA cannot
# reach LAN GitLab). Used on schedule, after v* tags, and API SYNC_RELEASES=1.
#
# Optional env:
#   REPO                 — dadhatdaniel/void-browser
#   GITHUB_TOKEN         — raises rate limits (also accepts GH_WORKFLOW_TOKEN)
#   OUT                  — website/releases.json
#   TAG                  — pin a specific tag (default: newest non-draft release)
#   WAIT_ASSETS_SEC      — poll until installer assets exist (0 = no wait)
#   WAIT_ASSETS_INTERVAL — seconds between polls (default 90)

set -euo pipefail

REPO="${REPO:-dadhatdaniel/void-browser}"
OUT="${OUT:-website/releases.json}"
API="https://api.github.com/repos/${REPO}"
WAIT_ASSETS_SEC="${WAIT_ASSETS_SEC:-0}"
WAIT_ASSETS_INTERVAL="${WAIT_ASSETS_INTERVAL:-90}"

# Prefer dedicated workflow token when present (GitLab CI often sets both).
TOKEN="${GITHUB_TOKEN:-${GH_WORKFLOW_TOKEN:-}}"

auth_hdr=()
if [[ -n "$TOKEN" ]]; then
  auth_hdr=(-H "Authorization: Bearer $TOKEN")
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

fetch_release() {
  if [[ -n "${TAG:-}" ]]; then
    echo "[sync] Fetching release TAG=$TAG ..."
    code=$(curl -sS -o /tmp/gh-rel.json -w '%{http_code}' \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "${auth_hdr[@]}" \
      "$API/releases/tags/$TAG" || echo "000")
  else
    echo "[sync] Fetching newest published release (includes prereleases) ..."
    code=$(curl -sS -o /tmp/gh-rels.json -w '%{http_code}' \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "${auth_hdr[@]}" \
      "$API/releases?per_page=10" || echo "000")
    if [[ "$code" != "200" ]]; then
      echo "[sync] GitHub list releases HTTP $code" >&2
      cat /tmp/gh-rels.json >&2 || true
      return 1
    fi
    jq '[.[] | select(.draft==false)][0]' /tmp/gh-rels.json > /tmp/gh-rel.json
    TAG="$(jq -r '.tag_name // empty' /tmp/gh-rel.json)"
    code=200
  fi

  if [[ "$code" != "200" ]]; then
    echo "[sync] GitHub release HTTP $code" >&2
    cat /tmp/gh-rel.json >&2 || true
    return 1
  fi
  if [[ ! -s /tmp/gh-rel.json ]] || [[ "$(jq -r '.tag_name // empty' /tmp/gh-rel.json)" = "" ]]; then
    echo "[sync] No release found" >&2
    return 1
  fi
  TAG="$(jq -r '.tag_name' /tmp/gh-rel.json)"
  return 0
}

installer_asset_count() {
  jq '[.assets[] | select(.name | test("\\.(deb|AppImage|dmg|msi)$|setup\\.exe$|_x64-setup\\.exe$"))] | length' \
    /tmp/gh-rel.json
}

# IMPORTANT: bash single quotes preserve backslashes literally.
# Use '\.deb$' (one backslash) so jq --arg gets regex \.deb$ (literal dot).
# '\\.deb$' would pass \\.deb$ and look for a literal backslash — matching 0 files.
pick_asset() {
  local regex="$1"
  jq -r --arg re "$regex" '
    .assets[]
    | select(.name | test($re))
    | {name, url: .browser_download_url, size}
  ' /tmp/gh-rel.json | jq -s '.[0] // empty'
}

# Mapped website keys (deb/AppImage/setup.exe/dmg) — not raw installer_asset_count.
# Count alone is insufficient: pick_asset regex bugs can see N installers and map 0.
mapped_asset_count() {
  local n=0
  for re in '\.deb$' '\.AppImage$' 'setup\.exe$|_x64-setup\.exe$' '\.dmg$'; do
    if [[ -n "$(pick_asset "$re")" ]]; then
      n=$((n + 1))
    fi
  done
  echo "$n"
}

# Initial fetch + optional wait for GHA to publish installers after a tag.
elapsed=0
until fetch_release; do
  if [[ "$WAIT_ASSETS_SEC" -le 0 ]] || [[ "$elapsed" -ge "$WAIT_ASSETS_SEC" ]]; then
    echo "[sync] Release not available yet (waited ${elapsed}s)" >&2
    exit 1
  fi
  echo "[sync] Release not visible yet — sleep ${WAIT_ASSETS_INTERVAL}s (${elapsed}/${WAIT_ASSETS_SEC}s)"
  sleep "$WAIT_ASSETS_INTERVAL"
  elapsed=$((elapsed + WAIT_ASSETS_INTERVAL))
done

if [[ "$WAIT_ASSETS_SEC" -gt 0 ]]; then
  while true; do
    count="$(installer_asset_count)"
    mapped="$(mapped_asset_count)"
    if [[ "$count" -gt 0 && "$mapped" -gt 0 ]]; then
      echo "[sync] Release $TAG has $count installer asset(s) ($mapped mapped for website)"
      break
    fi
    if [[ "$elapsed" -ge "$WAIT_ASSETS_SEC" ]]; then
      echo "[sync] ERROR: release $TAG still has no mappable installer assets after ${elapsed}s (raw=$count mapped=$mapped)" >&2
      exit 1
    fi
    echo "[sync] Waiting for installer assets on $TAG — sleep ${WAIT_ASSETS_INTERVAL}s (${elapsed}/${WAIT_ASSETS_SEC}s raw=$count mapped=$mapped)"
    sleep "$WAIT_ASSETS_INTERVAL"
    elapsed=$((elapsed + WAIT_ASSETS_INTERVAL))
    fetch_release || true
  done
fi

VERSION="${TAG#v}"
PUBLISHED="$(jq -r '.published_at // .created_at' /tmp/gh-rel.json | cut -c1-10)"

size_label() {
  local bytes="${1:-0}"
  if [[ "$bytes" -le 0 ]]; then
    echo "~?"
    return 0
  fi
  local mb=$(( (bytes + 512000) / 1000000 ))
  if [[ "$mb" -lt 1 ]]; then
    echo "~$(( (bytes + 512) / 1024 )) KB"
  else
    echo "~${mb} MB"
  fi
}

add_from_pick() {
  local key="$1"
  local json="$2"
  [[ -z "$json" || "$json" = "null" ]] && return 0
  local name url bytes
  name="$(echo "$json" | jq -r '.name')"
  url="$(echo "$json" | jq -r '.url')"
  bytes="$(echo "$json" | jq -r '.size')"
  jq --arg key "$key" --arg name "$name" --arg url "$url" --arg size "$(size_label "$bytes")" \
    '.assets[$key] = {name: $name, url: $url, size_label: $size}' \
    "$OUT" > "${OUT}.tmp"
  mv "${OUT}.tmp" "$OUT"
  echo "[sync] + $key → $name"
}

mkdir -p "$(dirname "$OUT")"

jq -n \
  --arg tag "$TAG" \
  --arg version "$VERSION" \
  --arg released "$PUBLISHED" \
  --arg page "https://github.com/${REPO}/releases/tag/${TAG}" \
  --arg latest_page "https://github.com/${REPO}/releases/latest" \
  '{
    tag: $tag,
    version: $version,
    released: $released,
    page: $page,
    latest_page: $latest_page,
    assets: {}
  }' > "$OUT"

# IMPORTANT: bash single quotes preserve backslashes literally.
# Use '\.deb$' (one backslash) so jq --arg gets regex \.deb$ (literal dot).
# '\\.deb$' would pass \\.deb$ and look for a literal backslash — matching 0 files.
add_from_pick "linux_deb" "$(pick_asset '\.deb$')"
add_from_pick "linux_appimage" "$(pick_asset '\.AppImage$')"
add_from_pick "windows_exe" "$(pick_asset 'setup\.exe$|_x64-setup\.exe$')"
add_from_pick "macos_dmg" "$(pick_asset '\.dmg$')"

COUNT="$(jq '.assets | length' "$OUT")"
echo "[sync] wrote $OUT for $TAG ($COUNT asset(s))"
jq . "$OUT"

if [[ "$COUNT" -eq 0 ]]; then
  echo "[sync] ERROR: release $TAG has no installer assets" >&2
  exit 1
fi
