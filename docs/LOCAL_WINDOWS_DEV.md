# Local Windows development (Void Browser)

Build and test the **Windows WebView2 GUI on this PC**. Do not use Unraid Docker for Windows QA.

## Why Unraid Docker cannot replace Windows testing

| Environment | What it is | What you can test |
|-------------|------------|-------------------|
| **This Windows PC** | Native MSVC + WebView2 | Real `.exe`, chrome UI, content webviews, smoke test |
| **Unraid Docker** (`10.0.0.10`) | Linux containers | Rust unit tests / Linux builds only |
| **GitHub `windows-latest`** | Remote Windows CI | Release artifacts + automated smoke |

Unraid runs **Linux**. It cannot run the Windows `.exe`, initialize WebView2, or show the Tauri GUI. A green Linux `cargo test` does **not** mean the Windows content area paints.

## Prerequisites

1. **Rust** (rustup) — `https://rustup.rs`  
   Default host: `x86_64-pc-windows-msvc`
2. **Visual Studio Build Tools 2022** — workload **Desktop development with C++**  
   Needs `link.exe` (MSVC). Without it, `cargo` fails at link time.
3. **WebView2 Runtime** — usually already installed on Windows 10/11
4. **Tauri CLI 2** — `cargo install tauri-cli --version "^2"`

### Silent winget install (Build Tools)

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools -e `
  --accept-package-agreements --accept-source-agreements `
  --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --add Microsoft.VisualStudio.Component.Windows11SDK.22621"
```

After install, **open a new PowerShell** (or reboot) so the MSVC environment is on PATH. Or rely on `vcvars` via rustup’s MSVC detection.

### WebView2 (if missing)

```powershell
winget install --id Microsoft.EdgeWebView2Runtime -e --accept-package-agreements
```

### Rust + Tauri CLI

```powershell
# If rustup is not installed, run the rustup-init.exe installer from https://rustup.rs
rustup default stable
cargo install tauri-cli --version "^2"
```

## Fast local loop (no GitHub)

From the repo root (`void-browser`):

```powershell
# One-shot: check tools, then dev or release build
.\scripts\dev-windows.ps1              # cargo tauri dev
.\scripts\dev-windows.ps1 -Release     # release exe (no MSI)
.\scripts\dev-windows.ps1 -Release -Smoke

# Or manually (recommended target dir on local NTFS):
$env:CARGO_TARGET_DIR = "$env:USERPROFILE\void-browser-target"
cd src-tauri
cargo build --release
# exe: %USERPROFILE%\void-browser-target\release\void-browser.exe
```

Notes:

- If the repo lives on a network share (`Z:` → Unraid), `src-tauri/gen` must be writable by your Windows user. Root-owned files from Docker/Linux cause `Access is denied` in `tauri-build`. Fix on the host: `rm -rf src-tauri/gen && mkdir -p src-tauri/gen && chown -R daniel:users src-tauri/gen`.
- `cargo tauri build` MSI/NSIS may reject SemVer pre-releases like `alpha.13` (WiX wants numeric prerelease). For local QA use `cargo build --release` / `dev-windows.ps1 -Release`.

Release binary (with the script’s default `CARGO_TARGET_DIR`):

```text
%USERPROFILE%\void-browser-target\release\void-browser.exe
src-tauri\target\release\void-browser.exe   (mirrored copy)
```

Installer bundles (when build finishes):

```text
src-tauri\target\release\bundle\msi\
src-tauri\target\release\bundle\nsis\
```

### Smoke test (catches blank content webview)

Run from an **interactive desktop** PowerShell (Win+X / Windows Terminal you opened yourself). Agent/SSH/service sessions often create the content webview handle but WebView2 IPC (`size`/`bounds`) fails with `failed to receive message from webview`.

```powershell
.\scripts\smoke-browser.ps1
# or explicit path:
.\scripts\smoke-browser.ps1 -ExePath "$env:USERPROFILE\void-browser-target\release\void-browser.exe"
```

This launches `void-browser.exe --smoke-test`, navigates to `example.com`, asserts content webview bounds + URL, and writes `%TEMP%\void-browser-smoke.log`.

### Unit tests only (no GUI)

```powershell
cd src-tauri
cargo test
```

## Optional: Linux unit tests on Unraid Docker

Use Docker on Unraid **only** for `cargo test` / Linux builds — never as Windows GUI QA.

Example (run on the Unraid host via SSH):

```bash
docker run --rm -v /path/to/void-browser:/app -w /app/src-tauri \
  rust:1.85-bookworm \
  bash -lc 'apt-get update -qq && apt-get install -y -qq pkg-config libwebkit2gtk-4.1-dev libgtk-3-0 && cargo test'
```

See also `scripts/docker-linux-unit-test.sh`.

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `link.exe not found` | VS Build Tools / C++ workload missing; new shell after install |
| `cargo: command not found` | `%USERPROFILE%\.cargo\bin` not on PATH |
| White/black content, URL updates | Content webview bounds/z-order — run smoke; see `browser.rs` |
| Smoke timeout | WebView2 missing or display session blocked |

## Version / release policy

Prefer a **local smoke pass** before tagging `v0.1.0-alpha.*` and pushing GitHub releases. Source of truth remains GitLab (`lightfootcloud/void-browser`).
