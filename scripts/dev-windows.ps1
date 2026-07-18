# Void Browser — local Windows dev / build helper
#
# Usage:
#   .\scripts\dev-windows.ps1              # cargo tauri dev
#   .\scripts\dev-windows.ps1 -Release     # cargo tauri build
#   .\scripts\dev-windows.ps1 -CheckOnly   # prereq check only
#   .\scripts\dev-windows.ps1 -Release -Smoke  # build then smoke-browser.ps1
#
# Docs: docs/LOCAL_WINDOWS_DEV.md

param(
  [switch]$Release,
  [switch]$CheckOnly,
  [switch]$Smoke,
  [switch]$SkipPrereq
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

# Ensure rustup cargo is visible in this session
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) {
  $env:PATH = "$cargoBin;$env:PATH"
}

function Write-Ok($msg) { Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Bad($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "[..] $msg" -ForegroundColor Cyan }

function Test-Command($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Find-LinkExe {
  if (Test-Command "link.exe") {
    return (Get-Command "link.exe").Source
  }
  $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
  if (-not (Test-Path $vswhere)) { return $null }
  $install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if (-not $install) { return $null }
  $candidates = Get-ChildItem -Path (Join-Path $install "VC\Tools\MSVC") -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending
  foreach ($c in $candidates) {
    $link = Join-Path $c.FullName "bin\Hostx64\x64\link.exe"
    if (Test-Path $link) { return $link }
  }
  return $null
}

function Test-WebView2 {
  $key = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
  if (Test-Path $key) {
    $pv = (Get-ItemProperty $key -ErrorAction SilentlyContinue).pv
    if ($pv) { return $pv }
  }
  return $null
}

function Assert-Prereqs {
  $failed = $false
  Write-Info "Checking local Windows toolchain..."

  if (Test-Command "rustc") {
    Write-Ok "rustc $(& rustc --version)"
  } else {
    Write-Bad "rustc not found. Install from https://rustup.rs and ensure %USERPROFILE%\.cargo\bin is on PATH."
    $failed = $true
  }

  if (Test-Command "cargo") {
    Write-Ok "cargo $(& cargo --version)"
  } else {
    Write-Bad "cargo not found."
    $failed = $true
  }

  $link = Find-LinkExe
  if ($link) {
    Write-Ok "MSVC link.exe -> $link"
  } else {
    Write-Bad "MSVC link.exe not found. Install VS Build Tools with C++ workload (see docs/LOCAL_WINDOWS_DEV.md)."
    $failed = $true
  }

  $wv = Test-WebView2
  if ($wv) {
    Write-Ok "WebView2 Runtime $wv"
  } else {
    Write-Bad "WebView2 Runtime not detected. Install: winget install Microsoft.EdgeWebView2Runtime"
    $failed = $true
  }

  $hasTauri = $false
  if (Test-Command "cargo-tauri") { $hasTauri = $true }
  elseif (Test-Command "cargo") {
    $list = & cargo --list 2>$null
    if ($list -match "^\s*tauri\b") { $hasTauri = $true }
  }
  if ($hasTauri) {
    Write-Ok "tauri-cli available (cargo tauri)"
  } else {
    Write-Bad 'tauri-cli missing. Install: cargo install tauri-cli --version "^2"'
    $failed = $true
  }

  if ($failed) {
    Write-Bad "Prerequisites missing - see docs/LOCAL_WINDOWS_DEV.md"
    exit 2
  }
  Write-Ok "Prerequisites OK"
}

if (-not $SkipPrereq) {
  Assert-Prereqs
}

if ($CheckOnly) {
  exit 0
}

# Network shares (e.g. Z: -> Unraid) can leave root-owned src-tauri/gen unwritable.
# Prefer a local NTFS target dir for faster links and fewer ACL surprises.
if (-not $env:CARGO_TARGET_DIR) {
  $env:CARGO_TARGET_DIR = Join-Path $env:USERPROFILE "void-browser-target"
  Write-Info "CARGO_TARGET_DIR=$($env:CARGO_TARGET_DIR)"
}

if ($Release) {
  Write-Info "Building release binary (cargo build --release)..."
  # Avoid MSI bundling: WiX rejects SemVer pre-releases like alpha.13.
  Push-Location (Join-Path $Root "src-tauri")
  try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  } finally {
    Pop-Location
  }

  $exeCandidates = @(
    (Join-Path $env:CARGO_TARGET_DIR "release\void-browser.exe"),
    (Join-Path $Root "src-tauri\target\release\void-browser.exe")
  )
  $exe = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $exe) {
    Write-Bad "Build finished but void-browser.exe not found"
    exit 1
  }
  Write-Ok "Built: $exe"

  # Keep a copy where smoke-browser.ps1 looks by default
  $mirror = Join-Path $Root "src-tauri\target\release"
  New-Item -ItemType Directory -Force -Path $mirror | Out-Null
  Copy-Item $exe (Join-Path $mirror "void-browser.exe") -Force

  if ($Smoke) {
    Write-Info "Running smoke test..."
    & (Join-Path $PSScriptRoot "smoke-browser.ps1") -ExePath $exe
    exit $LASTEXITCODE
  }
} else {
  Write-Info "Starting dev (cargo tauri dev)..."
  cargo tauri dev
  exit $LASTEXITCODE
}
