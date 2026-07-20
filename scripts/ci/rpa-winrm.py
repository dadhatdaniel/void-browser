#!/usr/bin/env python3
"""
Void Browser — Linux/CI WinRM client for void-rpa-windows (rpa-win).

Runs from GitLab runners on the LAN that can reach 10.0.0.28:5985.
Mirrors scripts/rpa-remote-run.ps1: sync repo, schedule interactive RPA,
wait for done.json, pull artifacts.

Credentials (never commit):
  VOID_RPA_WIN_HOST  (default 10.0.0.28)
  VOID_RPA_WIN_USER  (default rpa-win)
  VOID_RPA_WIN_PASS  (required)

Usage:
  python scripts/ci/rpa-winrm.py --sync-repo-only
  python scripts/ci/rpa-winrm.py --download-install --sync-repo
  python scripts/ci/rpa-winrm.py --download-install --sync-repo --release-tag v0.1.0-alpha.16
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SCENARIOS = (
    "download_install,app_launch,smoke_navigate,nav_history,visit_void_site,"
    "settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update"
)


def _require_winrm() -> None:
    try:
        import winrm  # noqa: F401
    except ImportError:
        print("pywinrm required: pip install pywinrm requests", file=sys.stderr)
        raise SystemExit(2)


def session_from_env():
    import winrm

    host = os.environ.get("VOID_RPA_WIN_HOST", "10.0.0.28").strip()
    user = os.environ.get("VOID_RPA_WIN_USER", "rpa-win").strip()
    password = os.environ.get("VOID_RPA_WIN_PASS", "").strip()
    if not password:
        print(
            "VOID_RPA_WIN_PASS is required (set GitLab CI variable; never commit).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    endpoint = host if host.startswith("http") else f"http://{host}:5985/wsman"
    return winrm.Session(endpoint, auth=(user, password), transport="ntlm")


def run_ps(sess, script: str) -> tuple[int, str, str]:
    result = sess.run_ps(script)
    out = (result.std_out or b"").decode("utf-8", errors="replace")
    err_raw = (result.std_err or b"").decode("utf-8", errors="replace")
    code = int(result.status_code)
    # PowerShell often dumps CLIXML progress noise on stderr with non-zero status.
    if "CLIXML" in err_raw or "<Objs " in err_raw:
        meaningful = [
            ln
            for ln in err_raw.splitlines()
            if ln.strip()
            and "CLIXML" not in ln
            and not ln.strip().startswith("<")
            and "Preparing modules" not in ln
            and "xmlns=" not in ln
        ]
        err = "\n".join(meaningful)
        # Side-effect scripts may have empty stdout; treat pure CLIXML noise as success.
        if code != 0 and not err.strip():
            return 0, out, err
        return code, out, err
    return code, out, err_raw


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def sync_repo(sess, remote_root: str) -> None:
    print(f"[rpa] git sync on VM: {remote_root}", flush=True)
    code, out, err = run_ps(
        sess,
        f"""
$ErrorActionPreference = 'Continue'
$root = {ps_quote(remote_root)}
if (-not (Test-Path $root)) {{ throw "RemoteRoot missing: $root" }}
Set-Location $root
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
  [Environment]::GetEnvironmentVariable('Path','User')
git -c credential.helper= fetch --depth 1 origin main
if ($LASTEXITCODE -ne 0) {{ Write-Output "FETCH_FAIL=$LASTEXITCODE"; exit $LASTEXITCODE }}
git -c credential.helper= reset --hard origin/main
if ($LASTEXITCODE -ne 0) {{ Write-Output "RESET_FAIL=$LASTEXITCODE"; exit $LASTEXITCODE }}
git log -1 --oneline
Write-Output 'SYNC_OK'
exit 0
""",
    )
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    if "SYNC_OK" not in out:
        raise RuntimeError(f"git sync failed (exit {code}): {err or out}")


def write_remote_bytes(sess, remote_path: str, data: bytes) -> None:
    """Write a remote file via small base64 chunks (WinRM command-length safe)."""
    b64 = base64.b64encode(data).decode("ascii")
    remote_dir = str(Path(remote_path.replace("\\", "/")).parent).replace("/", "\\")
    chunk_size = 1_500
    chunks = [b64[i : i + chunk_size] for i in range(0, len(b64), chunk_size)]
    b64_path = remote_path + ".b64"
    code, out, err = run_ps(
        sess,
        f"""
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path {ps_quote(remote_dir)} | Out-Null
if (Test-Path {ps_quote(b64_path)}) {{ Remove-Item -Force {ps_quote(b64_path)} }}
New-Item -ItemType File -Force -Path {ps_quote(b64_path)} | Out-Null
Write-Output 'PREP_OK'
""",
    )
    if code != 0 or "PREP_OK" not in out:
        raise RuntimeError(f"prep write {remote_path}: {err or out}")
    for i, chunk in enumerate(chunks):
        code, out, err = run_ps(
            sess,
            f"""
$ErrorActionPreference = 'Stop'
[IO.File]::AppendAllText({ps_quote(b64_path)}, {ps_quote(chunk)})
Write-Output 'CHUNK_OK'
""",
        )
        if code != 0 or "CHUNK_OK" not in out:
            raise RuntimeError(f"chunk write {remote_path} #{i}/{len(chunks)}: {err or out}")
    code, out, err = run_ps(
        sess,
        f"""
$ErrorActionPreference = 'Stop'
$b64 = [IO.File]::ReadAllText({ps_quote(b64_path)})
[IO.File]::WriteAllBytes({ps_quote(remote_path)}, [Convert]::FromBase64String(($b64 -replace '\\s','')))
Remove-Item -Force {ps_quote(b64_path)} -ErrorAction SilentlyContinue
Write-Output 'WRITE_OK'
""",
    )
    if code != 0 or "WRITE_OK" not in out:
        raise RuntimeError(f"decode write {remote_path}: {err or out}")


def push_harness_files(sess, remote_root: str, local_root: Path) -> None:
    rels = [
        "tests/rpa/runner.py",
        "tests/rpa/requirements.txt",
        "scripts/run-rpa-windows.ps1",
        "scripts/rpa-after-release.ps1",
        "docs/RPA_TESTING.md",
    ]
    print("[rpa] pushing harness files via WinRM...", flush=True)
    for rel in rels:
        local = local_root / rel.replace("/", os.sep)
        if not local.is_file():
            continue
        remote_rel = rel.replace("/", "\\")
        remote = f"{remote_root}\\{remote_rel}"
        write_remote_bytes(sess, remote, local.read_bytes())
        print(f"  pushed {rel}")


def build_wrapper(
    *,
    remote_root: str,
    scenarios: str,
    launch_wait: float,
    download_install: bool,
    status_dir: str,
    release_tag: str,
) -> str:
    dl_flag = "-DownloadInstall" if download_install else ""
    portable_line = ""
    if release_tag:
        url = (
            "https://github.com/dadhatdaniel/void-browser/releases/download/"
            f"{release_tag}/void-browser.exe"
        )
        portable_line = f"$env:VOID_RPA_PORTABLE_URL = '{url}'\n"
    # Never pass an empty -ExePath (argparse --exe with no value). Prefer letting
    # DownloadInstall / dist discovery supply the binary.
    return f"""$ErrorActionPreference = 'Continue'
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
$env:PYTHONWARNINGS = 'ignore'
{portable_line}Set-Location '{remote_root}'
$exePath = Join-Path '{remote_root}' 'dist\\void-browser.exe'
$extra = @()
if ((Test-Path $exePath) -and ('{dl_flag}' -eq '')) {{
  $extra += @('-ExePath', $exePath)
}}
$code = 1
try {{
  & '.\\scripts\\run-rpa-windows.ps1' @extra -Scenarios '{scenarios}' -LaunchWait {launch_wait} {dl_flag} *>&1 |
    Tee-Object -FilePath '{status_dir}\\run.log'
  $code = $LASTEXITCODE
}} catch {{
  $_ | Out-String | Tee-Object -FilePath '{status_dir}\\run.log' -Append
  $code = 1
}}
if ($null -eq $code) {{ $code = 1 }}
$latest = Get-ChildItem (Join-Path '{remote_root}' 'artifacts\\rpa') -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -First 1
@{{
  exit_code = $code
  finished_at = (Get-Date).ToString('o')
  artifact_dir = if ($latest) {{ $latest.FullName }} else {{ $null }}
  log = '{status_dir}\\run.log'
}} | ConvertTo-Json | Set-Content -Path '{status_dir}\\done.json' -Encoding UTF8
"""


def schedule_and_wait(
    sess,
    *,
    remote_root: str,
    scenarios: str,
    download_install: bool,
    launch_wait: float,
    timeout_sec: int,
    release_tag: str,
) -> dict:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    status = f"C:\\Users\\rpa-win\\void-rpa-status\\{run_id}"
    wrapper_path = f"{status}\\run.ps1"
    wrapper = build_wrapper(
        remote_root=remote_root,
        scenarios=scenarios,
        launch_wait=launch_wait,
        download_install=download_install,
        status_dir=status,
        release_tag=release_tag,
    )

    print(
        f"[rpa] scheduling interactive run {run_id} "
        f"(DownloadInstall={int(download_install)})...",
        flush=True,
    )
    write_remote_bytes(sess, wrapper_path, wrapper.encode("utf-8"))

    code, out, err = run_ps(
        sess,
        f"""
$ErrorActionPreference = 'Stop'
$wrapper = {ps_quote(wrapper_path)}
$taskName = 'VoidBrowserRPA'
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
  "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`""
)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -Compatibility Win8
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
$q = (query user 2>&1 | Out-String)
if ($q -notmatch 'Active') {{
  Write-Output 'ERROR: No Active console session. Autologon should land rpa-win on an unlocked desktop after reboot.'
  Write-Output 'Check Winlogon AutoAdminLogon on the VM (scripts/ci/apply-rpa-autologon.py) or VNC http://10.0.0.10:5702/ for diagnosis only.'
  Write-Output $q
  throw 'RPA requires Active console session (Session 1). Autologon missing or Windows Update still running.'
}}
Start-ScheduledTask -TaskName $taskName
Write-Output "Started $taskName wrapper=$wrapper"
""",
    )
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    if code != 0:
        raise RuntimeError(f"schedule failed: {err or out}")

    done_path = f"{status}\\done.json"
    deadline = time.time() + timeout_sec
    print(f"[rpa] waiting up to {timeout_sec}s for done.json...", flush=True)
    done_obj = None
    while time.time() < deadline:
        time.sleep(8)
        _c, o, _e = run_ps(
            sess,
            f"""
if (Test-Path {ps_quote(done_path)}) {{ Get-Content -Raw {ps_quote(done_path)} }}
else {{ Write-Output '' }}
""",
        )
        text = (o or "").strip()
        if text:
            done_obj = json.loads(text)
            break
        print(f"  ... still running ({int(deadline - time.time())}s left)", flush=True)

    if not done_obj:
        raise TimeoutError(f"Timed out after {timeout_sec}s waiting for RPA done.json")

    print(
        f"[rpa] exit_code={done_obj.get('exit_code')} "
        f"artifact_dir={done_obj.get('artifact_dir')}",
        flush=True,
    )
    log_path = done_obj.get("log") or f"{status}\\run.log"
    _c, o, _e = run_ps(
        sess,
        f"""
if (Test-Path {ps_quote(log_path)}) {{ Get-Content {ps_quote(log_path)} -Tail 80 | Out-String }}
""",
    )
    print("---- remote log (tail) ----")
    print(o)
    return done_obj


def pull_artifacts(sess, artifact_dir: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    print(f"[rpa] pulling artifacts -> {local_dir}", flush=True)
    code, out, err = run_ps(
        sess,
        f"""
$ErrorActionPreference = 'Stop'
Get-ChildItem {ps_quote(artifact_dir)} -File | ForEach-Object {{ $_.Name }}
""",
    )
    if code != 0:
        raise RuntimeError(f"list artifacts failed: {err or out}")
    names = [n.strip() for n in out.splitlines() if n.strip()]
    for name in names:
        remote = f"{artifact_dir}\\{name}"
        code, out, err = run_ps(
            sess,
            f"""
$ErrorActionPreference = 'Stop'
$bytes = [IO.File]::ReadAllBytes({ps_quote(remote)})
[Convert]::ToBase64String($bytes)
""",
        )
        if code != 0:
            print(f"  SKIP {name}: {err or out}", file=sys.stderr)
            continue
        b64 = "".join(out.split())
        (local_dir / name).write_bytes(base64.b64decode(b64))
        print(f"  {name} ({(local_dir / name).stat().st_size} bytes)")


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
            dest = base / stamp
            dest.mkdir(parents=True, exist_ok=True)
            for f in local_dir.iterdir():
                if f.is_file():
                    (dest / f.name).write_bytes(f.read_bytes())
            print(f"[rpa] mirrored to {dest}")
            return
        except OSError as e:
            print(f"[rpa] mirror skip {base}: {e}")


def wait_for_github_release(tag: str, timeout_sec: int = 1200) -> None:
    if not tag:
        return
    import urllib.request

    portable = (
        f"https://github.com/dadhatdaniel/void-browser/releases/download/{tag}/void-browser.exe"
    )
    latest = (
        "https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json"
    )
    print(f"[rpa] waiting for GitHub assets tag={tag} ...", flush=True)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        ok_p = ok_l = False
        for label, url in (("portable", portable), ("latest.json", latest)):
            try:
                req = urllib.request.Request(
                    url, method="HEAD", headers={"User-Agent": "VoidBrowser-CI/1.0"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if getattr(resp, "status", 200) < 400:
                        if label == "portable":
                            ok_p = True
                        else:
                            ok_l = True
            except Exception as e:  # noqa: BLE001
                print(f"  {label} not ready: {e}")
        if ok_p and ok_l:
            print("[rpa] GitHub release assets ready")
            return
        time.sleep(20)
    raise TimeoutError(f"GitHub assets for {tag} not ready after {timeout_sec}s")


def main() -> int:
    _require_winrm()
    parser = argparse.ArgumentParser(description="WinRM RPA trigger for rpa-win VM")
    parser.add_argument("--remote-root", default=r"C:\void-browser")
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    parser.add_argument("--download-install", action="store_true")
    parser.add_argument("--sync-repo", action="store_true")
    parser.add_argument("--sync-repo-only", action="store_true")
    parser.add_argument("--no-push-harness", action="store_true",
                        help="Do not base64-push harness files (default: push off; use --push-harness)")
    parser.add_argument(
        "--push-harness",
        action="store_true",
        help="Force-push local harness files over WinRM (slow; prefer git sync on main)",
    )
    parser.add_argument("--launch-wait", type=float, default=4.0)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--release-tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--wait-github", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "artifacts" / "rpa"))
    args = parser.parse_args()

    sess = session_from_env()

    if args.sync_repo_only:
        sync_repo(sess, args.remote_root)
        if args.push_harness and not args.no_push_harness:
            push_harness_files(sess, args.remote_root, ROOT)
        print("[rpa] sync-repo-only done")
        return 0

    tag = (args.release_tag or "").strip()
    if args.wait_github or (tag and args.download_install):
        wait_for_github_release(
            tag, timeout_sec=int(os.environ.get("RPA_WAIT_GITHUB_SEC", "1200"))
        )

    if args.sync_repo:
        sync_repo(sess, args.remote_root)
    if args.push_harness and not args.no_push_harness:
        push_harness_files(sess, args.remote_root, ROOT)

    done = schedule_and_wait(
        sess,
        remote_root=args.remote_root,
        scenarios=args.scenarios,
        download_install=args.download_install,
        launch_wait=args.launch_wait,
        timeout_sec=args.timeout_sec,
        release_tag=tag,
    )

    art = done.get("artifact_dir")
    if not art:
        print("[rpa] no artifact_dir", file=sys.stderr)
        return int(done.get("exit_code") or 1)

    stamp = Path(str(art).replace("\\", "/")).name
    local_dir = Path(args.out) / stamp
    pull_artifacts(sess, art, local_dir)
    mirror_to_unraid(local_dir, stamp)

    report = local_dir / "report.json"
    if report.is_file():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            print(f"[rpa] report ok={data.get('ok')} dir={local_dir}")
            for sc in data.get("scenarios") or []:
                mark = "PASS" if sc.get("ok") else "FAIL"
                print(f"  [{mark}] {sc.get('name')} ({sc.get('duration_sec')}s)")
        except Exception as e:  # noqa: BLE001
            print(f"[rpa] could not parse report: {e}")

    return int(done.get("exit_code") or 1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        raise SystemExit(1)
