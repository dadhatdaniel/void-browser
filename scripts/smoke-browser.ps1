# Void Browser — Windows smoke test
# Launches the built exe with --smoke-test and fails on blank-content regressions.
#
# Usage:
#   .\scripts\smoke-browser.ps1
#   .\scripts\smoke-browser.ps1 -ExePath "src-tauri\target\release\void-browser.exe"
#   .\scripts\dev-windows.ps1 -Release -Smoke
#
# Exit codes: 0 pass, 1 fail, 2 exe missing / launch error

param(
  [string]$ExePath = "",
  [int]$TimeoutSec = 90
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Find-VoidExe {
  param([string]$Hint)
  if ($Hint -and (Test-Path -LiteralPath $Hint)) { return (Resolve-Path -LiteralPath $Hint).Path }

  $candidates = @(
    (Join-Path $env:USERPROFILE "void-browser-target\release\void-browser.exe"),
    (Join-Path $env:USERPROFILE "void-browser-target\debug\void-browser.exe"),
    (Join-Path $Root "src-tauri\target\release\void-browser.exe"),
    (Join-Path $Root "src-tauri\target\debug\void-browser.exe"),
    (Join-Path $Root "src-tauri\target\release\bundle\nsis\*\void-browser.exe")
  )
  foreach ($c in $candidates) {
    $resolved = Get-Item -Path $c -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($resolved) { return $resolved.FullName }
  }
  return $null
}

$exe = Find-VoidExe -Hint $ExePath
if (-not $exe) {
  Write-Host "void-browser.exe not found." -ForegroundColor Red
  Write-Host "Build locally first:" -ForegroundColor Yellow
  Write-Host "  .\scripts\dev-windows.ps1 -Release"
  Write-Host "  cargo tauri build"
  Write-Host "Or pass -ExePath to a built binary."
  exit 2
}

$log = Join-Path $env:TEMP "void-browser-smoke.log"
if (Test-Path $log) { Remove-Item $log -Force }

Write-Host "Smoke: launching $exe --smoke-test (timeout ${TimeoutSec}s)"
Write-Host "Smoke log: $log"

$proc = Start-Process -FilePath $exe -ArgumentList @("--smoke-test") -PassThru -WindowStyle Normal
try {
  Wait-Process -Id $proc.Id -Timeout $TimeoutSec -ErrorAction Stop
} catch {
  Write-Host "Smoke: timeout — killing process"
  try { Stop-Process -Id $proc.Id -Force } catch { }
  if (Test-Path $log) { Get-Content $log | Write-Host }
  exit 1
}

# GUI subsystem exes need a refresh before ExitCode is reliable.
Start-Sleep -Milliseconds 200
$proc.Refresh()
$code = $proc.ExitCode
Write-Host "Smoke: exit code $code"
if (Test-Path $log) {
  Write-Host "──── smoke log ────"
  Get-Content $log | Write-Host
  Write-Host "───────────────────"
}

if ($code -ne 0) { exit 1 }
exit 0
