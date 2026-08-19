#!/bin/bash
# Start/stop Unraid libvirt VM void-rpa-windows for GitLab RPA jobs.
# Run on the unraid-shell executor (has virsh). Docker jobs cannot call virsh.
#
#   bash scripts/ci/rpa-vm-power.sh start
#   bash scripts/ci/rpa-vm-power.sh stop
#   bash scripts/ci/rpa-vm-power.sh status
#
# Env:
#   RPA_LIBVIRT_DOMAIN  default void-rpa-windows
#   VOID_RPA_WIN_HOST   default 10.0.0.28
#   VOID_RPA_WIN_PORT   default 5985
#   KEEP_RPA_VM=1       skip shutdown
set -eu

DOMAIN="${RPA_LIBVIRT_DOMAIN:-void-rpa-windows}"
HOST="${VOID_RPA_WIN_HOST:-10.0.0.28}"
PORT="${VOID_RPA_WIN_PORT:-5985}"
WAIT_SEC="${RPA_WINRM_WAIT_SEC:-300}"
ACTION="${1:-start}"

if ! command -v virsh >/dev/null 2>&1; then
  echo "BLOCKER: virsh not found — this job must run on tags: [unraid-shell]"
  exit 1
fi

dom_state() {
  virsh domstate "$DOMAIN" 2>/dev/null || echo "missing"
}

wait_winrm() {
  echo "Waiting up to ${WAIT_SEC}s for WinRM TCP ${HOST}:${PORT} ..."
  python3 - "$HOST" "$PORT" "$WAIT_SEC" <<'PY'
import socket, sys, time
host, port, timeout = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
deadline = time.time() + timeout
last = None
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"WinRM TCP open on {host}:{port}", flush=True)
            raise SystemExit(0)
    except OSError as e:
        last = e
        print(f"  wait: {e}", flush=True)
    time.sleep(5)
print(f"TIMEOUT: WinRM {host}:{port} did not open ({last})", file=sys.stderr)
raise SystemExit(1)
PY
}

case "$ACTION" in
  start)
    state=$(dom_state)
    echo "libvirt ${DOMAIN} state=${state}"
    if echo "$state" | grep -qi running; then
      echo "${DOMAIN} already running"
    elif echo "$state" | grep -qi missing; then
      echo "BLOCKER: domain ${DOMAIN} not defined"
      virsh list --all || true
      exit 1
    else
      echo "virsh start ${DOMAIN}"
      if ! virsh start "$DOMAIN"; then
        # racing a parallel job is ok
        if ! virsh domstate "$DOMAIN" | grep -qi running; then
          echo "BLOCKER: failed to start ${DOMAIN}"
          exit 1
        fi
      fi
    fi
    wait_winrm
    ;;
  stop)
    if [ "${KEEP_RPA_VM:-}" = "1" ] || [ "${KEEP_RPA_VM:-}" = "true" ] || [ "${KEEP_RPA_VM:-}" = "yes" ]; then
      echo "KEEP_RPA_VM=1 — leaving ${DOMAIN} running"
      exit 0
    fi
    state=$(dom_state)
    echo "libvirt ${DOMAIN} state=${state}"
    if echo "$state" | grep -qi running; then
      echo "virsh shutdown ${DOMAIN}"
      virsh shutdown "$DOMAIN" || true
    else
      echo "${DOMAIN} not running — nothing to stop"
    fi
    ;;
  status)
    echo "libvirt ${DOMAIN} state=$(dom_state)"
    python3 - "$HOST" "$PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=3):
        print(f"WinRM TCP {host}:{port} OPEN")
except OSError as e:
    print(f"WinRM TCP {host}:{port} CLOSED ({e})")
    raise SystemExit(1)
PY
    ;;
  *)
    echo "usage: $0 start|stop|status"
    exit 2
    ;;
esac
