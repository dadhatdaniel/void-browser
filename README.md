# VOID

**No tracking. No AI. Just browsing.**

A minimal, secure, privacy-first web browser built with [Tauri](https://tauri.app) and Rust.

## Why Void?

Every major browser has become a platform for surveillance, advertising, or feature creep. Chrome sells your attention. Firefox adds AI. Safari locks you in. Brave has crypto.

Void has none of that. It's a browser. It browses. That's it.

## Features

- 🛡️ **Built-in ad blocker** — Powered by adblock-rust (same engine as Brave)
- 🔒 **HTTPS-only mode** — Insecure connections blocked by default
- 👻 **Anti-fingerprinting** — Canvas, WebGL, AudioContext resistance
- 🚫 **Zero telemetry** — Nothing leaves your machine. Ever.
- ⚡ **Tiny footprint** — Under 10MB, uses native OS webview
- 🎨 **Customizable** — Dark/Light/Midnight themes, custom CSS, configurable keybinds
- 🔐 **DNS over HTTPS** — Quad9, Cloudflare, or Mullvad built in
- 📋 **Local config** — Plain TOML file, no cloud, no account

## What we don't have

- ✕ AI assistant
- ✕ Cryptocurrency wallet
- ✕ Cloud sync / accounts
- ✕ Telemetry / analytics
- ✕ News feed / sponsored shortcuts
- ✕ VPN upsell

## Security Levels

| Level | Description |
|-------|-------------|
| **Standard** | Ad/tracker blocking, referrer stripping, HTTPS upgrades |
| **Strict** (default) | + Third-party cookie blocking, WebRTC leak prevention, fingerprint resistance |
| **Paranoid** | + Full fingerprint resistance, JS disabled by default, timezone spoofing |

## Building

### Prerequisites

- [Rust](https://rustup.rs) 1.70+
- [Tauri CLI](https://tauri.app/start/create-project/) v2
- Platform dependencies:
  - **Linux**: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev`
  - **macOS**: Xcode Command Line Tools
  - **Windows**: WebView2 (pre-installed on Windows 10/11)

### Development

```bash
# Install Tauri CLI
cargo install tauri-cli --version "^2"

# Run in development mode
cargo tauri dev

# Build for production
cargo tauri build
```

### Build output

- **Linux**: `.deb` and `.AppImage` in `src-tauri/target/release/bundle/`
- **macOS**: `.dmg` in `src-tauri/target/release/bundle/`
- **Windows**: `.msi` and `.exe` in `src-tauri/target/release/bundle/`

## Configuration

Void stores its config in a local TOML file:

- **Linux**: `~/.config/void-browser/config.toml`
- **macOS**: `~/Library/Application Support/void-browser/config.toml`
- **Windows**: `%APPDATA%/void-browser/config.toml`

```toml
# Example config
homepage = "void://newtab"
search_engine = "DuckDuckGo"
theme = "Dark"
adblock_enabled = true
tracker_blocking = true
https_policy = "Strict"
security_level = "Strict"

[fingerprint_resistance]
spoof_canvas = true
spoof_webgl = true
spoof_audio = true
```

## Project Structure

```
void-browser/
├── src-tauri/          # Rust backend (Tauri)
│   ├── src/
│   │   ├── main.rs     # Entry point & Tauri commands
│   │   ├── adblock.rs  # Ad & tracker blocking engine
│   │   ├── privacy.rs  # Privacy hardening & fingerprint resistance
│   │   ├── config.rs   # User configuration (TOML)
│   │   └── tabs.rs     # Tab management
│   └── Cargo.toml
├── src/                # Frontend (HTML/CSS/JS)
│   ├── index.html      # Browser chrome UI
│   ├── style.css       # Themes & styling
│   └── app.js          # Tab/navigation controller
├── filters/            # Ad & tracker filter lists
├── website/            # Landing page
└── .gitlab-ci.yml      # CI/CD pipeline
```

## License

GPL-3.0 — Free as in freedom.

---

*Built by [Daniel Lightfoot](https://lightfoot.cloud). No VC. No telemetry. No bullshit.*

## Security

Void takes security seriously:

- **Signed releases** — All packages include SHA-256 checksums and GPG signatures
- **Dependency auditing** — `cargo audit` runs on every build to catch known CVEs
- **License compliance** — Automated license scanning ensures no incompatible dependencies
- **Binary verification** — Post-build checks verify RELRO, stripped symbols, and linked libraries
- **No tracking on the website** — The Void website uses zero cookies, zero analytics, and zero tracking scripts. We verify this in CI.
- **Hardened container** — The website runs read-only with dropped capabilities and memory limits

To verify a download:
```bash
# Check file integrity
sha256sum --check CHECKSUMS.sha256

# Verify GPG signature
gpg --verify CHECKSUMS.sha256.asc
```
