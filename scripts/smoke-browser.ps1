# Void Browser — Windows smoke test
# Launches the built exe with --smoke-test and fails on black-screen regressions.
#
# Usage:
#   .\scripts\smoke-browser.ps1
#   .\scripts\smoke-browser.ps1 -ExePath "src-tauri\target\release\void-browser.exe"
#
# Exit codes: 0 pass, 1 fail, 2 exe missing / launch error

param(
  [string]$ExePath = "",
  [int]$TimeoutSec = 90
)

$ErrorActionPreference = "Stop"

function Find-VoidExe {
  param([string]$Hint)
  if ($Hint -and (Test-Path -LiteralPath $Hint)) { return (Resolve-Path $Hint).Path }

  $candidates = @(
    "src-tauri\target\release\void-browser.exe",
    "src-tauri\target\debug\void-browser.exe"
  )
  foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { return (Resolve-Path $c).Path }
  }
  return $null
}

$exe = Find-VoidExe -Hint $ExePath
if (-not $exe) {
  Write-Error "void-browser.exe not found. Build first (cargo tauri build) or pass -ExePath."
  exit 2
}

$log = Join-Path $env:TEMP "void-browser-smoke.log"
if (Test-Path $log) { Remove-Item $log -Force }

Write-Host "Smoke: launching $exe --smoke-test (timeout ${TimeoutSec}s)"
Write-Host "Smoke log: $log"

$proc = Start-Process -FilePath $exe -ArgumentList @("--smoke-test") -PassThru -WindowStyle Normal
$sw = [Diagnostics.Stopwatch]::StartNew()
while (-not $proc.HasExited -and $sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
  Start-Sleep -Milliseconds 400
}

if (-not $proc.HasExited) {
  Write-Host "Smoke: timeout — killing process"
  try { Stop-Process -Id $proc.Id -Force } catch { }
  if (Test-Path $log) { Get-Content $log | Write-Host }
  exit 1
}

$code = $proc.ExitCode
Write-Host "Smoke: exit code $code"
if (Test-Path $log) {
  Write-Host "──── smoke log ────"
  Get-Content $log | Write-Host
  Write-Host "───────────────────"
}

if ($code -ne 0) { exit 1 }
exit 0
