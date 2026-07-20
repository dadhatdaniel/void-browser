#!/usr/bin/env python3
"""
Void Browser — SSH client for void-test-linux (rpa-linux) RPA smoke.

Runs from GitLab runners on the LAN that can reach 10.0.1.114:22.
Mirrors scripts/ci/rpa-winrm.py: sync/push harness, run smoke on the
logged-in X11 session (DISPLAY=:0), pull artifacts.

Credentials (never commit):
  VOID_RPA_LINUX_HOST  (default 10.0.1.114)
  VOID_RPA_LINUX_USER  (default rpa-linux)
  VOID_RPA_LINUX_PASS  (required)

Usage:
  python scripts/ci/rpa-ssh.py --sync-repo --download-install
  python scripts/ci/rpa-ssh.py --scenarios smoke_launch,smoke_navigate
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SCENARIOS = (
    "download_install,app_launch,smoke_navigate,nav_history,visit_void_site,"
    "settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update"
)
DEFAULT_REMOTE_ROOT = "/home/rpa-linux/void-browser"


def _require_paramiko():
    try:
        import paramiko  # noqa: F401
    except ImportError:
        print("paramiko required: pip install paramiko", file=sys.stderr)
        raise SystemExit(2)


def connect():
    import paramiko

    host = os.environ.get("VOID_RPA_LINUX_HOST", "10.0.1.114").strip()
    user = os.environ.get("VOID_RPA_LINUX_USER", "rpa-linux").strip()
    password = os.environ.get("VOID_RPA_LINUX_PASS", "").strip()
    if not password:
        print(
            "VOID_RPA_LINUX_PASS is required (set GitLab CI variable; never commit).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=user,
        password=password,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run(client, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = int(stdout.channel.recv_exit_status())
    return code, out, err


def ensure_desktop_session(client) -> None:
    """Fail fast if there is no graphical session for rpa-linux."""
    code, out, err = run(
        client,
        "loginctl list-sessions --no-legend 2>/dev/null; "
        "echo ---; "
        "test -S /run/user/1000/bus && echo BUS_OK || echo BUS_MISSING; "
        "pgrep -x gnome-shell >/dev/null && echo SHELL_OK || echo SHELL_MISSING; "
        "pgrep -x Xorg >/dev/null && echo XORG_OK || echo XORG_MISSING",
    )
    print(out)
    if "SHELL_OK" not in out and "XORG_OK" not in out:
        raise RuntimeError(
            "No Active graphical session on void-test-linux. "
            "Check GDM autologin (scripts/ci/configure-rpa-linux-desktop.sh) "
            "or VNC http://10.0.0.10:5701/ for diagnosis. "
            f"detail={out or err}"
        )


def sync_repo(client, remote_root: str) -> None:
    print(f"[rpa-linux] git sync on VM: {remote_root}", flush=True)
    code, out, err = run(
        client,
        f"""
set -e
root={remote_root!r}
mkdir -p "$root"
cd "$root"
export GIT_TERMINAL_PROMPT=0
if [ ! -d .git ]; then
  git clone --depth 1 http://10.0.0.10:8929/lightfootcloud/void-browser.git "$root" || \\
    git clone --depth 1 https://github.com/dadhatdaniel/void-browser.git "$root"
fi
cd "$root"
git fetch --depth 1 origin main 2>/dev/null || git fetch --depth 1 origin 2>/dev/null || true
git checkout -f main 2>/dev/null || git checkout -f master 2>/dev/null || true
git reset --hard origin/main 2>/dev/null || git reset --hard FETCH_HEAD 2>/dev/null || true
git rev-parse --short HEAD || true
echo SYNC_OK
""",
        timeout=300,
    )
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    if code != 0 or "SYNC_OK" not in out:
        raise RuntimeError(f"git sync failed: {err or out}")


def push_harness(client, remote_root: str, local_root: Path) -> None:
    rels = [
        "scripts/run-rpa-linux.sh",
        "scripts/ci/configure-rpa-linux-desktop.sh",
        "tests/rpa/runner_linux.py",
        "tests/rpa/requirements.txt",
        "docs/RPA_TESTING.md",
        "docs/TEST_VMS.md",
    ]
    print("[rpa-linux] pushing harness files via SFTP...", flush=True)
    sftp = client.open_sftp()
    try:
        for rel in rels:
            local = local_root / rel.replace("/", os.sep)
            if not local.is_file():
                continue
            remote = f"{remote_root.rstrip('/')}/{rel}"
            # ensure parent dirs
            parts = remote.split("/")[:-1]
            path = ""
            for p in parts:
                if not p:
                    continue
                path = f"{path}/{p}"
                try:
                    sftp.stat(path)
                except OSError:
                    try:
                        sftp.mkdir(path)
                    except OSError:
                        pass
            data = local.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            with sftp.file(remote, "wb") as rf:
                rf.write(data)
            if rel.endswith(".sh"):
                sftp.chmod(remote, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            print(f"  pushed {rel}")
    finally:
        sftp.close()


def run_smoke(
    client,
    *,
    remote_root: str,
    scenarios: str,
    timeout_sec: int,
    download_install: bool,
) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    status = f"/home/rpa-linux/void-rpa-status/{run_id}"
    print(f"[rpa-linux] starting smoke {run_id} scenarios={scenarios}", flush=True)

    force_dl = os.environ.get("VOID_RPA_FORCE_DOWNLOAD", "1").strip() or "1"
    sudo_pass = os.environ.get("VOID_RPA_LINUX_PASS", "").strip()
    # Escape for single-quoted bash embedding
    sudo_q = sudo_pass.replace("'", "'\"'\"'")

    wrapper = f"""#!/usr/bin/env bash
set +e
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
export VOID_RELEASES_JSON_URL=http://10.0.0.10:5080/releases.json
export VOID_RPA_FORCE_DOWNLOAD={force_dl}
export VOID_RPA_LINUX_SUDO_PASS='{sudo_q}'
export VOID_SOFTWARE_RENDERING=1
export WEBKIT_DISABLE_COMPOSITING_MODE=1
export LIBGL_ALWAYS_SOFTWARE=1
export VOID_RPA_CLEANUP=1
mkdir -p {status} {remote_root}/artifacts/rpa {remote_root}/downloads/rpa
chmod +x {remote_root}/scripts/run-rpa-linux.sh
python3 -m pip install --user -q Pillow 2>/dev/null || true
echo '{sudo_q}' | sudo -S DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xclip scrot xdotool 2>/dev/null || true
# Disable apport crash dialogs stealing focus during RPA
echo '{sudo_q}' | sudo -S systemctl stop apport.service 2>/dev/null || true
echo '{sudo_q}' | sudo -S systemctl disable apport.service 2>/dev/null || true
echo '{sudo_q}' | sudo -S bash -c 'echo enabled=0 > /etc/default/apport' 2>/dev/null || true
cd {remote_root}
./scripts/run-rpa-linux.sh --scenarios '{scenarios}' > {status}/run.log 2>&1
code=$?
latest=$(ls -1dt {remote_root}/artifacts/rpa/*/ 2>/dev/null | head -n1 || true)
python3 -c "import json; print(json.dumps({{'exit_code': $code, 'finished_at': __import__('datetime').datetime.now().isoformat(), 'artifact_dir': ('''$latest'''.strip() or None)}}))" > {status}/done.json
"""

    sftp = client.open_sftp()
    try:
        run(client, f"mkdir -p {status!r}")
        wrapper_path = f"{status}/run.sh"
        with sftp.file(wrapper_path, "w") as f:
            f.write(wrapper.replace("\r\n", "\n"))
        sftp.chmod(wrapper_path, 0o755)
    finally:
        sftp.close()

    code, out, err = run(
        client,
        f"nohup bash {status}/run.sh >/dev/null 2>&1 & echo STARTED",
        timeout=30,
    )
    print(out)
    if code != 0 or "STARTED" not in out:
        raise RuntimeError(f"failed to start smoke: {err or out}")

    deadline = time.time() + timeout_sec
    print(f"[rpa-linux] waiting up to {timeout_sec}s for done.json...", flush=True)
    done_obj = None
    while time.time() < deadline:
        time.sleep(6)
        _c, o, _e = run(
            client, f"test -f {status!r}/done.json && cat {status!r}/done.json || true"
        )
        text = (o or "").strip()
        if text:
            try:
                done_obj = json.loads(text)
                break
            except json.JSONDecodeError:
                pass
        print(f"  ... still running ({int(deadline - time.time())}s left)", flush=True)

    if not done_obj:
        _c, o, _e = run(client, f"tail -n 80 {status!r}/run.log 2>/dev/null || true")
        print("---- remote log (tail) ----")
        print(o)
        raise TimeoutError(
            f"Timed out after {timeout_sec}s waiting for Linux RPA done.json"
        )

    print(
        f"[rpa-linux] exit_code={done_obj.get('exit_code')} "
        f"artifact_dir={done_obj.get('artifact_dir')}",
        flush=True,
    )
    _c, o, _e = run(client, f"tail -n 80 {status!r}/run.log 2>/dev/null || true")
    print("---- remote log (tail) ----")
    print(o)
    return done_obj


def pull_artifacts(client, artifact_dir: str, local_dir: Path) -> None:
    import paramiko

    artifact_dir = (artifact_dir or "").rstrip("/")
    if not artifact_dir:
        return
    local_dir.mkdir(parents=True, exist_ok=True)
    print(f"[rpa-linux] pulling artifacts -> {local_dir}", flush=True)
    sftp = client.open_sftp()
    try:
        for attr in sftp.listdir_attr(artifact_dir):
            if stat.S_ISDIR(attr.st_mode or 0):
                continue
            name = attr.filename
            remote = f"{artifact_dir}/{name}"
            local = local_dir / name
            sftp.get(remote, str(local))
            print(f"  {name} ({local.stat().st_size} bytes)")
    finally:
        sftp.close()


def mirror_to_unraid(local_dir: Path, stamp: str) -> None:
    candidates = [
        Path("/mnt/user/appdata/void-rpa-artifacts"),
        Path("/mnt/user/void-rpa-artifacts"),
    ]
    env_dir = os.environ.get("VOID_RPA_MIRROR_DIR", "").strip()
    if env_dir:
        candidates.insert(0, Path(env_dir))
    for base in candidates:
        try:
            if not base.parent.exists() and not base.exists():
                continue
            dest = base / f"linux-{stamp}"
            dest.mkdir(parents=True, exist_ok=True)
            for f in local_dir.iterdir():
                if f.is_file():
                    (dest / f.name).write_bytes(f.read_bytes())
            print(f"[rpa-linux] mirrored to {dest}")
            return
        except OSError as e:
            print(f"[rpa-linux] mirror skip {base}: {e}")


def main() -> int:
    _require_paramiko()
    parser = argparse.ArgumentParser(description="SSH RPA smoke for void-test-linux")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    parser.add_argument("--download-install", action="store_true",
                        help="Ensure AppImage download (default behavior of run-rpa-linux.sh)")
    parser.add_argument("--sync-repo", action="store_true")
    parser.add_argument("--sync-repo-only", action="store_true")
    parser.add_argument("--push-harness", action="store_true", default=True)
    parser.add_argument("--no-push-harness", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--out", default=str(ROOT / "artifacts" / "rpa"))
    parser.add_argument("--configure-desktop", action="store_true",
                        help="Re-apply GDM autologin + no-lock before smoke")
    args = parser.parse_args()

    client = connect()
    try:
        if args.configure_desktop:
            push_harness(client, args.remote_root, ROOT)
            code, out, err = run(
                client,
                f"bash {args.remote_root}/scripts/ci/configure-rpa-linux-desktop.sh",
                timeout=180,
            )
            print(out)
            if code != 0:
                print(err, file=sys.stderr)
                raise RuntimeError("configure-desktop failed")

        if args.sync_repo_only:
            sync_repo(client, args.remote_root)
            if args.push_harness and not args.no_push_harness:
                push_harness(client, args.remote_root, ROOT)
            print("[rpa-linux] sync-repo-only done")
            return 0

        ensure_desktop_session(client)

        if args.sync_repo:
            try:
                sync_repo(client, args.remote_root)
            except RuntimeError as e:
                print(f"[rpa-linux] git sync warning: {e} — continuing with push", flush=True)

        if args.push_harness and not args.no_push_harness:
            push_harness(client, args.remote_root, ROOT)

        done = run_smoke(
            client,
            remote_root=args.remote_root,
            scenarios=args.scenarios,
            timeout_sec=args.timeout_sec,
            download_install=args.download_install,
        )

        art = (done.get("artifact_dir") or "").strip()
        ec = done.get("exit_code")
        exit_code = 1 if ec is None else int(ec)

        if not art:
            print("[rpa-linux] no artifact_dir", file=sys.stderr)
            return exit_code

        stamp = Path(art.rstrip("/")).name
        local_dir = Path(args.out) / stamp
        pull_artifacts(client, art.rstrip("/"), local_dir)
        mirror_to_unraid(local_dir, stamp)

        report = local_dir / "report.json"
        if report.is_file():
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
                print(f"[rpa-linux] report ok={data.get('ok')} dir={local_dir}")
                for sc in data.get("scenarios") or []:
                    mark = "PASS" if sc.get("ok") else "FAIL"
                    print(f"  [{mark}] {sc.get('name')} ({sc.get('duration_sec')}s)")
            except Exception as e:  # noqa: BLE001
                print(f"[rpa-linux] could not parse report: {e}")

        return exit_code
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        raise SystemExit(1)
