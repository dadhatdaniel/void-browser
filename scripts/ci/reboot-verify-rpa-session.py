#!/usr/bin/env python3
"""Reboot void-rpa-windows via WinRM, wait for boot + Active console, no VNC."""
from __future__ import annotations

import os
import sys
import time

import winrm


def session():
    password = os.environ.get("VOID_RPA_WIN_PASS", "").strip()
    if not password:
        raise SystemExit("VOID_RPA_WIN_PASS required")
    host = os.environ.get("VOID_RPA_WIN_HOST", "10.0.0.28").strip()
    user = os.environ.get("VOID_RPA_WIN_USER", "rpa-win").strip()
    endpoint = host if host.startswith("http") else f"http://{host}:5985/wsman"
    return winrm.Session(endpoint, auth=(user, password), transport="ntlm")


def run_ps(sess, script: str):
    r = sess.run_ps(script)
    out = (r.std_out or b"").decode("utf-8", errors="replace")
    return int(r.status_code), out


def main() -> int:
    do_reboot = "--no-reboot" not in sys.argv
    sess = session()

    if do_reboot:
        print("[reboot] issuing Restart-Computer -Force ...", flush=True)
        try:
            # May drop mid-command
            run_ps(sess, "Restart-Computer -Force")
        except Exception as e:
            print(f"[reboot] connection dropped (expected): {e}", flush=True)
        print("[reboot] waiting for WinRM to drop...", flush=True)
        time.sleep(15)
    else:
        print("[reboot] skipped (--no-reboot)", flush=True)

    deadline = time.time() + 300
    print("[wait] polling WinRM until up (max 300s)...", flush=True)
    while time.time() < deadline:
        try:
            code, out = run_ps(session(), "Write-Output 'pong'; $env:COMPUTERNAME")
            if code == 0 and "pong" in out:
                print(f"[wait] WinRM up: {out.strip()}", flush=True)
                break
        except Exception:
            pass
        time.sleep(5)
    else:
        print("FAIL: WinRM did not return within 300s", file=sys.stderr)
        return 1

    # Autologon can lag a bit after WinRM is up
    print("[wait] waiting for Active console session (max 120s)...", flush=True)
    deadline = time.time() + 120
    last = ""
    while time.time() < deadline:
        try:
            code, out = run_ps(
                session(),
                r"""
$query = (query user 2>&1 | Out-String)
Write-Output $query
$exp = Get-Process explorer -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Output ("explorer_session=" + $(if ($exp) { $exp.SessionId } else { 'none' }))
$wl = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Write-Output ("AutoAdminLogon=" + $wl.AutoAdminLogon + " user=" + $wl.DefaultUserName)
""",
            )
            last = out
            print("--- probe ---", flush=True)
            print(out, flush=True)
            if "Active" in out and "rpa-win" in out.lower() and "explorer_session=" in out:
                # explorer in session 1 is the interactive desktop
                if "explorer_session=1" in out or "explorer_session=2" in out:
                    print("PASS: Active interactive session without opening VNC", flush=True)
                    return 0
                # still Active is good enough if query shows Active
                if "Active" in out:
                    print("PASS: Active console session present", flush=True)
                    return 0
        except Exception as e:
            last = str(e)
            print(f"[wait] error: {e}", flush=True)
        time.sleep(5)

    print("FAIL: no Active session after boot", file=sys.stderr)
    print(last, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
