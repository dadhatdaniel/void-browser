#!/usr/bin/env bash
# Poll GitHub for the newest published release and rewrite website/releases.json.
# Used by GitLab CI (schedule / manual / API trigger) as a durable fallback when
# the GitHub release job cannot commit back to GitLab.
#
# Optional env:
#   REPO          — dadhatdaniel/void-browser
#   GITHUB_TOKEN  — raises rate limits
#   OUT           — website/releases.json
#   TAG           — pin a specific tag (default: newest non-draft release)

set -euo pipefail

REPO="${REPO:-dadhatdaniel/void-browser}"
OUT="${OUT:-website/releases.json}"
API="https://api.github.com/repos/${REPO}"

auth_hdr=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  auth_hdr=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

if [[ -n "${TAG:-}" ]]; then
  echo "[sync] Using pinned TAG=$TAG"
  code=$(curl -sS -o /tmp/gh-rel.json -w '%{http_code}' \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${auth_hdr[@]}" \
    "$API/releases/tags/$TAG")
else
  echo "[sync] Fetching newest published release (includes prereleases) ..."
  code=$(curl -sS -o /tmp/gh-rels.json -w '%{http_code}' \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${auth_hdr[@]}" \
    "$API/releases?per_page=10")
  if [[ "$code" != "200" ]]; then
    echo "[sync] GitHub list releases HTTP $code" >&2
    cat /tmp/gh-rels.json >&2 || true
    exit 1
  fi
  jq '[.[] | select(.draft==false)][0]' /tmp/gh-rels.json > /tmp/gh-rel.json
  TAG="$(jq -r '.tag_name' /tmp/gh-rel.json)"
fi

if [[ "$code" != "200" && ! -s /tmp/gh-rel.json ]]; then
  echo "[sync] GitHub release HTTP $code" >&2
  exit 1
fi

# If we fetched by tag above and got non-200:
if [[ ! -s /tmp/gh-rel.json ]] || [[ "$(jq -r '.tag_name // empty' /tmp/gh-rel.json)" = "" ]]; then
  echo "[sync] No release found" >&2
  exit 1
fi

TAG="$(jq -r '.tag_name' /tmp/gh-rel.json)"
VERSION="${TAG#v}"
PUBLISHED="$(jq -r '.published_at // .created_at' /tmp/gh-rel.json | cut -c1-10)"

pick_asset() {
  local regex="$1"
  jq -r --arg re "$regex" '
    .assets[]
    | select(.name | test($re))
    | {name, url: .browser_download_url, size}
  ' /tmp/gh-rel.json | jq -s '.[0] // empty'
}

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

add_from_pick "linux_deb" "$(pick_asset '\\.deb$')"
add_from_pick "linux_appimage" "$(pick_asset '\\.AppImage$')"
add_from_pick "windows_exe" "$(pick_asset 'setup\\.exe$|_x64-setup\\.exe$')"
add_from_pick "macos_dmg" "$(pick_asset '\\.dmg$')"

COUNT="$(jq '.assets | length' "$OUT")"
echo "[sync] wrote $OUT for $TAG ($COUNT asset(s))"
jq . "$OUT"

if [[ "$COUNT" -eq 0 ]]; then
  echo "[sync] ERROR: release $TAG has no installer assets" >&2
  exit 1
fi
