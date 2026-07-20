#!/usr/bin/env bash
# Void Browser — Linux RPA entrypoint (AppImage + xdotool suite).
#
# Runs ON void-test-linux (DISPLAY=:0, GDM autologin). Triggered by
# scripts/ci/rpa-ssh.py from GitLab.
#
# Usage:
#   ./scripts/run-rpa-linux.sh
#   ./scripts/run-rpa-linux.sh --scenarios smoke_launch,smoke_navigate
#   VOID_RELEASES_JSON_URL=http://10.0.0.10:5080/releases.json ./scripts/run-rpa-linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SCENARIOS="${VOID_RPA_SCENARIOS:-download_install,app_launch,smoke_navigate,nav_history,visit_void_site,settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update}"
LAUNCH_WAIT="${VOID_RPA_LAUNCH_WAIT:-8}"
export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export VOID_RELEASES_JSON_URL="${VOID_RELEASES_JSON_URL:-http://10.0.0.10:5080/releases.json}"
export VOID_RPA_DOWNLOAD_DIR="${VOID_RPA_DOWNLOAD_DIR:-$ROOT/downloads/rpa}"
export VOID_RPA_ARTIFACTS="${VOID_RPA_ARTIFACTS:-$ROOT/artifacts/rpa}"
# Always-run teardown removes staged AppImages (parity with Windows uninstall).
export VOID_RPA_CLEANUP="${VOID_RPA_CLEANUP:-1}"
# QEMU guests lack DRI3 — software WebKit by default (see runner_linux._launch_env).
export VOID_SOFTWARE_RENDERING="${VOID_SOFTWARE_RENDERING:-1}"
export WEBKIT_DISABLE_COMPOSITING_MODE="${WEBKIT_DISABLE_COMPOSITING_MODE:-1}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"

APPIMAGE_ARG=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenarios) SCENARIOS="$2"; shift 2 ;;
    --appimage) APPIMAGE_ARG=(--appimage "$2"); shift 2 ;;
    --launch-wait) LAUNCH_WAIT="$2"; shift 2 ;;
    *) echo "[rpa-linux] unknown arg: $1" >&2; shift ;;
  esac
done

log() { echo "[rpa-linux] $*"; }

for bin in xdotool scrot python3 curl; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    log "ERROR: missing $bin"
    exit 2
  fi
done

# Focus-stealers (do NOT pkill -f void-browser — matches ~/void-browser/ harness path)
pkill -x update-manager 2>/dev/null || true

mkdir -p "$VOID_RPA_DOWNLOAD_DIR" "$VOID_RPA_ARTIFACTS"

# Pillow optional but used for blank-content checks
python3 -c 'import PIL' 2>/dev/null || python3 -m pip install --user -q Pillow 2>/dev/null || true

log "root=$ROOT scenarios=$SCENARIOS display=$DISPLAY"
exec python3 "$ROOT/tests/rpa/runner_linux.py" \
  --scenarios "$SCENARIOS" \
  --launch-wait "$LAUNCH_WAIT" \
  --out "$VOID_RPA_ARTIFACTS" \
  "${APPIMAGE_ARG[@]}"
