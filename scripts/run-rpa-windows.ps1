# Void Browser — Windows RPA entrypoint
#
# Usage:
#   .\scripts\run-rpa-windows.ps1
#   .\scripts\run-rpa-windows.ps1 -Scenarios smoke_navigate,settings_preserves_tab
#   .\scripts\run-rpa-windows.ps1 -ExePath "C:\path\void-browser.exe"
#   .\scripts\run-rpa-windows.ps1 -Build   # cargo tauri build (release) first
#   .\scripts\run-rpa-windows.ps1 -DownloadInstall  # fetch latest from LAN releases.json / GitHub
#
# Artifacts: artifacts\rpa\<timestamp>\report.json + *.png
# Docs: docs\RPA_TESTING.md

param(
  [string]$ExePath = "",
  [string]$Scenarios = "download_install,app_launch,smoke_navigate,nav_history,visit_void_site,settings_preserves_tab,new_tab,youtube_signin_page,context_menu",
  [switch]$Build,
  [switch]$SmokeOnly,
  [switch]$DownloadInstall,
  [double]$LaunchWait = 4.0
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if ($SmokeOnly) {
  $Scenarios = "app_launch,smoke_navigate"
}

if ($DownloadInstall -and ($Scenarios -notmatch "download_install")) {
  $Scenarios = "download_install,$Scenarios"
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

# Optional PowerShell pre-download (mirrors Python download_install; useful when exe missing).
if ($DownloadInstall) {
  $dlDir = Join-Path $Root "downloads\rpa"
  New-Item -ItemType Directory -Force -Path $dlDir | Out-Null
  $releasesUrl = if ($env:VOID_RELEASES_JSON_URL) { $env:VOID_RELEASES_JSON_URL } else { "http://10.0.0.10:5080/releases.json" }
  Write-Host "Pre-download via $releasesUrl ..." -ForegroundColor Cyan
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $meta = Invoke-RestMethod -Uri $releasesUrl -TimeoutSec 30
    $tag = $meta.tag
    $setupUrl = $meta.assets.windows_exe.url
    $setupName = $meta.assets.windows_exe.name
    $portableUrl = if ($env:VOID_RPA_PORTABLE_URL) {
      $env:VOID_RPA_PORTABLE_URL
    } else {
      "https://github.com/dadhatdaniel/void-browser/releases/download/$tag/void-browser.exe"
    }
    $portableOut = Join-Path $dlDir "void-browser.exe"
    Write-Host "  portable: $portableUrl"
    # Skip re-download when a fresh alpha.16+ binary is already present.
    $needPortable = -not (Test-Path $portableOut) -or ((Get-Item $portableOut).Length -lt 1000000)
    if ($needPortable) {
      Invoke-WebRequest -Uri $portableUrl -OutFile $portableOut -UseBasicParsing -TimeoutSec 300
    } else {
      Write-Host "  portable already present ($((Get-Item $portableOut).Length) bytes)"
    }
    if ((Get-Item $portableOut).Length -lt 1000000) { throw "portable download too small" }
    $distDir = Join-Path $Root "dist"
    New-Item -ItemType Directory -Force -Path $distDir | Out-Null
    # Only copy into dist if nothing is locking the dest (runner promotes after stop).
    try {
      Copy-Item -Force $portableOut (Join-Path $distDir "void-browser.exe")
    } catch {
      Write-Warning "Could not refresh dist yet (file in use): $_"
    }
    if ($setupUrl) {
      $setupOut = Join-Path $dlDir $setupName
      if (-not (Test-Path $setupOut) -or ((Get-Item $setupOut).Length -lt 500000)) {
        Write-Host "  setup: $setupUrl"
        Invoke-WebRequest -Uri $setupUrl -OutFile $setupOut -UseBasicParsing -TimeoutSec 300
      } else {
        Write-Host "  setup already present ($((Get-Item $setupOut).Length) bytes)"
      }
    }
    Write-Host "Pre-download ready ($tag) -> $dlDir" -ForegroundColor Green
  } catch {
    Write-Warning "Pre-download failed (Python download_install may still succeed): $_"
  }
}

if (-not $ExePath) {
  $distExe = Join-Path $Root "dist\void-browser.exe"
  if (Test-Path $distExe) { $ExePath = $distExe }
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
# Native stderr (warnings) must not abort under $ErrorActionPreference=Stop.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  & $py @argsList 2>&1 | ForEach-Object {
    if ($_ -is [System.Management.Automation.ErrorRecord]) {
      Write-Host $_.ToString()
    } else {
      Write-Host $_
    }
  }
  $code = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $prevEap
}
if ($null -eq $code) { $code = 1 }

$latest = Get-ChildItem (Join-Path $Root "artifacts\rpa") -Directory -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($latest) {
  Write-Host ""
  Write-Host "Artifacts: $($latest.FullName)" -ForegroundColor Green
  $report = Join-Path $latest.FullName "report.json"
  if (Test-Path $report) {
    Write-Host "Report:    $report"
    try {
      $r = Get-Content $report -Raw | ConvertFrom-Json
      Write-Host ("Overall ok={0}" -f $r.ok)
      foreach ($sc in $r.scenarios) {
        $mark = if ($sc.ok) { "PASS" } else { "FAIL" }
        Write-Host ("  [{0}] {1} ({2}s)" -f $mark, $sc.name, $sc.duration_sec)
      }
    } catch { }
  }
  Get-ChildItem $latest.FullName -Filter "*.png" | ForEach-Object { Write-Host "  PNG $($_.Name)" }
}

exit $code
