# Configure void-rpa-windows for headless RPA after reboot.
# Run on the guest as rpa-win (local admin), or via:
#   VOID_RPA_WIN_PASS=... python scripts/ci/apply-rpa-autologon.py
#
# Secrets: VOID_RPA_WIN_PASS in the process env only — never commit passwords.
# Mechanism: Winlogon AutoAdminLogon + ForceAutoLogon for local account rpa-win.
# QEMU VNC (Unraid :5902 / noVNC :5702) can stay listening; a human viewer is NOT required.

$ErrorActionPreference = 'Stop'

$pass = $env:VOID_RPA_WIN_PASS
if ([string]::IsNullOrWhiteSpace($pass)) {
  throw 'VOID_RPA_WIN_PASS is required (set in process env; do not hardcode).'
}

$user = if ($env:VOID_RPA_WIN_USER) { $env:VOID_RPA_WIN_USER } else { 'rpa-win' }

# Ensure password matches and is required (blank-password mode breaks Autologon)
cmd /c "net user $user $pass /passwordreq:yes" | Out-Null

# --- Autologon (Winlogon) ---
# DefaultDomainName must be '.' for this local account (LogonUI uses .\rpa-win).
$wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty -Path $wl -Name AutoAdminLogon -Value '1' -Type String
Set-ItemProperty -Path $wl -Name DefaultUserName -Value $user -Type String
Set-ItemProperty -Path $wl -Name DefaultDomainName -Value '.' -Type String
Set-ItemProperty -Path $wl -Name DefaultPassword -Value $pass -Type String
Set-ItemProperty -Path $wl -Name ForceAutoLogon -Value '1' -Type String
Set-ItemProperty -Path $wl -Name EnableFirstLogonAnimation -Value 0 -Type DWord
Remove-ItemProperty -Path $wl -Name AutoLogonCount -ErrorAction SilentlyContinue

# --- ARSO backup after unexpected restart while logged in ---
$sysPol = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
if (-not (Test-Path $sysPol)) { New-Item -Path $sysPol -Force | Out-Null }
Set-ItemProperty -Path $sysPol -Name DisableAutomaticRestartSignOn -Value 0 -Type DWord
Set-ItemProperty -Path $sysPol -Name InactivityTimeoutSecs -Value 0 -Type DWord

# --- Classic password prompt (not passwordless device mode) ---
$pl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device'
if (-not (Test-Path $pl)) { New-Item -Path $pl -Force | Out-Null }
Set-ItemProperty -Path $pl -Name DevicePasswordLessBuildVersion -Value 0 -Type DWord

# --- No lock screen for this test VM ---
$ls = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization'
if (-not (Test-Path $ls)) { New-Item -Path $ls -Force | Out-Null }
Set-ItemProperty -Path $ls -Name NoLockScreen -Value 1 -Type DWord

$desk = 'HKCU:\Control Panel\Desktop'
Set-ItemProperty -Path $desk -Name ScreenSaveActive -Value '0' -Type String
Set-ItemProperty -Path $desk -Name ScreenSaverIsSecure -Value '0' -Type String
Set-ItemProperty -Path $desk -Name ScreenSaveTimeOut -Value '0' -Type String

$cuSys = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\System'
if (-not (Test-Path $cuSys)) { New-Item -Path $cuSys -Force | Out-Null }
Set-ItemProperty -Path $cuSys -Name DisableLockWorkstation -Value 1 -Type DWord
Set-ItemProperty -Path $cuSys -Name NoLockScreen -Value 1 -Type DWord

powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_NONE CONSOLELOCK 0 2>$null
powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_NONE CONSOLELOCK 0 2>$null
powercfg /SETACTIVE SCHEME_CURRENT 2>$null

# --- Verify (no password echo) ---
$check = Get-ItemProperty $wl | Select-Object AutoAdminLogon, DefaultUserName, DefaultDomainName, ForceAutoLogon
$hasPass = -not [string]::IsNullOrEmpty((Get-ItemProperty $wl).DefaultPassword)
Write-Output 'CONFIGURED Autologon:'
Write-Output ($check | Format-List | Out-String)
Write-Output "DefaultPasswordSet=$hasPass"
Write-Output "DisableAutomaticRestartSignOn=$((Get-ItemProperty $sysPol).DisableAutomaticRestartSignOn)"
Write-Output "InactivityTimeoutSecs=$((Get-ItemProperty $sysPol).InactivityTimeoutSecs)"
Write-Output "ScreenSaveActive=$((Get-ItemProperty $desk).ScreenSaveActive)"
Write-Output 'OK — reboot to verify Active console without opening VNC'
