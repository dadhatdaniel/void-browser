# Void Browser - trigger RPA on the Windows VM and pull artifacts locally.
#
# Run from DANIELRIG (or any WinRM client with TrustedHosts set):
#   $env:VOID_RPA_WIN_PASS = '...'   # never commit
#   .\scripts\rpa-remote-run.ps1
#   .\scripts\rpa-remote-run.ps1 -SmokeOnly
#   .\scripts\rpa-remote-run.ps1 -DownloadInstall
#   .\scripts\rpa-remote-run.ps1 -Scenarios smoke_navigate,settings_preserves_tab
#
# Credentials: VOID_RPA_WIN_USER / VOID_RPA_WIN_PASS, or -Credential.
# Default host: 10.0.0.28 (void-rpa-windows / RPA-WIN-VM).
#
# Artifacts land in: <repo>\artifacts\rpa\<timestamp>\ (pulled via WinRM).
# Docs: docs\RPA_TESTING.md

param(
  [string]$ComputerName = $(if ($env:VOID_RPA_WIN_HOST) { $env:VOID_RPA_WIN_HOST } else { "10.0.0.28" }),
  [string]$UserName = $(if ($env:VOID_RPA_WIN_USER) { $env:VOID_RPA_WIN_USER } else { "rpa-win" }),
  [string]$Password = $env:VOID_RPA_WIN_PASS,
  [PSCredential]$Credential,
  [string]$RemoteRoot = "C:\void-browser",
  [string]$ExePath = "C:\void-browser\dist\void-browser.exe",
  [string]$Scenarios = "download_install,app_launch,smoke_navigate,nav_history,visit_void_site,void_xp_desktop,settings_preserves_tab,new_tab,youtube_signin_page,context_menu,auto_update",
  [switch]$SmokeOnly,
  [switch]$DownloadInstall,
  [switch]$SkipPull,
  [switch]$SyncRepo,
  [double]$LaunchWait = 4.0,
  [int]$TimeoutSec = 1200,
  [string]$LocalArtifactsRoot = ""
)

$ErrorActionPreference = "Stop"
$LocalRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $LocalArtifactsRoot) {
  $LocalArtifactsRoot = Join-Path $LocalRoot "artifacts\rpa"
}
if ($SmokeOnly) { $Scenarios = "app_launch,smoke_navigate" }
if ($DownloadInstall -and ($Scenarios -notmatch "download_install")) {
  $Scenarios = "download_install,$Scenarios"
}

function Get-RpaCredential {
  if ($Credential) { return $Credential }
  if (-not $Password) {
    throw "Set VOID_RPA_WIN_PASS or pass -Credential / -Password (do not commit passwords)."
  }
  $sec = ConvertTo-SecureString $Password -AsPlainText -Force
  return New-Object System.Management.Automation.PSCredential($UserName, $sec)
}

function Ensure-TrustedHost([string]$HostName) {
  try {
    $current = (Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction Stop).Value
  } catch {
    $current = ""
  }
  if ($current -and ($current.Split(",") | ForEach-Object { $_.Trim() }) -contains $HostName) {
    return
  }
  Write-Host "TrustedHosts does not include $HostName - add it (elevated) then re-run:" -ForegroundColor Yellow
  Write-Host "  Set-Item WSMan:\localhost\Client\TrustedHosts -Value '$HostName' -Force" -ForegroundColor Yellow
  Write-Host "  # or append: juliarig,juliarig.local,10.0.2.145,$HostName" -ForegroundColor Yellow
}

Ensure-TrustedHost $ComputerName
$cred = Get-RpaCredential
$opt = New-PSSessionOption -OpenTimeout 20000 -OperationTimeout 180000
Write-Host "Connecting WinRM -> $ComputerName as $($cred.UserName) ..." -ForegroundColor Cyan
$session = New-PSSession -ComputerName $ComputerName -Credential $cred -Authentication Negotiate -SessionOption $opt

try {
  if ($SyncRepo) {
    Write-Host "Syncing repo on VM (git fetch/reset main)..." -ForegroundColor Cyan
    Invoke-Command -Session $session -ScriptBlock {
      param($RemoteRoot)
      $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [System.Environment]::GetEnvironmentVariable("Path", "User")
      if (-not (Test-Path $RemoteRoot)) { throw "RemoteRoot missing: $RemoteRoot" }
      Set-Location $RemoteRoot
      $env:GIT_TERMINAL_PROMPT = "0"
      git fetch --depth 1 origin main 2>&1 | Out-Host
      git reset --hard origin/main 2>&1 | Out-Host
      git log -1 --oneline
    } -ArgumentList $RemoteRoot
  }

  # Push local harness changes when SyncRepo is off but files differ (dev loop).
  # Always ensure critical scripts exist on the VM for this run.
  Write-Host "Syncing RPA harness files to VM..." -ForegroundColor Cyan
  $toPush = @(
    "tests\rpa\runner.py",
    "tests\rpa\requirements.txt",
    "scripts\run-rpa-windows.ps1",
    "scripts\rpa-after-release.ps1",
    "docs\RPA_TESTING.md"
  )
  foreach ($rel in $toPush) {
    $local = Join-Path $LocalRoot $rel
    if (-not (Test-Path $local)) { continue }
    $remote = Join-Path $RemoteRoot $rel
    $remoteDir = Split-Path $remote -Parent
    Invoke-Command -Session $session -ScriptBlock {
      param($d) New-Item -ItemType Directory -Force -Path $d | Out-Null
    } -ArgumentList $remoteDir
    Copy-Item -ToSession $session -Path $local -Destination $remote -Force
    Write-Host "  pushed $rel"
  }

  $runId = Get-Date -Format "yyyyMMdd_HHmmss"
  $remoteStatus = "C:\Users\rpa-win\void-rpa-status\$runId"
  # Pass as "1"/"0" — ArgumentList drops bare $false on some PowerShell hosts.
  $dlFlagArg = if ($DownloadInstall) { "1" } else { "0" }
  Write-Host "Scheduling interactive RPA runId=$runId (DownloadInstall=$dlFlagArg) ..." -ForegroundColor Cyan

  Invoke-Command -Session $session -ScriptBlock {
    param($RemoteRoot, $ExePath, $Scenarios, $LaunchWait, $remoteStatus, $runId, $DownloadInstallFlag)

    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
      [System.Environment]::GetEnvironmentVariable("Path", "User")

    if (-not (Test-Path (Join-Path $RemoteRoot "scripts\run-rpa-windows.ps1"))) {
      throw "Repo missing scripts\run-rpa-windows.ps1 under $RemoteRoot"
    }

    # Exe may be missing when download_install will fetch it.
    $exeArg = ""
    if (Test-Path $ExePath) {
      $exeArg = "-ExePath '$ExePath'"
    } elseif ($Scenarios -notmatch "download_install") {
      throw "Exe not found on VM: $ExePath (use -DownloadInstall or include download_install)"
    }

    New-Item -ItemType Directory -Force -Path $remoteStatus | Out-Null
    $wrapper = Join-Path $remoteStatus "run.ps1"
    $log = Join-Path $remoteStatus "run.log"
    $done = Join-Path $remoteStatus "done.json"
    $dlFlag = if ($DownloadInstallFlag -eq "1") { "-DownloadInstall" } else { "" }

    @"
`$ErrorActionPreference = 'Continue'
`$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
`$env:PYTHONWARNINGS = 'ignore'
Set-Location '$RemoteRoot'
`$code = 1
try {
  # Capture native exit code BEFORE Tee-Object; piping can clear LASTEXITCODE.
  & '.\scripts\run-rpa-windows.ps1' $exeArg -Scenarios '$Scenarios' -LaunchWait $LaunchWait $dlFlag *> '$log'
  if (`$null -ne `$LASTEXITCODE) { `$code = [int]`$LASTEXITCODE }
  Get-Content -Path '$log' -ErrorAction SilentlyContinue
} catch {
  `$_ | Out-String | Tee-Object -FilePath '$log' -Append
  `$code = 1
}
`$latest = Get-ChildItem (Join-Path '$RemoteRoot' 'artifacts\rpa') -Directory -ErrorAction SilentlyContinue |
  Sort-Object Name -Descending | Select-Object -First 1
@{
  exit_code = `$code
  finished_at = (Get-Date).ToString('o')
  artifact_dir = if (`$latest) { `$latest.FullName } else { `$null }
  log = '$log'
} | ConvertTo-Json | Set-Content -Path '$done' -Encoding UTF8
"@ | Set-Content -Path $wrapper -Encoding UTF8

    $taskName = "VoidBrowserRPA"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

    # Minimized so the console does not cover Void / steal Ctrl+L keystrokes mid-suite.
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
      "-NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File `"$wrapper`""
    )
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
      -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -Compatibility Win8
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null

    $q = (query user 2>&1 | Out-String)
    if ($q -notmatch "Active") {
      Write-Host $q
      throw @"
No Active console session. Autologon should unlock rpa-win after reboot (no VNC required).
Re-apply: VOID_RPA_WIN_PASS=... python scripts/ci/apply-rpa-autologon.py
Diagnose only: VNC http://10.0.0.10:5702/ or virsh screenshot on Unraid.
"@
    }

    Start-ScheduledTask -TaskName $taskName
    Write-Host "Started scheduled task $taskName"
    Write-Host "Status dir: $remoteStatus"
  } -ArgumentList $RemoteRoot, $ExePath, $Scenarios, $LaunchWait, $remoteStatus, $runId, $dlFlagArg

  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  $doneRemote = Join-Path $remoteStatus "done.json"
  Write-Host "Waiting for RPA (timeout ${TimeoutSec}s)..." -ForegroundColor Cyan
  $doneInfo = $null
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $doneInfo = Invoke-Command -Session $session -ScriptBlock {
      param($doneRemote)
      if (Test-Path $doneRemote) {
        Get-Content $doneRemote -Raw
      }
    } -ArgumentList $doneRemote
    if ($doneInfo) { break }
    Write-Host ("  ... still running ({0:n0}s left)" -f ($deadline - (Get-Date)).TotalSeconds)
  }

  if (-not $doneInfo) {
    throw "Timed out waiting for RPA after ${TimeoutSec}s. Check Autologon/session and C:\Users\rpa-win\void-rpa-status\"
  }

  $doneObj = $doneInfo | ConvertFrom-Json
  Write-Host "Remote exit_code=$($doneObj.exit_code) artifact_dir=$($doneObj.artifact_dir)" -ForegroundColor $(
    if ($doneObj.exit_code -eq 0) { "Green" } else { "Yellow" }
  )

  Invoke-Command -Session $session -ScriptBlock {
    param($log)
    if (Test-Path $log) {
      Write-Host "---- remote log (tail) ----"
      Get-Content $log -Tail 60
    }
  } -ArgumentList $doneObj.log

  if ($SkipPull) {
    Write-Host "SkipPull set - artifacts left on VM only."
    exit [int]$doneObj.exit_code
  }

  if (-not $doneObj.artifact_dir) {
    throw "RPA finished but no artifact_dir reported."
  }

  $stamp = Split-Path $doneObj.artifact_dir -Leaf
  $localDir = Join-Path $LocalArtifactsRoot $stamp
  New-Item -ItemType Directory -Force -Path $localDir | Out-Null
  Write-Host "Pulling artifacts -> $localDir" -ForegroundColor Cyan

  $files = Invoke-Command -Session $session -ScriptBlock {
    param($dir)
    Get-ChildItem $dir -File | ForEach-Object { $_.FullName }
  } -ArgumentList $doneObj.artifact_dir

  foreach ($remoteFile in $files) {
    $name = Split-Path $remoteFile -Leaf
    $dest = Join-Path $localDir $name
    Copy-Item -FromSession $session -Path $remoteFile -Destination $dest -Force
    Write-Host "  $name"
  }

  $report = Join-Path $localDir "report.json"
  if (Test-Path $report) {
    Write-Host ""
    Write-Host "Local report: $report" -ForegroundColor Green
    try {
      $r = Get-Content $report -Raw | ConvertFrom-Json
      Write-Host ("Overall ok={0}" -f $r.ok)
      foreach ($sc in $r.scenarios) {
        $mark = if ($sc.ok) { "PASS" } else { "FAIL" }
        Write-Host ("  [{0}] {1} ({2}s)" -f $mark, $sc.name, $sc.duration_sec)
      }
    } catch {
      Write-Host "(could not parse report.json)"
    }
  }

  $mirror = "Z:\void-rpa-artifacts\$stamp"
  try {
    if (Test-Path "Z:\") {
      New-Item -ItemType Directory -Force -Path (Split-Path $mirror) -ErrorAction SilentlyContinue | Out-Null
      Copy-Item -Path (Join-Path $localDir "*") -Destination (New-Item -ItemType Directory -Force -Path $mirror).FullName -Force
      Write-Host "Mirrored to $mirror"
    }
  } catch {
    # optional
  }

  exit [int]$doneObj.exit_code
}
finally {
  if ($session) { Remove-PSSession $session -ErrorAction SilentlyContinue }
}
