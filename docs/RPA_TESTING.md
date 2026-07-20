# RPA / E2E testing (Windows)

Automated UI tests for Void Browser using **pywinauto** (UI Automation).
Produces screenshots + JSON for humans and Cursor agent quality loops.

Linux/Unraid Docker **cannot** run this harness (no WebView2 GUI). See
[TEST_VMS.md](./TEST_VMS.md) for host/VM options.

## Quick start (this Windows PC)

```powershell
cd Z:\openclaw-localai\workspace\void-browser

# Optional: build first
.\scripts\run-rpa-windows.ps1 -Build -SmokeOnly

# Full suite (download + install + functionality)
.\scripts\run-rpa-windows.ps1 -DownloadInstall

# Subset
.\scripts\run-rpa-windows.ps1 -Scenarios smoke_navigate,settings_preserves_tab
```

Requires:

- Built `void-browser.exe` (or `-Build` / `-DownloadInstall`), or `dist\void-browser.exe`
- Interactive desktop session (not headless SSH)
- Python 3.10+ (`py -3` or `python`)
- WebView2 Runtime
- Network access to `http://10.0.0.10:5080/releases.json` and GitHub release assets

## Remote RPA (Unraid VM `void-rpa-windows`)

Guest: hostname `RPA-WIN-VM` / `JULIARIG`, IP **`10.0.0.28`**, user **`rpa-win`**.
Access: WinRM **5985** (preferred), RDP 3389, VNC `http://10.0.0.10:5702/`.
Passwords are **not** stored in git — use env vars or a local secret file.

### One-time client setup (DANIELRIG)

```powershell
# Add VM to TrustedHosts (elevated once)
Set-Item WSMan:\localhost\Client\TrustedHosts -Value 'juliarig,juliarig.local,10.0.2.145,10.0.0.28,rpa-win-vm' -Force

# Credentials for the remote runner (session only — do not commit)
$env:VOID_RPA_WIN_HOST = '10.0.0.28'
$env:VOID_RPA_WIN_USER = 'rpa-win'
$env:VOID_RPA_WIN_PASS = '<password>'
```

Ensure the guest has an **Active console session** (log in as `rpa-win` via VNC if needed). UI Automation requires an interactive desktop.

### Trigger from DANIELRIG

```powershell
cd Z:\openclaw-localai\workspace\void-browser

# Full suite: download latest build, install/stage, run all functionality tests
.\scripts\rpa-remote-run.ps1 -DownloadInstall

# Smoke only
.\scripts\rpa-remote-run.ps1 -SmokeOnly

# Sync git main on the VM first, then run
.\scripts\rpa-remote-run.ps1 -SyncRepo -DownloadInstall

# Subset
.\scripts\rpa-remote-run.ps1 -Scenarios smoke_navigate,settings_preserves_tab
```

What it does:

1. WinRM to the VM; pushes latest `tests/rpa/runner.py` + `scripts/run-rpa-windows.ps1`.
2. Registers a **scheduled task** that runs in the interactive session.
3. Runs `scripts\run-rpa-windows.ps1` on `C:\void-browser` (default: full scenario set).
4. Pulls `artifacts\rpa\<timestamp>\` (report.json + PNGs) into the local workspace via WinRM `Copy-Item -FromSession`.
5. Optionally mirrors a copy under `Z:\void-rpa-artifacts\<timestamp>\` when that folder exists on the Unraid `appdata` share.

### Guest layout

| Path | Purpose |
|------|---------|
| `C:\void-browser` | Git clone (GitLab `lightfootcloud/void-browser` or GitHub) |
| `C:\void-browser\dist\void-browser.exe` | Binary under test (staged from download or release) |
| `C:\void-browser\downloads\rpa\` | Fresh portable + NSIS setup downloads |
| `%USERPROFILE%\void-rpa-venv` | Python venv (pywinauto, Pillow, psutil) |
| `C:\void-browser\artifacts\rpa\<stamp>\` | On-VM artifacts before pull |
| `C:\Users\rpa-win\void-rpa-status\<stamp>\` | Remote run logs / `done.json` |

## Scenarios

| Name | What it checks |
|------|----------------|
| `download_install` | Fetch `releases.json` from LAN `:5080`, download portable `void-browser.exe` + Windows setup from GitHub URLs, screenshot download folder, stage into `dist\`, optional NSIS `/S`, require alpha.16+ |
| `app_launch` | Process alive, window ≥640×480, chrome/content not blank/white |
| `smoke_navigate` | example.com + duckduckgo; **fail hard** if content area is blank/white |
| `nav_history` | Address-bar nav, Alt+Left back, Alt+Right forward, F5 reload; blank fail |
| `visit_void_site` | **LAN** `http://10.0.0.10:5080/` hero → scroll → `#download`; optional public URL |
| `settings_preserves_tab` | open site → Settings (Ctrl+,) overlay → Esc; **fail hard** if tab wiped to blank |
| `new_tab` | Ctrl+T second tab; Ctrl+Shift+Tab switch back; blank fail |
| `youtube_signin_page` | Load `accounts.google.com/signin` UI (no password unless env set); blank fail |
| `context_menu` | URL bar clipboard Ctrl+C / Ctrl+V |

Default suite runs **all** of the above (in that order). After `download_install`, the harness relaunches on the staged build.

### Download notes

- LAN nginx at `:5080` serves `releases.json` with GitHub asset URLs. Direct paths like `/releases/*.exe` on the LAN host may SPA-fallback to HTML — the harness rejects HTML downloads and uses GitHub.
- Portable `void-browser.exe` is preferred for functional tests; NSIS setup is also fetched and optionally installed with `/S`.
- Override URLs with `VOID_RPA_PORTABLE_URL` / `VOID_RPA_SETUP_URL` / `VOID_RELEASES_JSON_URL` if needed.
- Set `VOID_RPA_RUN_NSIS=1` to also run the downloaded setup with `/S` (portable is preferred for functional tests).

`visit_void_site` dogfoods the marketing site inside Void Browser itself.

- **Primary:** `http://10.0.0.10:5080/` (Unraid nginx) — avoids Cloudflare bot challenges.
- **Optional:** `https://void.lightfoot.cloud/` — soft check (`allow_failure`); CF challenge does not fail the suite.
- Override with `VOID_SITE_LAN_URL` / `VOID_SITE_PUBLIC_URL` env vars if needed.

Optional Google login (local/CI secrets only — never commit):

```powershell
$env:VOID_TEST_GOOGLE_USER = "you@example.com"
$env:VOID_TEST_GOOGLE_PASS = "..."
```

## Artifacts

```
artifacts/rpa/<YYYYMMDD_HHMMSS>/
  report.json          # machine-readable steps + pass/fail
  01_....png
  02_...
```

After a remote run, the same tree appears under the local repo (and optionally
`Z:\void-rpa-artifacts\<stamp>\`).

`report.json` fields: `ok`, `scenarios[].steps[].screenshot`, `agent_review_hints`.

Blank detection: Pillow samples the content region (below chrome). Near-white /
near-zero variance → step fails; settings wipe → suite fails.

## Agent quality loop

After an RPA run (or CI artifact download):

1. Find latest dir: `artifacts/rpa/*/` (or CI download).
2. Read `report.json`.
3. **Read each `*.png` with the Read tool** (vision) — do not rely on filenames alone.
4. File bugs for failed steps or visually broken UI (blank content, settings killed the tab, Google block page, etc.).
5. Fix → rebuild / publish → refresh VM `dist\void-browser.exe` → re-run:

```powershell
.\scripts\rpa-remote-run.ps1 -DownloadInstall -Scenarios <failed>
```

Suggested agent prompt:

> Review `artifacts/rpa/<stamp>/report.json` and all PNGs. Summarize failures and open fixes for settings/YouTube/navigation regressions.

## CI

GitHub Actions job `rpa-windows` (workflow_dispatch / nightly) builds Windows,
runs a smoke subset, uploads `artifacts/rpa/**`. Initially `continue-on-error`
may be set until the runner GUI is reliable.

GitLab runners on Unraid are Linux — use them for `cargo test`, not RPA.

## Related

- [GOOGLE_SIGNIN.md](./GOOGLE_SIGNIN.md) — auth / WebView2 limits
- [TEST_VMS.md](./TEST_VMS.md) — Unraid VMs vs local PC vs GHA
- [LOCAL_WINDOWS_DEV.md](./LOCAL_WINDOWS_DEV.md) — build loop
