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
# Force-minimize this console so it cannot cover Void / steal mouse clicks.
try {{
  Add-Type -Name RpaWin -Namespace VoidRpa -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
'@ -ErrorAction SilentlyContinue
  $hwnd = [VoidRpa.RpaWin]::GetConsoleWindow()
  if ($hwnd -ne [IntPtr]::Zero) {{ [void][VoidRpa.RpaWin]::ShowWindow($hwnd, 6) }}
}} catch {{ }}
{portable_line}Set-Location '{remote_root}'
$exePath = Join-Path '{remote_root}' 'dist\\void-browser.exe'
$extra = @()
if ((Test-Path $exePath) -and ('{dl_flag}' -eq '')) {{
  $extra += @('-ExePath', $exePath)
}}
$code = 1
try {{
  # Capture native exit code BEFORE Tee-Object; piping can clear $LASTEXITCODE.
  & '.\\scripts\\run-rpa-windows.ps1' @extra -Scenarios '{scenarios}' -LaunchWait {launch_wait} {dl_flag} *> '{status_dir}\\run.log'
  if ($null -ne $LASTEXITCODE) {{ $code = [int]$LASTEXITCODE }}
  Get-Content -Path '{status_dir}\\run.log' -ErrorAction SilentlyContinue
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
# Minimized so the console does not cover Void / steal Ctrl+L keystrokes mid-suite.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
  "-NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File `"$wrapper`""
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
        print(
            f"[rpa] timed out after {timeout_sec}s — aborting guest task + writing done.json",
            flush=True,
        )
        log_quote = ps_quote(status + "\\run.log")
        root_quote = ps_quote(remote_root)
        done_quote = ps_quote(done_path)
        abort_ps = f"""
$ErrorActionPreference = 'Continue'
Stop-ScheduledTask -TaskName 'VoidBrowserRPA' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'VoidBrowserRPA' -Confirm:$false -ErrorAction SilentlyContinue
foreach ($n in @('void-browser','python','pythonw','uninstall')) {{
  Get-Process -Name $n -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}}
$latest = Get-ChildItem (Join-Path {root_quote} 'artifacts\\rpa') -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -First 1
@{{
  exit_code = 98
  finished_at = (Get-Date).ToString('o')
  aborted = $true
  reason = 'ci-wait-timeout'
  artifact_dir = if ($latest) {{ $latest.FullName }} else {{ $null }}
  log = {log_quote}
}} | ConvertTo-Json | Set-Content -Path {done_quote} -Encoding UTF8
Get-Content -Raw {done_quote}
"""
        _c, o, _e = run_ps(sess, abort_ps)
        text = (o or "").strip()
        if text:
            try:
                done_obj = json.loads(text)
            except json.JSONDecodeError:
                done_obj = None
        if not done_obj:
            raise TimeoutError(
                f"Timed out after {timeout_sec}s waiting for RPA done.json "
                "(guest abort also failed)"
            )

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


# Suite-startup cleanup on the guest (orphans, hung tasks, abort stale done.json).
SELF_HEAL_CLEANUP_PS = r"""
$ErrorActionPreference = 'Continue'
Write-Output '=== SELF_HEAL_CLEANUP ==='

# Kill orphan Void Browser / WebView2 / hung RPA python
foreach ($n in @('void-browser','Void','msedgewebview2','msiexec')) {
  Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Output ("KILL name={0} pid={1}" -f $_.ProcessName, $_.Id)
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
  }
}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $_.CommandLine -and (
    $_.CommandLine -match 'runner\.py|run-rpa-windows|pywinauto|void-rpa-status'
  )
} | ForEach-Object {
  Write-Output ("KILL pid={0} name={1}" -f $_.ProcessId, $_.Name)
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

# Stop / unregister hung RPA scheduled tasks
Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
  $_.TaskName -match 'void|rpa|Void|RPA'
} | ForEach-Object {
  if ($_.State -eq 'Running') {
    Write-Output ("STOP_TASK={0}" -f $_.TaskName)
    Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue
  }
  if ($_.TaskName -eq 'VoidBrowserRPA') {
    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output 'UNREGISTERED=VoidBrowserRPA'
  }
}

# Abort incomplete prior runs so CI waiters cannot hang on stale status dirs
$statusRoot = 'C:\Users\rpa-win\void-rpa-status'
if (Test-Path $statusRoot) {
  Get-ChildItem $statusRoot -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object {
      $done = Join-Path $_.FullName 'done.json'
      if (-not (Test-Path $done)) {
        @{
          exit_code = 99
          finished_at = (Get-Date).ToString('o')
          aborted = $true
          reason = 'suite-self-heal'
          artifact_dir = $null
        } | ConvertTo-Json -Compress | Set-Content -Path $done -Encoding UTF8
        Write-Output ("ABORT_DONE={0}" -f $done)
      }
    }
}

# Best-effort dismiss Update available / modal focus steals
try {
  $wshell = New-Object -ComObject wscript.shell
  $null = $wshell.AppActivate('Void')
  Start-Sleep -Milliseconds 150
  $wshell.SendKeys('{ESC}')
  Start-Sleep -Milliseconds 80
  $wshell.SendKeys('{ESC}')
  Write-Output 'DISMISS_ESC=ok'
} catch {
  Write-Output ('DISMISS_ESC=skip ' + $_)
}
Write-Output 'SELF_HEAL_CLEANUP_OK'
"""


def probe_session_health(sess) -> tuple[bool, str]:
    """Return (healthy, detail) for Active console + non-black desktop."""
    code, out, err = run_ps(
        sess,
        r"""
$ErrorActionPreference = 'Continue'
$q = (query user 2>&1 | Out-String)
Write-Output '---QUERY---'
Write-Output $q
$exp = Get-Process explorer -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Output ("explorer_session=" + $(if ($exp) { $exp.SessionId } else { 'none' }))
$active = ($q -match 'Active')
Write-Output ("active=" + $active)

# Sample primary screen luminance (black / locked desktop → mean ~0)
$mean = -1
try {
  Add-Type -AssemblyName System.Windows.Forms,System.Drawing -ErrorAction Stop
  $b = [Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object Drawing.Bitmap $b.Width, $b.Height
  $g = [Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.Location, [Drawing.Point]::Empty, $b.Size)
  $g.Dispose()
  $sum = 0L; $n = 0
  for ($y = 0; $y -lt $bmp.Height; $y += 24) {
    for ($x = 0; $x -lt $bmp.Width; $x += 24) {
      $c = $bmp.GetPixel($x, $y)
      $sum += ([int]$c.R + [int]$c.G + [int]$c.B) / 3
      $n++
    }
  }
  $bmp.Dispose()
  if ($n -gt 0) { $mean = [math]::Round(($sum / $n), 1) }
} catch {
  Write-Output ("screen_probe_err=" + $_)
}
Write-Output ("screen_mean=" + $mean)
if (-not $active) {
  Write-Output 'HEALTH=no_active_session'
} elseif ($mean -ge 0 -and $mean -lt 8) {
  Write-Output 'HEALTH=black_framebuffer'
} else {
  Write-Output 'HEALTH=ok'
}
""",
    )
    text = (out or "") + "\n" + (err or "")
    print(text, flush=True)
    if "HEALTH=ok" in text:
        return True, "Active session + desktop not black"
    if "HEALTH=black_framebuffer" in text:
        return False, "black framebuffer / locked desktop"
    if "HEALTH=no_active_session" in text or "Active" not in text:
        return False, "no Active console session (Autologon?)"
    # Probe failed to classify — treat Active + explorer as good enough
    if "Active" in text and "explorer_session=none" not in text:
        return True, "Active session (screen probe inconclusive)"
    return False, f"unhealthy (exit={code})"


def reboot_and_wait_active(timeout_sec: int = 360) -> None:
    """Reboot rpa-win once and wait for WinRM + Active console."""
    print("[rpa] issuing Restart-Computer -Force ...", flush=True)
    try:
        run_ps(session_from_env(), "Restart-Computer -Force")
    except Exception as e:  # noqa: BLE001
        print(f"[rpa] reboot connection dropped (expected): {e}", flush=True)
    time.sleep(15)
    deadline = time.time() + timeout_sec
    print(f"[rpa] waiting for WinRM + Active session (max {timeout_sec}s)...", flush=True)
    while time.time() < deadline:
        try:
            sess = session_from_env()
            code, out, _err = run_ps(sess, "Write-Output 'pong'")
            if code == 0 and "pong" in out:
                ok, detail = probe_session_health(sess)
                if ok:
                    print(f"[rpa] post-reboot healthy: {detail}", flush=True)
                    return
                print(f"[rpa] WinRM up but not healthy yet: {detail}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[rpa] wait: {e}", flush=True)
        time.sleep(8)
    raise TimeoutError(f"Guest did not become healthy within {timeout_sec}s after reboot")


def self_heal(sess, *, allow_reboot: bool = True) -> None:
    """
    Suite startup self-heal on void-rpa-windows:
      1) kill orphan Void / stuck RPA processes
      2) dismiss update/modal focus (best-effort)
      3) require Active interactive session (Autologon)
      4) if framebuffer dead / no desktop → reboot once, then continue
      5) clear hung previous runs (done.json / scheduled tasks)
    """
    print("[rpa] suite self-heal starting...", flush=True)
    code, out, err = run_ps(sess, SELF_HEAL_CLEANUP_PS)
    print(out, flush=True)
    if err.strip():
        print(err, file=sys.stderr)
    if "SELF_HEAL_CLEANUP_OK" not in out:
        print(f"[rpa] self-heal cleanup warning (exit {code})", flush=True)

    ok, detail = probe_session_health(sess)
    if ok:
        print(f"[rpa] self-heal OK: {detail}", flush=True)
        return

    print(f"[rpa] desktop unhealthy: {detail}", flush=True)
    if not allow_reboot:
        raise RuntimeError(
            f"RPA guest unhealthy and reboot disabled: {detail}. "
            "Re-apply Autologon (scripts/ci/apply-rpa-autologon.py) or open VNC for diagnosis."
        )

    print("[rpa] rebooting guest once to recover session/framebuffer...", flush=True)
    reboot_and_wait_active()
    sess2 = session_from_env()
    code, out, err = run_ps(sess2, SELF_HEAL_CLEANUP_PS)
    print(out, flush=True)
    ok2, detail2 = probe_session_health(sess2)
    if not ok2:
        raise RuntimeError(
            f"RPA guest still unhealthy after reboot: {detail2}. "
            "Check Autologon / Windows Update / VNC http://10.0.0.10:5702/"
        )
    print(f"[rpa] self-heal recovered after reboot: {detail2}", flush=True)


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
    parser.add_argument(
        "--no-self-heal",
        action="store_true",
        help="Skip suite-startup self-heal (orphans / session / one reboot)",
    )
    args = parser.parse_args()

    sess = session_from_env()
    skip_heal = args.no_self_heal or os.environ.get("VOID_RPA_SKIP_SELF_HEAL", "").strip() in (
        "1",
        "true",
        "yes",
    )

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

    if not skip_heal:
        # Reconnect after possible reboot inside self_heal
        self_heal(sess, allow_reboot=True)
        sess = session_from_env()

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

    def _exit_code() -> int:
        # Do not use `x or 1` — exit_code 0 is success and must be preserved.
        ec = done.get("exit_code")
        return 1 if ec is None else int(ec)

    if not art:
        print("[rpa] no artifact_dir", file=sys.stderr)
        return _exit_code()

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

    return _exit_code()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        raise SystemExit(1)
