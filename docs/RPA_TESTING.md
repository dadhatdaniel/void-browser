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

# Full scenario set
.\scripts\run-rpa-windows.ps1

# Subset
.\scripts\run-rpa-windows.ps1 -Scenarios smoke_navigate,settings_preserves_tab
```

Requires:

- Built `void-browser.exe` (or `-Build`)
- Interactive desktop session (not headless SSH)
- Python 3.10+ (`py -3` or `python`)
- WebView2 Runtime

## Scenarios

| Name | What it checks |
|------|----------------|
| `smoke_navigate` | example.com + duckduckgo; screenshots content |
| `settings_preserves_tab` | open site → Settings (Ctrl+,) → Esc → process/UI still alive |
| `new_tab` | Ctrl+T second tab; switch back |
| `youtube_signin_page` | Load `accounts.google.com/signin` UI (no password unless env set) |
| `context_menu` | URL bar clipboard Ctrl+C / Ctrl+V |

Optional Google login (local/CI secrets only — never commit):

```powershell
$env:VOID_TEST_GOOGLE_USER = "you@example.com"
$env:VOID_TEST_GOOGLE_PASS = "..."
```

## Artifacts

```
artifacts/rpa/<YYYYMMDD_HHMMSS>/
  report.json          # machine-readable steps + pass/fail
  01_smoke_example.png
  02_...
```

`report.json` fields: `ok`, `scenarios[].steps[].screenshot`, `agent_review_hints`.

## Agent quality loop

After an RPA run (or CI artifact download):

1. Find latest dir: `artifacts/rpa/*/` (or CI download).
2. Read `report.json`.
3. **Read each `*.png` with the Read tool** (vision) — do not rely on filenames alone.
4. File bugs for failed steps or visually broken UI (blank content, settings killed the tab, Google block page, etc.).
5. Fix → rebuild → re-run `.\scripts\run-rpa-windows.ps1 -Scenarios <failed>`.

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
