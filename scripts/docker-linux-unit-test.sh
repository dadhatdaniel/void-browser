#!/usr/bin/env bash
# Linux-only: run Rust unit tests in Docker (NOT Windows GUI / WebView2 QA).
# Intended for Unraid / CI Linux hosts.
#
# Usage (from repo root on a Linux host):
#   bash scripts/docker-linux-unit-test.sh
#
# This does NOT validate the Windows .exe content webview.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "[void] Linux unit tests via Docker (not Windows GUI QA)"
docker run --rm \
  -v "$ROOT:/app:ro" \
  -w /app/src-tauri \
  rust:1.85-bookworm \
  bash -lc '
    set -euo pipefail
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      pkg-config libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev \
      librsvg2-dev patchelf
    # writable target on anonymous volume
    mkdir -p /tmp/void-target
    export CARGO_TARGET_DIR=/tmp/void-target
    cargo test --lib
  '

echo "[void] Linux unit tests finished. Reminder: Windows GUI still needs a Windows host."
