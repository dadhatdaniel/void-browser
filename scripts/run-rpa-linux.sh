#!/usr/bin/env bash
# Void Browser — Linux RPA smoke (AppImage launch + basic navigate).
#
# Intended to run ON the void-test-linux guest (display :0, logged-in session).
# Triggered remotely by scripts/ci/rpa-ssh.py from GitLab.
#
# Usage:
#   ./scripts/run-rpa-linux.sh
#   ./scripts/run-rpa-linux.sh --scenarios smoke_launch,smoke_navigate
#   VOID_RELEASES_JSON_URL=http://10.0.0.10:5080/releases.json ./scripts/run-rpa-linux.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIOS="${VOID_RPA_SCENARIOS:-smoke_launch,smoke_navigate}"
RELEASES_URL="${VOID_RELEASES_JSON_URL:-http://10.0.0.10:5080/releases.json}"
DL_DIR="${VOID_RPA_DOWNLOAD_DIR:-$ROOT/downloads/rpa}"
ART_BASE="${VOID_RPA_ARTIFACTS:-$ROOT/artifacts/rpa}"
LAUNCH_WAIT="${VOID_RPA_LAUNCH_WAIT:-8}"
DISPLAY_NUM="${DISPLAY:-:0}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
ART_DIR="$ART_BASE/$STAMP"
APPIMAGE=""
REPORT="$ART_DIR/report.json"

mkdir -p "$DL_DIR" "$ART_DIR"

export DISPLAY="$DISPLAY_NUM"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

log() { echo "[rpa-linux] $*"; }

shot() {
  local name="$1"
  if command -v scrot >/dev/null 2>&1; then
    scrot -o "$ART_DIR/${name}.png" || true
  elif command -v gnome-screenshot >/dev/null 2>&1; then
    gnome-screenshot -f "$ART_DIR/${name}.png" || true
  fi
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()[:-1] if False else sys.argv[1]))' "$1"
}

# Resolve AppImage path: env override → downloads → releases.json
resolve_appimage() {
  if [[ -n "${VOID_RPA_APPIMAGE:-}" && -f "${VOID_RPA_APPIMAGE}" ]]; then
    APPIMAGE="$VOID_RPA_APPIMAGE"
    return
  fi
  local existing
  existing="$(ls -1t "$DL_DIR"/*.AppImage 2>/dev/null | head -n1 || true)"
  if [[ -n "$existing" && -f "$existing" ]]; then
    local sz
    sz=$(stat -c%s "$existing" 2>/dev/null || echo 0)
    if [[ "$sz" -gt 5000000 ]]; then
      APPIMAGE="$existing"
      return
    fi
  fi

  log "Fetching releases.json → $RELEASES_URL"
  local tmp js url name
  tmp="$(mktemp)"
  if ! curl -fsSL --connect-timeout 15 --max-time 60 -o "$tmp" "$RELEASES_URL"; then
    log "LAN releases.json failed; trying GitHub latest.json"
    curl -fsSL --connect-timeout 20 --max-time 60 \
      -o "$tmp" \
      "https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json" || true
  fi
  js="$(cat "$tmp")"
  rm -f "$tmp"
  url="$(printf '%s' "$js" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assets=d.get("assets") or {}
a=assets.get("linux_appimage") or {}
print(a.get("url") or "")
')"
  name="$(printf '%s' "$js" | python3 -c '
import json,sys
d=json.load(sys.stdin)
assets=d.get("assets") or {}
a=assets.get("linux_appimage") or {}
print(a.get("name") or "void-browser.AppImage")
')"
  if [[ -z "$url" ]]; then
    log "ERROR: no linux_appimage in releases metadata"
    return 1
  fi
  log "Downloading $name"
  curl -fL --retry 3 --connect-timeout 30 --max-time 600 -o "$DL_DIR/$name" "$url"
  chmod +x "$DL_DIR/$name"
  APPIMAGE="$DL_DIR/$name"
}

kill_void() {
  pkill -f '[Vv]oid[Bb]rowser' 2>/dev/null || true
  pkill -f 'Void\.Browser.*AppImage' 2>/dev/null || true
  # AppImage extracted processes
  pkill -f 'void-browser' 2>/dev/null || true
  sleep 1
}

scenario_smoke_launch() {
  local detail="ok" ok=1
  kill_void
  if [[ ! -f "$APPIMAGE" ]]; then
    echo "missing AppImage" >&2
    return 1
  fi
  log "Launching $APPIMAGE on DISPLAY=$DISPLAY"
  # nohup so SSH disconnect does not kill the GUI
  nohup "$APPIMAGE" --no-sandbox >/tmp/void-rpa-linux-app.log 2>&1 &
  local pid=$!
  sleep "$LAUNCH_WAIT"
  if ! kill -0 "$pid" 2>/dev/null && ! pgrep -af -i 'void|webkit' | grep -qi void; then
    detail="process exited early; log=$(tail -c 400 /tmp/void-rpa-linux-app.log 2>/dev/null || true)"
    ok=0
  fi
  # Prefer window title match
  if command -v xdotool >/dev/null 2>&1; then
    if xdotool search --name 'Void' >/dev/null 2>&1 || \
       xdotool search --class 'void' >/dev/null 2>&1 || \
       xdotool search --name 'void-browser' >/dev/null 2>&1; then
      detail="window found"
      ok=1
    elif [[ "$ok" -eq 1 ]]; then
      detail="process up, window not yet titled Void (may still be starting)"
    fi
  fi
  shot "01_smoke_launch"
  if [[ "$ok" -eq 1 ]]; then
    echo "$detail"
    return 0
  fi
  echo "$detail" >&2
  return 1
}

scenario_smoke_navigate() {
  local detail="ok"
  if ! command -v xdotool >/dev/null 2>&1; then
    echo "xdotool missing" >&2
    return 1
  fi
  local wid
  wid="$(xdotool search --name 'Void' 2>/dev/null | head -n1 || true)"
  if [[ -z "$wid" ]]; then
    wid="$(xdotool search --class 'void' 2>/dev/null | head -n1 || true)"
  fi
  if [[ -z "$wid" ]]; then
    # last resort: any large window owned by our user after launch
    wid="$(xdotool search --onlyvisible --name '.' 2>/dev/null | tail -n1 || true)"
  fi
  if [[ -z "$wid" ]]; then
    echo "no window to drive" >&2
    shot "02_smoke_navigate_fail"
    return 1
  fi
  xdotool windowactivate --sync "$wid" || true
  sleep 0.5
  # Focus address bar (Ctrl+L common in browsers) and navigate
  xdotool key --window "$wid" ctrl+l
  sleep 0.4
  xdotool type --window "$wid" --delay 12 'https://example.com'
  xdotool key --window "$wid" Return
  sleep 4
  shot "02_smoke_navigate"
  detail="typed example.com into window $wid"
  echo "$detail"
  return 0
}

# Parse --scenarios
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenarios) SCENARIOS="$2"; shift 2 ;;
    --appimage) VOID_RPA_APPIMAGE="$2"; shift 2 ;;
    *) log "unknown arg: $1"; shift ;;
  esac
done

log "root=$ROOT scenarios=$SCENARIOS"
resolve_appimage
log "appimage=$APPIMAGE ($(stat -c%s "$APPIMAGE" 2>/dev/null || echo 0) bytes)"

declare -a RESULTS=()
OVERALL=1
IFS=',' read -ra SC_LIST <<< "$SCENARIOS"
for sc in "${SC_LIST[@]}"; do
  sc="$(echo "$sc" | xargs)"
  [[ -z "$sc" ]] && continue
  start=$(date +%s)
  set +e
  case "$sc" in
    smoke_launch|app_launch)
      out=$(scenario_smoke_launch 2>&1)
      rc=$?
      ;;
    smoke_navigate)
      out=$(scenario_smoke_navigate 2>&1)
      rc=$?
      ;;
    *)
      out="unknown scenario"
      rc=1
      ;;
  esac
  set -e
  end=$(date +%s)
  dur=$((end - start))
  if [[ $rc -eq 0 ]]; then
    log "PASS $sc (${dur}s) $out"
    RESULTS+=("{\"name\":\"$sc\",\"ok\":true,\"duration_sec\":$dur,\"error\":null,\"detail\":$(json_escape "$out")}")
  else
    log "FAIL $sc (${dur}s) $out"
    OVERALL=0
    RESULTS+=("{\"name\":\"$sc\",\"ok\":false,\"duration_sec\":$dur,\"error\":$(json_escape "$out")}")
  fi
done

# Build report.json
{
  echo -n '{"ok":'
  if [[ $OVERALL -eq 1 ]]; then echo -n 'true'; else echo -n 'false'; fi
  echo -n ',"platform":"linux","stamp":"'"$STAMP"'","scenarios":['
  first=1
  for r in "${RESULTS[@]+"${RESULTS[@]}"}"; do
    [[ $first -eq 1 ]] || echo -n ','
    first=0
    echo -n "$r"
  done
  echo -n '],"meta":{"appimage":'"$(json_escape "$APPIMAGE")"',"display":'"$(json_escape "$DISPLAY")"'}}'
  echo
} > "$REPORT"

log "report -> $REPORT (ok=$OVERALL)"
cat "$REPORT"
# Leave app running for human VNC inspection; CI can kill later.
exit $((1 - OVERALL))
