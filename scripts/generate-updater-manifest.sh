#!/usr/bin/env bash
# Build Tauri updater latest.json from release artifacts + .sig files.
#
# Required env:
#   TAG   — git tag (e.g. v0.1.0-alpha.6)
#   REPO  — GitHub owner/name (default: dadhatdaniel/void-browser)
#
# Optional:
#   ARTIFACTS_DIR — directory with downloaded build artifacts (default: artifacts)
#   OUT           — output path (default: latest.json)
#
# Platform keys follow https://v2.tauri.app/plugin/updater/#static-json-file

set -euo pipefail

TAG="${TAG:?TAG required}"
REPO="${REPO:-dadhatdaniel/void-browser}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-artifacts}"
OUT="${OUT:-latest.json}"
VERSION="${TAG#v}"
PUB_DATE="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
NOTES="Void Browser ${TAG}. See https://github.com/${REPO}/releases/tag/${TAG}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

find_first() {
  local pattern="$1"
  find "$ARTIFACTS_DIR" -type f -name "$pattern" 2>/dev/null | head -n1 || true
}

# Pick first existing match from a list of globs
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

read_sig() {
  local artifact="$1"
  local sig="${artifact}.sig"
  if [[ ! -f "$sig" ]]; then
    echo ""
    return 0
  fi
  tr -d '\r\n' < "$sig"
}

add_platform() {
  local key="$1"
  local file="$2"
  [[ -z "$file" || ! -f "$file" ]] && return 0
  local sig
  sig="$(read_sig "$file")"
  if [[ -z "$sig" ]]; then
    echo "[updater] skip $key — missing $(basename "$file").sig" >&2
    return 0
  fi
  local name
  name="$(basename "$file")"
  local url="https://github.com/${REPO}/releases/download/${TAG}/${name}"
  jq --arg key "$key" --arg url "$url" --arg sig "$sig" \
    '.platforms[$key] = {url: $url, signature: $sig}' \
    "$OUT" > "${OUT}.tmp"
  mv "${OUT}.tmp" "$OUT"
  echo "[updater] + $key → $name"
}

jq -n \
  --arg version "$VERSION" \
  --arg notes "$NOTES" \
  --arg pub_date "$PUB_DATE" \
  '{version: $version, notes: $notes, pub_date: $pub_date, platforms: {}}' \
  > "$OUT"

# Linux — AppImage is the updater target when createUpdaterArtifacts=true
add_platform "linux-x86_64" "$(pick_file '*amd64.AppImage' '*x86_64.AppImage' '*.AppImage')"

# Windows — NSIS setup.exe preferred; MSI as fallback
add_platform "windows-x86_64" "$(pick_file '*-setup.exe' '*_x64*.exe' '*.msi')"

# macOS — updater uses .app.tar.gz (not .dmg)
add_platform "darwin-aarch64" "$(pick_file '*aarch64*.app.tar.gz' '*.app.tar.gz')"
add_platform "darwin-x86_64" "$(pick_file '*x64*.app.tar.gz' '*x86_64*.app.tar.gz')"

COUNT="$(jq '.platforms | length' "$OUT")"
if [[ "$COUNT" -eq 0 ]]; then
  echo "[updater] WARNING: no signed platforms found — latest.json has empty platforms" >&2
  echo "[updater] (Is TAURI_SIGNING_PRIVATE_KEY set in GitHub Actions secrets?)" >&2
fi

echo "[updater] wrote $OUT ($COUNT platform(s))"
jq . "$OUT"
