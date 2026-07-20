#!/usr/bin/env bash
# Build website/releases.json from a GitHub Release tag + local artifacts.
#
# Required env:
#   TAG  — git tag (e.g. v0.1.0-alpha.16)
#
# Optional:
#   REPO          — GitHub owner/name (default: dadhatdaniel/void-browser)
#   ARTIFACTS_DIR — local artifacts dir (default: artifacts)
#   OUT           — output path (default: website/releases.json)
#   GITHUB_TOKEN  — optional; raises API rate limits when resolving asset URLs

set -euo pipefail

TAG="${TAG:?TAG required}"
REPO="${REPO:-dadhatdaniel/void-browser}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-artifacts}"
OUT="${OUT:-website/releases.json}"
VERSION="${TAG#v}"
RELEASED="$(date -u +"%Y-%m-%d")"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

# GitHub Releases stores asset names with spaces replaced by '.'
github_asset_name() {
  local name="$1"
  echo "${name// /.}"
}

find_first() {
  local pattern="$1"
  find "$ARTIFACTS_DIR" -type f -name "$pattern" 2>/dev/null | head -n1 || true
}

pick_file() {
  local f
  for pattern in "$@"; do
    f="$(find_first "$pattern")"
    if [[ -n "$f" && -f "$f" ]]; then
      echo "$f"
      return 0
    fi
  done
  echo ""
}

size_label() {
  local bytes="$1"
  if [[ -z "$bytes" || "$bytes" -le 0 ]]; then
    echo "~?"
    return 0
  fi
  # Approximate MB for website badges
  local mb=$(( (bytes + 512000) / 1000000 ))
  if [[ "$mb" -lt 1 ]]; then
    echo "~$(( (bytes + 512) / 1024 )) KB"
  else
    echo "~${mb} MB"
  fi
}

asset_url() {
  local name="$1"
  echo "https://github.com/${REPO}/releases/download/${TAG}/${name}"
}

add_asset() {
  local key="$1"
  local file="$2"
  [[ -z "$file" || ! -f "$file" ]] && return 0
  local name asset bytes
  name="$(basename "$file")"
  asset="$(github_asset_name "$name")"
  bytes="$(wc -c < "$file" | tr -d ' ')"
  jq --arg key "$key" \
    --arg name "$asset" \
    --arg url "$(asset_url "$asset")" \
    --arg size "$(size_label "$bytes")" \
    '.assets[$key] = {name: $name, url: $url, size_label: $size}' \
    "$OUT" > "${OUT}.tmp"
  mv "${OUT}.tmp" "$OUT"
  echo "[website] + $key → $asset"
}

mkdir -p "$(dirname "$OUT")"

jq -n \
  --arg tag "$TAG" \
  --arg version "$VERSION" \
  --arg released "$RELEASED" \
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

add_asset "linux_deb" "$(pick_file '*amd64.deb' '*.deb')"
add_asset "linux_appimage" "$(pick_file '*amd64.AppImage' '*.AppImage')"
add_asset "windows_exe" "$(pick_file '*-setup.exe' '*_x64*.exe')"
add_asset "macos_dmg" "$(pick_file '*aarch64*.dmg' '*.dmg')"

COUNT="$(jq '.assets | length' "$OUT")"
if [[ "$COUNT" -eq 0 ]]; then
  echo "[website] WARNING: no installer assets found for $TAG" >&2
fi

echo "[website] wrote $OUT ($COUNT asset(s))"
jq . "$OUT"
