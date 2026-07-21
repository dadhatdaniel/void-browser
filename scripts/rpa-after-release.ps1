# Void Browser — Path B: run full RPA on void-rpa-windows after a release.
#
# Intended for Unraid/cron or manual ops after GitHub Release + latest.json land.
# Does NOT store passwords — set VOID_RPA_WIN_PASS in the environment.
#
# Usage (from DANIELRIG or any WinRM client):
#   $env:VOID_RPA_WIN_PASS = '...'   # never commit
#   .\scripts\rpa-after-release.ps1
#   .\scripts\rpa-after-release.ps1 -SyncRepo
#
# What it does:
#   1. WinRM to rpa-win VM (default 10.0.0.28)
#   2. DownloadInstall latest portable from LAN releases.json / GitHub
#   3. Run default suite including auto_update
#   4. Pull artifacts\rpa\<stamp>\ locally
#
# Primary CI gate is GitHub Actions "RPA Windows" on release:published.
# This script is the GPU/desktop-friendly self-hosted complement.
#
# Docs: docs\RPA_TESTING.md

param(
  [switch]$SyncRepo,
  [string]$Scenarios = "",
  [int]$TimeoutSec = 1800
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$remote = Join-Path $here "rpa-remote-run.ps1"
if (-not (Test-Path $remote)) {
  throw "Missing $remote"
}

if (-not $env:VOID_RPA_WIN_PASS -and -not $env:VOID_RPA_WIN_PASSWORD) {
  Write-Warning "VOID_RPA_WIN_PASS is not set. rpa-remote-run.ps1 will fail without credentials."
}

# Full suite (includes auto_update via runner.py / remote defaults once synced).
if (-not $Scenarios) {
  $Scenarios = @(
    "download_install",
    "app_launch",
    "smoke_navigate",
    "nav_history",
    "visit_void_site",
    "void_xp_desktop",
    "settings_preserves_tab",
    "new_tab",
    "youtube_signin_page",
    "context_menu",
    "auto_update"
  ) -join ","
}

Write-Host "=== RPA after release (remote void-rpa-windows) ===" -ForegroundColor Cyan
Write-Host "Scenarios: $Scenarios"
$hostName = if ($env:VOID_RPA_WIN_HOST) { $env:VOID_RPA_WIN_HOST } else { "10.0.0.28" }
Write-Host "Host: $hostName"

$remoteArgs = @{
  DownloadInstall = $true
  Scenarios       = $Scenarios
  TimeoutSec      = $TimeoutSec
}
if ($SyncRepo) { $remoteArgs.SyncRepo = $true }

& $remote @remoteArgs
exit $LASTEXITCODE
