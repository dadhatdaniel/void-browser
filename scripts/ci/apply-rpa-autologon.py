#!/usr/bin/env python3
"""Short Autologon fix: DefaultDomainName=., passwordreq:yes, registry password."""
from __future__ import annotations

import base64
import os
import sys

import winrm


def main() -> int:
    password = os.environ.get("VOID_RPA_WIN_PASS", "").strip()
    if not password:
        print("VOID_RPA_WIN_PASS required", file=sys.stderr)
        return 2
    host = os.environ.get("VOID_RPA_WIN_HOST", "10.0.0.28").strip()
    user = os.environ.get("VOID_RPA_WIN_USER", "rpa-win").strip()
    endpoint = host if host.startswith("http") else f"http://{host}:5985/wsman"
    sess = winrm.Session(endpoint, auth=(user, password), transport="ntlm")
    pass_b64 = base64.b64encode(password.encode()).decode()
    user_lit = user.replace("'", "''")

    ps = f"""
$ErrorActionPreference = 'Stop'
$pass = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{pass_b64}'))
$user = '{user_lit}'
cmd /c "net user $user $pass /passwordreq:yes"
$wl = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon'
Set-ItemProperty $wl DefaultDomainName '.' -Type String
Set-ItemProperty $wl DefaultUserName $user -Type String
Set-ItemProperty $wl DefaultPassword $pass -Type String
Set-ItemProperty $wl AutoAdminLogon '1' -Type String
Set-ItemProperty $wl ForceAutoLogon '1' -Type String
Set-ItemProperty $wl EnableFirstLogonAnimation 0 -Type DWord
Remove-ItemProperty $wl AutoLogonCount -EA SilentlyContinue
# Disable lock screen blur / Windows Hello convenience if present
$ls = 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\Personalization'
if (-not (Test-Path $ls)) {{ New-Item $ls -Force | Out-Null }}
Set-ItemProperty $ls NoLockScreen 1 -Type DWord
$c = Get-ItemProperty $wl
"domain=$($c.DefaultDomainName) auto=$($c.AutoAdminLogon) force=$($c.ForceAutoLogon) passLen=$($c.DefaultPassword.Length)"
net user $user | findstr /i "Password required"
$pass = $null
'OK'
"""
    r = sess.run_ps(ps)
    print((r.std_out or b"").decode("utf-8", "replace"))
    err = (r.std_err or b"").decode("utf-8", "replace")
    meaningful = [
        ln
        for ln in err.splitlines()
        if ln.strip()
        and "CLIXML" not in ln
        and not ln.strip().startswith("<")
        and "xmlns=" not in ln
        and "Preparing modules" not in ln
    ]
    if meaningful:
        print("STDERR:", "\n".join(meaningful)[:2000], file=sys.stderr)
    return int(r.status_code)


if __name__ == "__main__":
    raise SystemExit(main())
