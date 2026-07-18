# Void Browser — Windows RPA entrypoint
#
# Usage:
#   .\scripts\run-rpa-windows.ps1
#   .\scripts\run-rpa-windows.ps1 -Scenarios smoke_navigate,settings_preserves_tab
#   .\scripts\run-rpa-windows.ps1 -ExePath "C:\path\void-browser.exe"
#   .\scripts\run-rpa-windows.ps1 -Build   # cargo tauri build (release) first
#
# Artifacts: artifacts\rpa\<timestamp>\report.json + *.png
# Docs: docs\RPA_TESTING.md

param(
  [string]$ExePath = "",
  [string]$Scenarios = "smoke_navigate,settings_preserves_tab,new_tab,youtube_signin_page,context_menu",
  [switch]$Build,
  [switch]$SmokeOnly,
  [double]$LaunchWait = 4.0
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ($SmokeOnly) {
  $Scenarios = "smoke_navigate"
}

if ($Build) {
  Write-Host "Building release binary..." -ForegroundColor Cyan
  & (Join-Path $Root "scripts\dev-windows.ps1") -Release
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Prefer a local venv (repo may live on a network share where venvs are slow/fragile).
$venvCandidates = @(
  (Join-Path $env:USERPROFILE "void-rpa-venv"),
  (Join-Path $Root "tests\rpa\.venv")
)
$py = $null
foreach ($venv in $venvCandidates) {
  $candidate = Join-Path $venv "Scripts\python.exe"
  if (Test-Path $candidate) { $py = $candidate; break }
}
if (-not $py) {
  Write-Host "Creating Python venv + installing deps..." -ForegroundColor Cyan
  $venv = $venvCandidates[0]
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
  if (-not $python) { throw "Python 3 not found. Install Python 3.10+ and re-run." }
  if ($python.Name -eq "py.exe") {
    & py -3 -m venv $venv
  } else {
    & python -m venv $venv
  }
  $py = Join-Path $venv "Scripts\python.exe"
  if (-not (Test-Path $py)) { throw "Failed to create venv at $venv" }
  & $py -m pip install --upgrade pip
  & $py -m pip install -r (Join-Path $Root "tests\rpa\requirements.txt")
}

$argsList = @(
  (Join-Path $Root "tests\rpa\runner.py"),
  "--scenarios", $Scenarios,
  "--launch-wait", "$LaunchWait"
)
if ($ExePath) {
  $argsList += @("--exe", $ExePath)
}

Write-Host "Running RPA: $Scenarios" -ForegroundColor Cyan
& $py @argsList
$code = $LASTEXITCODE

$latest = Get-ChildItem (Join-Path $Root "artifacts\rpa") -Directory -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($latest) {
  Write-Host ""
  Write-Host "Artifacts: $($latest.FullName)" -ForegroundColor Green
  $report = Join-Path $latest.FullName "report.json"
  if (Test-Path $report) {
    Write-Host "Report:    $report"
  }
  Get-ChildItem $latest.FullName -Filter "*.png" | ForEach-Object { Write-Host "  PNG $($_.Name)" }
}

exit $code
