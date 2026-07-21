# RPA / E2E testing (Windows)

Automated UI tests for Void Browser using **pywinauto** (UI Automation).
Produces screenshots + JSON for humans and Cursor agent quality loops.

Linux/Unraid Docker **cannot** run this harness (no WebView2 GUI). See
[TEST_VMS.md](./TEST_VMS.md) for host/VM options.

## Is RPA automated on release?

| Path | Today |
|------|--------|
| GitLab `rpa-windows` (WinRM → `10.0.0.28`) after GitHub Release | **Yes** — `sync-website-releases-to-gitlab.sh` POSTs `RPA_AFTER_RELEASE=1` |
| GitLab `rpa-linux` (SSH → `10.0.1.114`) after GitHub Release | **Yes** — same trigger; full-ish AppImage suite (xdotool) |
| GitHub Actions `RPA Windows` after release | **Yes** — Build & Release `workflow_dispatch`es with `release_tag` (needs Environment secret `GH_WORKFLOW_TOKEN`). Plain `on.release` alone is unreliable: `GITHUB_TOKEN`-created releases do not start sibling workflows. |
| GitHub Actions nightly / `workflow_dispatch` | Yes |
| Unraid cron / local `rpa-after-release.ps1` | Optional backup |

**Full chain:** [RELEASE_PIPELINE.md](./RELEASE_PIPELINE.md)

**Trigger chain for a new tagged release:**

1. Tag + push on GitLab (`v*`) → `trigger-github-build` kicks **Build & Release**.
2. Build & Release publishes installers + signed `latest.json`.
3. Release job syncs `website/releases.json` to GitLab → **deploy-site**.
4. Same sync POSTs GitLab pipeline `RPA_AFTER_RELEASE=1` → **rpa-windows** (WinRM) + **rpa-linux** (SSH).
5. Secondary: Build & Release dispatches hosted **RPA Windows** (`release_tag=v*`) via `GH_WORKFLOW_TOKEN`.

Requires GitLab CI variables **`VOID_RPA_WIN_PASS`** and **`VOID_RPA_LINUX_PASS`** (masked). Windows VM uses **rpa-win Autologon**; Linux VM uses **GDM autologon** for `rpa-linux` (see [TEST_VMS.md](./TEST_VMS.md)).

## Quick start (this Windows PC)

```powershell
cd Z:\openclaw-localai\workspace\void-browser

# Optional: build first
.\scripts\run-rpa-windows.ps1 -Build -SmokeOnly

# Full suite (download + install + functionality + auto_update)
.\scripts\run-rpa-windows.ps1 -DownloadInstall

# Subset
.\scripts\run-rpa-windows.ps1 -Scenarios smoke_navigate,settings_preserves_tab,auto_update
```

Requires:

- Built `void-browser.exe` (or `-Build` / `-DownloadInstall`), or `dist\void-browser.exe`
- Interactive desktop session (not headless SSH)
- Python 3.10+ (`py -3` or `python`)
- WebView2 Runtime
- Network access to `http://10.0.0.10:5080/releases.json` and GitHub release assets
- For `auto_update`: HTTPS to GitHub `latest.json` + an older portable (default `v0.1.0-alpha.15`)

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

UI Automation needs an interactive desktop. **rpa-win Autologon** provides that after reboot; you do **not** need to open/leave VNC. If the job fails with “No Active console session”, re-apply Autologon (`scripts/ci/apply-rpa-autologon.py`) or check that Windows Update is not still mid-install.

### Trigger from DANIELRIG

```powershell
cd Z:\openclaw-localai\workspace\void-browser

# Full suite: download latest build, install/stage, run all functionality tests
.\scripts\rpa-remote-run.ps1 -DownloadInstall

# After a GitHub Release (Path B — recommended companion to GHA)
.\scripts\rpa-after-release.ps1
.\scripts\rpa-after-release.ps1 -SyncRepo

# Smoke only
.\scripts\rpa-remote-run.ps1 -SmokeOnly

# Sync git main on the VM first, then run
.\scripts\rpa-remote-run.ps1 -SyncRepo -DownloadInstall

# Subset
.\scripts\rpa-remote-run.ps1 -Scenarios smoke_navigate,settings_preserves_tab,auto_update
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
| `C:\void-browser\downloads\rpa\old\` | Older portable used by `auto_update` |
| `%USERPROFILE%\void-rpa-venv` | Python venv (pywinauto, Pillow, psutil) |
| `C:\void-browser\artifacts\rpa\<stamp>\` | On-VM artifacts before pull |
| `C:\Users\rpa-win\void-rpa-status\<stamp>\` | Remote run logs / `done.json` |

## Scenarios

| Name | What it checks |
|------|----------------|
| `download_install` | Fetch `releases.json` from LAN `:5080`, download portable `void-browser.exe` + Windows setup from GitHub URLs, screenshot download folder, stage into `dist\`, silent NSIS `/S` (skip with `VOID_RPA_SKIP_NSIS=1`), require alpha.16+ |
| `app_launch` | Process alive, window ≥640×480, chrome/content not blank/white |
| `smoke_navigate` | example.com + duckduckgo; **fail hard** if content area is blank/white |
| `nav_history` | Address-bar nav, Alt+Left back, Alt+Right forward, F5 reload; blank fail |
| `visit_void_site` | **LAN** XP theatrical site via `?skip=1`: desktop + Start/taskbar, Void Browser icon, Welcome/Get Void CTAs, Download window + `releases.json` buttons; screenshots; optional public URL |
| `void_xp_desktop` | Bare LAN URL → **Esc** lands on desktop; soft easter (Recycle Bin dblclick or Start → Command Prompt) |
| `settings_preserves_tab` | open site → Settings (Ctrl+,) overlay → Esc; **fail hard** if tab wiped to blank |
| `new_tab` | Ctrl+T second tab; Ctrl+Shift+Tab switch back; blank fail |
| `youtube_signin_page` | Load `accounts.google.com/signin` (retries + new tab); **hard fail** if stuck on Void New Tab; **soft_fail** if navigated but live Google/WebView2 challenge hides classic white card |
| `context_menu` | URL bar clipboard Ctrl+C / Ctrl+V |
| `adblock_blocks_ads` | Local fixture with fake ad URLs; asserts `VOID_ADBLOCK_DEBUG` network log blocked >=3 expected hosts; writes `network-log.json` |
| `auto_update` | Fetch GitHub `latest.json` (**fail on 404 / wrong URLs**); stage older portable (default `v0.1.0-alpha.15`); launch → startup quiet check and/or Settings **Check for updates**; assert **Update available** dialog; screenshot; click **Install & Relaunch** |

Default suite runs **all** of the above (in that order). After `download_install`, the harness relaunches on the staged build. `auto_update` runs last so it can temporarily switch to an older build, then restores the previous exe. **Teardown** always runs `uninstall_void_browser` (registry QuietUninstall/`/S`) so the VM does not keep Void Browser installed — even if scenarios fail.

Unexpected **Update available** dialogs during other scenarios (e.g. suite still on alpha.N while `latest.json` advertises alpha.N+1) are handled two ways:

1. **Preferred (alpha.18+):** RPA launches Void with `VOID_DISABLE_UPDATER=1` (or `--disable-updater`) so the quiet startup check never runs. `auto_update` clears that env so the dialog can appear.
2. **Fallback (current release binaries):** `dismiss_update_dialogs_until_clear` clicks **Later** after launch / promote / before each non-`auto_update` scenario, covering the quiet-check race (~4s + network).

Each scenario also has a hard wall-clock timeout (`VOID_RPA_SCENARIO_TIMEOUT`, default **180s**) so a stuck modal cannot wedge the suite until the CI 30-minute job timeout.

### `auto_update` details

1. `GET https://github.com/dadhatdaniel/void-browser/releases/latest/download/latest.json`
2. Require `platforms.windows-x86_64.url` on `github.com/dadhatdaniel/void-browser/releases/download/...` plus a signature.
3. Download older portable (`VOID_RPA_OLD_TAG`, default `v0.1.0-alpha.15`, or first older release that ships `void-browser.exe`).
4. Relaunch older build; wait for startup quiet update prompt (~4s) or open Settings and click **Check for updates**.
5. Screenshot the dialog; click **Install & Relaunch** (accept path). Soft-reconnect if the process restarts.
6. Fail if `latest.json` is 404, URLs are wrong, older portable cannot download, or no update dialog appears.

Overrides:

| Env | Purpose |
|-----|---------|
| `VOID_RPA_LATEST_JSON_URL` | Override latest.json URL |
| `VOID_RPA_OLD_TAG` | Older release tag (default `v0.1.0-alpha.15`) |
| `VOID_RPA_OLD_PORTABLE_URL` | Pin exact older `void-browser.exe` URL |
| `VOID_DISABLE_UPDATER` | Set by harness on non-`auto_update` launches (app honors from alpha.18+) |
| `VOID_RPA_SCENARIO_TIMEOUT` | Per-scenario hard timeout seconds (default `180`) |
| `VOID_RPA_STRICT_YOUTUBE` | Set `1` to hard-fail `youtube_signin_page` (default: soft_fail / allow_failure for live Google + focus races) |

### Download notes

- LAN nginx at `:5080` serves `releases.json` with GitHub asset URLs. Direct paths like `/releases/*.exe` on the LAN host may SPA-fallback to HTML — the harness rejects HTML downloads and uses GitHub.
- Portable `void-browser.exe` is preferred for functional tests; NSIS setup is also fetched and installed with `/S` by default (set `VOID_RPA_SKIP_NSIS=1` to skip).
- Always-run teardown uninstalls any NSIS/MSI Void Browser via Uninstall registry keys (idempotent if already gone).
- Override URLs with `VOID_RPA_PORTABLE_URL` / `VOID_RPA_SETUP_URL` / `VOID_RELEASES_JSON_URL` if needed.
- Set `VOID_RPA_RUN_NSIS=1` explicitly if needed; `VOID_RPA_SKIP_NSIS=1` forces portable-only.

`visit_void_site` dogfoods the XP theatrical marketing site inside Void Browser itself.

- **Primary (CI-stable):** `http://10.0.0.10:5080/?skip=1` — skips BIOS/load/login; asserts desktop, Start, Void Browser, Welcome/Get Void, Download/`releases.json`.
- **Boot path:** `void_xp_desktop` opens bare `:5080/` then Esc → desktop; soft easter egg.
- **Optional:** `https://void.lightfoot.cloud/` — soft check (`allow_failure`); CF challenge does not fail the suite.
- Override with `VOID_SITE_LAN_URL` / `VOID_SITE_LAN_SKIP_URL` / `VOID_SITE_PUBLIC_URL` if needed.

Optional Google login (local/CI secrets only — never commit):

```powershell
$env:VOID_TEST_GOOGLE_USER = "you@example.com"
$env:VOID_TEST_GOOGLE_PASS = "..."
```

## Artifacts

```
artifacts/rpa/<YYYYMMDD_HHMMSS>/
  report.json            # machine-readable steps + pass/fail
  network.har            # HAR 1.2 (suite-wide; synthesized from Void decision log)
  network.jsonl          # raw decision stream (JSONL)
  network.json           # capped live snapshot
  network-summary.json   # blocked/allowed counts + review hints
  network-<scenario>.har # optional per-scenario snapshot after each scenario
  01_....png
  02_...
```

Unraid mirror (unchanged): full stamp tree including HAR/JSONL →
`/mnt/user/appdata/void-rpa-artifacts/<stamp>/` (Windows) or `linux-<stamp>/`.

### Network HAR capture (all RPA scenarios)

WebView2 does not expose a reliable Chromium DevTools HAR export to RPA.
Void synthesizes **HAR 1.2-compatible** JSON from the same stream used for
adblock diagnosis:

| Env | Role |
|-----|------|
| `VOID_RPA_NETWORK_CAPTURE=1` | Suite default (set `0` to opt out) |
| `VOID_NETWORK_CAPTURE=1` | Enables capture inside Void |
| `VOID_NETWORK_CAPTURE_LOG` | JSONL path (suite: `network.jsonl`) |
| `VOID_NETWORK_HAR_PATH` | Live/final HAR path (suite: `network.har`) |
| `VOID_ADBLOCK_DEBUG=1` | Legacy alias; still enables the same logger |

**Windows (primary):** `WebResourceRequested` + main-frame navigation decisions
(method, URL, resource type, blocked/allowed, filter). Blocked → HAR `status` 403;
allowed → `status` 0 (real HTTP status unknown without CDP). Custom `_void.*` fields
carry decision/filter/resourceType/sourceUrl.

**Linux (best-effort):** no WebResourceRequested hook; capture is mainly
document navigations that hit the adblock engine. Suite still writes `network.har`.

Harness rebuilds `network.har` from the full JSONL at suite end (and snapshots
`network-<scenario>.har` after each scenario).

### Adblock scenario (`adblock_blocks_ads`)

1. Temporarily redirects capture to a fixture-scoped JSONL under the artifact dir.
2. Serves `tests/fixtures/adblock/` on `127.0.0.1:<ephemeral>`.
3. Navigates to the fixture (passive fake ad/tracker URLs matching EasyList patterns).
4. Asserts >=3 expected ad hosts are `blocked` in the log, and the fixture page still paints.
5. Writes `network-log.json` + `network-adblock_blocks_ads.har` for diagnosis.

```powershell
.\scripts\run-rpa-windows.ps1 -Build -Scenarios adblock_blocks_ads
# or remote:
.\scripts\rpa-remote-run.ps1 -SyncRepo -Scenarios adblock_blocks_ads
```

Requires a build that includes WebView2 `WebResourceRequested` interception.
Older portables will produce an empty log and fail with a rebuild hint.

After a remote run, the same tree appears under the local repo (and optionally
`Z:\void-rpa-artifacts\<stamp>\`).

`report.json` fields: `ok`, `scenarios[].steps[].screenshot`, `agent_review_hints`.

Blank detection: Pillow samples the content region (below chrome). Near-white /
near-zero variance → step fails; settings wipe → suite fails.

## Agent quality loop

After an RPA run (or CI artifact download):

1. Find latest dir: `artifacts/rpa/*/` (or Unraid mirror / CI download).
2. Read `report.json`.
3. **Read each `*.png` with the Read tool** (vision) — do not rely on filenames alone.
4. On **FAILED** scenarios: also review `network.har` / `network-summary.json` /
   `network-<scenario>.har` for failed requests (`status>=400` except expected 403
   blocks), unexpected **allowed** trackers, and `about:blank` / empty document
   navigations that correlate with blank screenshots.
5. File bugs for failed steps or visually broken UI (blank content, settings killed the tab, Google block page, etc.).
6. Fix → rebuild / publish → refresh VM `dist\void-browser.exe` → re-run:

```powershell
.\scripts\rpa-remote-run.ps1 -DownloadInstall -Scenarios <failed>
```

Suggested agent prompt:

> Review `artifacts/rpa/<stamp>/report.json`, all PNGs, and `network.har` /
> `network-summary.json`. Summarize failures (UI + network) and open fixes for
> settings/YouTube/navigation/updater/adblock regressions.

## CI

### Path A — GitLab → rpa-win (primary, automatic after release)

Job: `rpa-windows` in `.gitlab-ci.yml` → `scripts/ci/rpa-winrm.py`.

| Event | Behavior |
|-------|----------|
| Pipeline var `RPA_AFTER_RELEASE=1` | Auto after GitHub release sync; waits for GitHub assets; full suite + `auto_update` |
| Manual play on `main` / `v*` | Same job, no new tag needed |
| `main` push | `sync-rpa-win-repo` (stage `sync`, before `rpa`) refreshes `C:\void-browser` |

Artifacts: full PNGs + `report.json` mirror to `/mnt/user/appdata/void-rpa-artifacts/<stamp>/` (runner share mount). **GitLab `artifacts:` upload stays disabled** on RPA jobs (coordinator 500 masks suite results). Optional local JPEG pack: `python3 scripts/ci/prepare-rpa-upload.py`.

Runner must bind-mount the Unraid share into job containers (`config.toml` → `runners.docker.volumes`):
`/mnt/user/appdata/void-rpa-artifacts:/mnt/user/appdata/void-rpa-artifacts:rw`.
CI sets `VOID_RPA_MIRROR_DIR` and fails `before_script` if the mount is missing.
Windows teardown uninstall is hard-capped (`VOID_RPA_TEARDOWN_TIMEOUT`, default **120s**) so a stuck NSIS uninstall cannot wedge `done.json` for the full CI wait.

**Suite self-heal** (WinRM / SSH wrappers at job start, before scheduling the suite):

1. Kill orphan `void-browser` / stuck RPA processes  
2. Dismiss update / focus-stealer dialogs (best-effort; Windows also dismisses inside the harness)  
3. Require Active interactive session (Windows Autologon) or GNOME/Xorg + `DISPLAY` (Linux)  
4. If framebuffer/desktop is dead (black screen / no session): **reboot guest once**, wait for healthy session, continue  
5. Clear hung previous runs (`done.json` abort + scheduled-task cleanup)

Skip with `--no-self-heal` or `VOID_RPA_SKIP_SELF_HEAL=1`.

### Path A2 — GitLab → rpa-linux (parallel full-ish suite)

Job: `rpa-linux` in `.gitlab-ci.yml` → `scripts/ci/rpa-ssh.py`.

Guest: `void-test-linux` / `rpa-linux` @ **`10.0.1.114`**. VNC diagnose: `http://10.0.0.10:5701/`.

| Event | Behavior |
|-------|----------|
| Pipeline var `RPA_AFTER_RELEASE=1` | Auto after GitHub release sync; full Linux suite |
| `website/releases.json` change on `main` | Auto (same rule as `rpa-windows`) |
| Manual play on `main` / `v*` | Same job |
| `main` push | `sync-rpa-linux-repo` (stage `sync`, before `rpa`) refreshes `/home/rpa-linux/void-browser` |

Default scenarios (Windows-parity names):
`download_install,app_launch,smoke_navigate,nav_history,visit_void_site,void_xp_desktop,settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update`

| Windows | Linux |
|---------|--------|
| `void-browser.exe` / NSIS | AppImage (+ optional `.deb` via `VOID_RPA_RUN_DEB=1`) |
| pywinauto UIA | xdotool + scrot (`tests/rpa/runner_linux.py`) — **AT-SPI does not expose Void chrome** on Ubuntu 24.04 WebKitGTK/Tauri |
| Blank-content UIA asserts | Pillow content-region sampling of screenshots |
| `auto_update` older portable + Install dialog | older AppImage + `latest.json` `linux-x86_64`; dialog accept is best-effort (no UIA tree) |
| NSIS uninstall teardown | kill processes + remove AppImages (`VOID_RPA_CLEANUP=1`) + optional `dpkg --purge` |
| `youtube_signin_page` soft_fail | same soft_fail semantics |

**Linux-impossible / limited:** real AT-SPI context menus, reliable Install & Relaunch button clicks, WebView2-style Google challenge classification. Omnibox focus uses click-chrome + Ctrl+L (not UIA Edit).

**QEMU guests (QXL / virtio-gpu without virgl):** WebKit HW compositing can paint a
**solid black upper half** of the webview. Root causes observed on `void-test-linux`:

1. Opaque chrome `body` fill when the shell child webview allocation is taller than
   the toolbar strip (fixed: `body.browsing { background: transparent }` + GTK
   `set_size_request` / raise).
2. `WEBKIT_FORCE_COMPOSITING_MODE` + broken DRI3 (fixed: never FORCE; auto soft-render
   on qxl/virtio_gpu/no-render-node; `WEBKIT_DISABLE_DMABUF_RENDERER=1`).
3. Guest user missing `video`/`render` groups (fixed in `configure-rpa-linux-desktop.sh`).

VM video is now **virtio** (+ optional egl-headless). Soft-render env still default in
`run-rpa-linux.sh` / `rpa-ssh.py` / `runner_linux._launch_env`:

- `VOID_SOFTWARE_RENDERING=1` → binary forces `WEBKIT_DISABLE_COMPOSITING_MODE=1`
- `LIBGL_ALWAYS_SOFTWARE=1`, `GALLIUM_DRIVER=llvmpipe`, `GSK_RENDERER=cairo`

Linux RPA Pillow checks **fail** (never silent-PASS) on uniform blank, low `content_mean` / mostly-black frames, **and** the top-half black paint glitch. Apport dialogs are disabled in the SSH wrapper. Suite self-heal reboots once if the X framebuffer is solid black after a WebKit crash.

Requires **`VOID_RPA_LINUX_PASS`**. Desktop: GDM autologin — see `scripts/ci/configure-rpa-linux-desktop.sh`.

```powershell
$env:VOID_RPA_LINUX_PASS = '<password>'
$env:VOID_RPA_FORCE_DOWNLOAD = '1'   # always fetch latest AppImage
python scripts/ci/rpa-ssh.py --push-harness --download-install
```

### Path B — GitHub Actions (secondary)

Workflow: `.github/workflows/rpa-windows.yml` (`RPA Windows`).

| Event | Behavior |
|-------|----------|
| `release: types: [published]` | Download that tag’s `void-browser.exe`, run smoke + `auto_update` |
| `schedule` / `workflow_dispatch` | Build unsigned NSIS, smoke + `auto_update` |

`continue-on-error: true` on hosted runners — prefer GitLab→rpa-win for the real desktop gate.

### Path C — local / Unraid cron

```powershell
$env:VOID_RPA_WIN_PASS = '<password>'   # never commit
.\scripts\rpa-after-release.ps1 -SyncRepo
```

GitLab runners are Linux — they use WinRM (`rpa-winrm.py`), not native GUI.

## Related

- [GOOGLE_SIGNIN.md](./GOOGLE_SIGNIN.md) — auth / WebView2 limits
- [TEST_VMS.md](./TEST_VMS.md) — Unraid VMs vs local PC vs GHA
- [LOCAL_WINDOWS_DEV.md](./LOCAL_WINDOWS_DEV.md) — build loop
