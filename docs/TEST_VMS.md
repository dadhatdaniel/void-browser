# Test environments: Unraid VMs vs local PC vs CI

Inventory date: **2026-07-20** (host `lightfootserver` / `10.0.0.10`).

## Honest status

| Date | What happened |
|------|----------------|
| **2026-07-18** | Docs only — no Void VMs created. |
| **2026-07-20 (earlier)** | Created empty-disk installer VMs (`void-test-linux` + a Win11 ISO `void-rpa-windows`). |
| **2026-07-20 (this update)** | **Cloned** configured Windows guest `fresh-configured` → **`void-rpa-windows`**. GPU passthrough stripped; VNC added. Installer Windows VM renamed to `void-rpa-windows-scratch` (shut off). `void-test-linux` kept. |

**Why clone `fresh-configured` instead of Win11 OOBE?**  
User already has a configured Windows disk. Cloning avoids a full reinstall/license OOBE. Original stays shut off with GPU passthrough intact for personal use.

## What can test what

| Environment | cargo test | Tauri Linux build | WebView2 GUI / RPA | Notes |
|-------------|------------|-------------------|--------------------|-------|
| **This Windows PC** (danielrig) | Yes (MSVC) | No | **Yes — primary** | Interactive desktop |
| **GitHub Actions `windows-latest`** | Yes | No | Partial | CI smoke |
| **GitHub Actions `ubuntu-latest`** | Yes | Yes | No WebView2 | WebKitGTK |
| **Unraid Docker** | Yes | Yes | **No** | No Windows GUI |
| **`void-test-linux`** | After Ubuntu install | After deps | No | VNC; ISO install |
| **`void-rpa-windows`** | Optional (MSVC) | No | **Yes — cloned guest** | VNC; already-configured Windows disk |

## Host resources

| Resource | Observed |
|----------|----------|
| CPU | 12 threads |
| RAM | 62 GiB; `ha` 4 + `void-test-linux` 8 + `void-rpa-windows` 16 ≈ 28 GiB guests |
| Disk clone | `fresh-configured` ~57 GiB used of 500 GiB sparse → same for clone |

Do **not** start `fresh-configured` / `juliavm` / `Windows 11` while `void-rpa-windows` runs (they share the same physical GPU if those GPU VMs are started — clone itself has **no** GPU).

## VMs (`virsh list --all`)

| Name | State | vCPU | RAM | Role |
|------|-------|------|-----|------|
| **`void-rpa-windows`** | **running** | 8 | 16 GiB | **Clone of `fresh-configured`** — Void RPA / WebView2 (VNC, no GPU) |
| **`void-test-linux`** | **running** | 4 | 8 GiB | Ubuntu ISO → cargo/WebKit tests |
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

**Access path for RPA setup**

1. Open Unraid: `http://10.0.0.10` → **VMs**.
2. Click **`void-rpa-windows`** → **VNC** (or open `http://10.0.0.10:5702/` directly).
3. Log into the existing Windows session (same guest as `fresh-configured`, new MAC / no GPU — display is QXL/VNC).
4. If Windows complains about hardware change / reactivation, use your license/account (clone may trigger reactivation).
5. Install/run Void Browser + `.\scripts\run-rpa-windows.ps1` on the guest.

No physical GPU monitor is required for the clone — VNC only. Leave `fresh-configured` off unless you intentionally want the GTX 1080 Ti passthrough desktop.

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

## `void-test-linux` next steps

1. VNC: `http://10.0.0.10:5701/`
2. Finish Ubuntu install from attached ISO.
3. OpenSSH + Rust + WebKitGTK deps → `cargo test --manifest-path src-tauri/Cargo.toml`

## Windows license

Cloning a licensed disk can still require reactivation after hardware change (no GPU, new virt UUID/MAC). Use your own key/account. Do not pirate.

## Recommendation

1. **RPA now** → this PC, or `void-rpa-windows` via VNC once you can log in.
2. **Linux unit/build** → GHA `ubuntu-latest`, Docker, or finish `void-test-linux`.
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
