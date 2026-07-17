# VOID

**No tracking. No AI. Just browsing.**

🌐 **[void.lightfoot.cloud](https://void.lightfoot.cloud)** — Official Website

A minimal, secure, privacy-first web browser built with [Tauri](https://tauri.app) and Rust.

[![Build Linux](https://img.shields.io/badge/Linux-deb%20%7C%20AppImage-FCC624?logo=linux&logoColor=black)](https://void.lightfoot.cloud)
[![Build macOS & Windows](https://github.com/dadhatdaniel/void-browser/actions/workflows/build-macos.yml/badge.svg)](https://github.com/dadhatdaniel/void-browser/actions/workflows/build-macos.yml)
[![Windows](https://img.shields.io/badge/Windows-msi%20%7C%20exe-0078D4?logo=windows&logoColor=white)](https://github.com/dadhatdaniel/void-browser/releases)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL%203.0-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

---

## Why Void?

Every major browser has become a platform for surveillance, advertising, or feature creep. Chrome sells your attention. Firefox adds AI. Safari locks you in. Brave has crypto.

Void has none of that. It's a browser. It browses. That's it.

**Built for people who believe the internet should work for them, not against them.**

## Downloads

Get the latest release for your platform:

| Platform | Format | Where |
|----------|--------|-------|
| 🐧 **Linux** | `.deb`, `.AppImage` | [GitLab Releases](https://void.lightfoot.cloud) |
| 🪟 **Windows** | `.msi`, `.exe` | [GitHub Releases](https://github.com/dadhatdaniel/void-browser/releases) |
| 🍎 **macOS** | `.dmg` | [GitHub Releases](https://github.com/dadhatdaniel/void-browser/releases) |

All releases include SHA-256 checksums. Verify your download:
```bash
sha256sum --check CHECKSUMS.sha256   # Linux
shasum -a 256 --check CHECKSUMS.sha256  # macOS
```

## Features

- 🛡️ **Built-in ad blocker** — Powered by adblock-rust (same engine as Brave), 137K+ filter rules
- 🔒 **HTTPS-only mode** — Insecure connections blocked by default
- 👻 **Anti-fingerprinting** — Canvas, WebGL, AudioContext resistance
- 🚫 **Zero telemetry** — Nothing leaves your machine. Ever.
- ⚡ **Tiny footprint** — Under 10MB, uses native OS webview
- 🎨 **Customizable** — Dark/Light/Midnight themes, custom CSS, configurable keybinds
- 🔐 **DNS over HTTPS** — Quad9, Cloudflare, or Mullvad built in
- 🌐 **WebRTC leak protection** — Prevents IP leaks even with VPNs

## What we don't have

| ✕ | Feature we'll never add |
|---|---|
| ✕ | AI assistant |
| ✕ | Cryptocurrency wallet |
| ✕ | Cloud sync / accounts |
| ✕ | Telemetry / analytics |
| ✕ | News feed / sponsored shortcuts |
| ✕ | VPN upsell |
| ✕ | Social features |

## Security Levels

| Level | Description |
|-------|-------------|
| **Standard** | Ad/tracker blocking, referrer stripping, HTTPS upgrades |
| **Strict** (default) | + Third-party cookie blocking, WebRTC leak prevention, fingerprint resistance |
| **Paranoid** | + Full fingerprint resistance, JS disabled by default, timezone spoofing |

## Building from Source

### Prerequisites

- [Rust](https://rustup.rs) 1.85+
- [Tauri CLI](https://tauri.app/start/create-project/) v2
- Platform dependencies:
  - **Linux**: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev`
  - **macOS**: Xcode Command Line Tools
  - **Windows**: WebView2 (pre-installed on Windows 10/11)

### Build

```bash
cargo install tauri-cli --version "^2"
cargo tauri dev    # Development
cargo tauri build  # Production
```

### Build Output

| Platform | Artifacts |
|----------|-----------|
| Linux | `src-tauri/target/release/bundle/deb/`, `appimage/` |
| macOS | `src-tauri/target/release/bundle/dmg/` |
| Windows | `src-tauri/target/release/bundle/msi/`, `nsis/` |

## Configuration

Void stores config in a local TOML file — no cloud, no account:

| OS | Path |
|----|------|
| Linux | `~/.config/void-browser/config.toml` |
| macOS | `~/Library/Application Support/void-browser/config.toml` |
| Windows | `%APPDATA%/void-browser/config.toml` |

```toml
homepage = "void://newtab"
search_engine = "DuckDuckGo"
theme = "Dark"
adblock_enabled = true
security_level = "Strict"

[fingerprint_resistance]
spoof_canvas = true
spoof_webgl = true
spoof_audio = true
```

## Project Structure

```
void-browser/
├── src-tauri/           # Rust backend (Tauri)
│   └── src/
│       ├── main.rs      # Entry point & commands
│       ├── adblock.rs   # Ad & tracker blocking (adblock-rust)
│       ├── privacy.rs   # Privacy hardening & fingerprint resistance
│       ├── config.rs    # User configuration (TOML)
│       └── tabs.rs      # Tab management
├── src/                 # Frontend (HTML/CSS/JS)
├── filters/             # Ad & tracker filter lists
├── website/             # Landing page (void.lightfoot.cloud)
├── .gitlab-ci.yml       # GitLab CI — Linux builds + security + deploy
└── .github/workflows/   # GitHub Actions — macOS + Windows builds
```

## CI/CD

| Platform | What it builds | Trigger |
|----------|---------------|---------|
| **GitLab** (internal) | Linux `.deb` + `.AppImage`, security scans, website deploy | Every push + tags |
| **GitHub Actions** | macOS `.dmg` + Windows `.msi`/`.exe` | Tags only |

## Security

- **Signed releases** — SHA-256 checksums on all packages
- **Dependency auditing** — `cargo audit` on every build
- **License compliance** — Automated scanning
- **Zero tracking website** — [void.lightfoot.cloud](https://void.lightfoot.cloud) uses no cookies, no analytics, no tracking
- **AI scraping prevention** — robots.txt blocks 25+ AI crawlers, TDM reservation protocol

## License

[GPL-3.0](LICENSE) — Free as in freedom.

---

*Built by [Daniel Lightfoot](https://lightfoot.cloud). No VC. No telemetry. No bullshit.*

*Website: [void.lightfoot.cloud](https://void.lightfoot.cloud) · Source: [GitHub](https://github.com/dadhatdaniel/void-browser)*
