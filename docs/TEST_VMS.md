# Test environments: Unraid VMs vs local PC vs CI

Inventory date: **2026-07-20** (host `lightfootserver` / `10.0.0.10`).

## Honest status

| Date | What happened |
|------|----------------|
| **2026-07-18** | Docs only — no Void VMs created. |
| **2026-07-20 (earlier)** | Created empty-disk installer VMs (`void-test-linux` + a Win11 ISO `void-rpa-windows`). |
| **2026-07-20 (this update)** | **Cloned** configured Windows guest `fresh-configured` → **`void-rpa-windows`**. GPU passthrough stripped; VNC added. Installer Windows VM renamed to `void-rpa-windows-scratch` (shut off). `void-test-linux` kept. |
| **2026-07-20 (later)** | Disabled **julia** autologin on `void-rpa-windows` (see Guest access). VM stopped at login screen. |
| **2026-07-20 (Autologon)** | Enabled **rpa-win** Winlogon Autologon + no idle lock. After reboot, console Session 1 is Active **without** opening VNC. |
| **2026-07-20 (Linux RPA)** | Recreated Ubuntu 24.04 `void-test-linux`; **GDM + rpa-linux autologin** (Xorg); OpenSSH; GitLab **`rpa-linux`** smoke (AppImage launch + navigate via SSH). |

**Why clone `fresh-configured` instead of Win11 OOBE?**  
User already has a configured Windows disk. Cloning avoids a full reinstall/license OOBE. Original stays shut off with GPU passthrough intact for personal use.

## What can test what

| Environment | cargo test | Tauri Linux build | WebView2 GUI / RPA | Notes |
|-------------|------------|-------------------|--------------------|-------|
| **This Windows PC** (danielrig) | Yes (MSVC) | No | **Yes — primary** | Interactive desktop |
| **GitHub Actions `windows-latest`** | Yes | No | Partial | CI smoke |
| **GitHub Actions `ubuntu-latest`** | Yes | Yes | No WebView2 | WebKitGTK |
| **Unraid Docker** | Yes | Yes | **No** | No Windows GUI |
| **`void-test-linux`** | Optional | Optional | **Yes — smoke** | GDM autologin; AppImage RPA via SSH |
| **`void-rpa-windows`** | Optional (MSVC) | No | **Yes — cloned guest** | VNC; already-configured Windows disk |

## Host resources

| Resource | Observed |
|----------|----------|
| CPU | 12 threads |
| RAM | 62 GiB; `ha` 4 + `void-test-linux` 8 + `void-rpa-windows` 16 ≈ 28 GiB guests |
| Disk clone | `fresh-configured` ~57 GiB used of 500 GiB sparse → same for clone |

Do **not** start `fresh-configured` / `juliavm` / `Windows 11` while `void-rpa-windows` runs (they share the same physical GPU if those GPU VMs are started — clone itself has **no** GPU).

## Guest access (hostnames / usernames only — **no passwords in git**)

Passwords live only on the Unraid host: `/root/void-rpa-vm-credentials.txt` (mode 600). Do not commit them.

| Libvirt name | Guest hostname | Guest IP (br0) | Login user | Notes |
|--------------|----------------|----------------|------------|-------|
| **`void-rpa-windows`** | `JULIARIG` (plan: `rpa-win-vm`) | **`10.0.0.28`** | **`rpa-win`** | WinRM `5985`, RDP `3389`. VNC `http://10.0.0.10:5702/`. |
| **`void-test-linux`** | `rpa-linux-vm` | **`10.0.1.114`** | **`rpa-linux`** | SSH `22`, GDM autologin (Xorg `:0`). VNC `http://10.0.0.10:5701/`. |

### Windows: rpa-win Autologon (required for headless RPA)

**Why VNC used to be required:** after julia Autologon was removed (2026-07-20), reboot left the VM on the login screen (`query user` → no session). UI Automation / scheduled-task RPA needs an **Active console** (Session 1 + `explorer`). Opening noVNC and signing in manually was a workaround — **not** because QEMU VNC must stay connected. VNC/QXL always listens (`0.0.0.0:5902` / websocket `5702`); a human viewer is optional diagnostics only.

**Configured on the guest (password stays on the VM / Unraid secrets — not in git):**

| Setting | Value |
|---------|--------|
| Winlogon `AutoAdminLogon` / `ForceAutoLogon` | `1` |
| `DefaultUserName` | `rpa-win` |
| `DefaultDomainName` | `.` (local account; LogonUI uses `.\rpa-win`) |
| `DefaultPassword` | set via WinRM apply script from `VOID_RPA_WIN_PASS` |
| ARSO `DisableAutomaticRestartSignOn` | `0` (backup after crash reboot) |
| Screensaver / idle lock / NoLockScreen | disabled for this test account |
| `julia` account | remains **disabled** |

**Apply / re-apply (from DANIELRIG or CI host with WinRM):**

```powershell
$env:VOID_RPA_WIN_PASS = '<password>'   # never commit
python scripts/ci/apply-rpa-autologon.py
# optional verify after reboot (does not open VNC):
python scripts/ci/reboot-verify-rpa-session.py
```

On-guest script (same registry changes): `scripts/ci/configure-rpa-autologon.ps1`.

**Verified (2026-07-20):** reboot via WinRM → wait for boot → **do not** open VNC → `query user` shows `rpa-win` console **Active** Session 1 → RPA smoke (`app_launch`,`smoke_navigate`) **PASS**.

**Julia history (still relevant):** blank-password julia + old Autologon/`ForceAutoLogon` were removed; account stays `net user julia /active:no`. Temp note on host only: `/root/void-rpa-windows-julia-temp-password.txt`.

## VMs (`virsh list --all`)

| Name | State | vCPU | RAM | Role |
|------|-------|------|-----|------|
| **`void-rpa-windows`** | **running** | 8 | 16 GiB | **Clone of `fresh-configured`** — Void RPA / WebView2 (VNC, no GPU); log in as `rpa-win` |
| **`void-test-linux`** | **running** | 4 | 8 GiB | Ubuntu guest `rpa-linux-vm` / user `rpa-linux` |
| `ha` | running | 4 | 4 GiB | Home Assistant — **do not touch** |
| `fresh-configured` | shut off | 12 | 24 GiB | **Original** GPU-passthrough Windows — **untouched** |
| `void-rpa-windows-scratch` | shut off | 4 | 8 GiB | Former empty Win11 installer VM (safe to delete later) |
| `juliavm` | shut off | 12 | 32 GiB | Personal GPU VM |
| `Windows 11` | shut off | 12 | 24 GiB | GPU passthrough; not Void RPA |

### Disks

| VM | Disk |
|----|------|
| `fresh-configured` | `/mnt/user/VMs/fresh-configured/vdisk1.img` (original — do not delete) |
| `void-rpa-windows` | `/mnt/user/VMs/void-rpa-windows/vdisk1.img` (sparse clone, ~57 GiB used / 500 GiB virt) |
| `void-rpa-windows-scratch` | `/mnt/user/VMs/void-rpa-windows-scratch/vdisk1.img` (empty 80G installer disk) |
| `void-test-linux` | `/mnt/user/VMs/void-test-linux/vdisk1.img` (40G + Ubuntu ISO) |

### Console / VNC

| VM | VNC TCP | noVNC (browser) | Unraid UI |
|----|---------|-----------------|-----------|
| `ha` | `10.0.0.10:5900` | `http://10.0.0.10:5700/` | VMs → ha → VNC |
| `void-test-linux` | `10.0.0.10:5901` | `http://10.0.0.10:5701/` | VMs → void-test-linux → **VNC** |
| **`void-rpa-windows`** | **`10.0.0.10:5902`** | **`http://10.0.0.10:5702/`** | VMs → **void-rpa-windows** → **VNC** |

Ports are libvirt autoport; re-check after restart:

```bash
ssh -i C:\Users\rud12\.ssh\id_ed25519_openclaw -o IdentitiesOnly=yes root@10.0.0.10
virsh list --all
virsh vncdisplay void-rpa-windows   # e.g. :2 → 5902 / 5702
```

**Access path for RPA**

1. After reboot, **rpa-win Autologon** should land an unlocked desktop automatically (no VNC step).
2. Trigger RPA via WinRM (`scripts/rpa-remote-run.ps1` or GitLab `rpa-windows`) — see [RPA_TESTING.md](./RPA_TESTING.md).
3. **Optional diagnose only:** Unraid VMs → **void-rpa-windows** → VNC, or `http://10.0.0.10:5702/`, or `virsh screenshot void-rpa-windows` on the host.
4. If Windows complains about hardware change / reactivation, use your license/account (clone may trigger reactivation).

No physical GPU monitor is required — QEMU VNC/QXL is enough, and a human viewer is not required once Autologon works. Leave `fresh-configured` off unless you intentionally want the GTX 1080 Ti passthrough desktop.

### Clone notes (what we changed on the clone only)

- **Kept:** Windows disk contents (configured guest).
- **Removed:** PCI `hostdev` GPU (GTX 1080 Ti + audio) so the host keeps the GPU.
- **Added:** VNC (`0.0.0.0`) + QXL video + USB tablet.
- **Adjusted:** 16 GiB RAM / 8 vCPU (original is 24 GiB / 12); new UUID, MAC, NVRAM copy.
- **Original `fresh-configured`:** unchanged XML, unchanged disk, still shut off with GPU passthrough.

Recreate helper: [`scripts/unraid-clone-fresh-to-void-rpa.sh`](../scripts/unraid-clone-fresh-to-void-rpa.sh)

### Start / stop

```bash
virsh start void-rpa-windows
virsh start void-test-linux
virsh shutdown void-rpa-windows
virsh shutdown void-test-linux
```

Optional cleanup later (installer leftovers only — **never** delete `fresh-configured` disk):

```bash
virsh undefine void-rpa-windows-scratch --nvram
# then rm -rf /mnt/user/VMs/void-rpa-windows-scratch   # only if you confirm it is the empty installer disk
```

## `void-test-linux` (Linux RPA)

1. VNC (optional diagnose): `http://10.0.0.10:5701/`
2. Guest: `rpa-linux` @ `10.0.1.114` — **GDM autologin** (Xorg `:0`); OpenSSH enabled.
3. GitLab job **`rpa-linux`** → `scripts/ci/rpa-ssh.py` → full Linux suite (`tests/rpa/runner_linux.py`): download AppImage from `releases.json`, launch, navigate / settings / tabs / YouTube (soft) / auto_update, teardown.
4. Re-apply desktop config: `bash scripts/ci/configure-rpa-linux-desktop.sh` on the guest (or `--configure-desktop` via rpa-ssh).
5. Local trigger from DANIELRIG:

```powershell
$env:VOID_RPA_LINUX_PASS = '<password>'   # never commit
python scripts/ci/rpa-ssh.py --push-harness --download-install
```

Requires GitLab CI variable **`VOID_RPA_LINUX_PASS`** (masked).

## Windows license

Cloning a licensed disk can still require reactivation after hardware change (no GPU, new virt UUID/MAC). Use your own key/account. Do not pirate.

## Recommendation

1. **RPA now** → this PC, or `void-rpa-windows` via WinRM (Autologon; VNC optional for diagnose).
2. **Linux RPA** → `void-test-linux` via GitLab `rpa-linux` / `scripts/ci/rpa-ssh.py` (full-ish suite; AT-SPI limited).
3. **Do not** start `fresh-configured` for agent RPA (no VNC; steals GPU).

## SSH

```powershell
ssh -i C:\Users\rud12\.ssh\id_ed25519_openclaw -o IdentitiesOnly=yes root@10.0.0.10
virsh list --all
```

## Related

- [RPA_TESTING.md](./RPA_TESTING.md)
- [LOCAL_WINDOWS_DEV.md](./LOCAL_WINDOWS_DEV.md)
- Clone script: [`scripts/unraid-clone-fresh-to-void-rpa.sh`](../scripts/unraid-clone-fresh-to-void-rpa.sh)
