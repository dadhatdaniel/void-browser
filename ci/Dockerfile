# ═══════════════════════════════════════════════════
# Void Browser — CI Build Image
# Pre-installs all system dependencies so pipeline
# jobs skip the 100+ second apt-get install step.
# ═══════════════════════════════════════════════════

FROM rust:latest

RUN apt-get update -qq && apt-get install -y -qq \
    libwebkit2gtk-4.1-dev \
    libgtk-3-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    patchelf \
    file \
    curl \
    nsis \
    nsis-pluginapi \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Pre-install Tauri CLI and audit tools
RUN cargo install tauri-cli --version "^2" --locked \
    && cargo install cargo-audit --locked \
    && cargo install cargo-license --locked

LABEL maintainer="Daniel Lightfoot"
LABEL description="Void Browser CI build image with all system deps pre-installed"
