# Test environments: Unraid VMs vs local PC vs CI

Inventory date: 2026-07-18 (host `lightfootserver` / `10.0.0.10`).

## What can test what

| Environment | cargo test | Tauri Linux build | WebView2 GUI / RPA screenshots | Notes |
|-------------|------------|-------------------|----------------------------------|-------|
| **This Windows PC** (danielrig) | Yes (MSVC) | No | **Yes — primary** | Interactive desktop required for WebView2 IPC |
| **GitHub Actions `windows-latest`** | Yes | No | Partial (smoke + optional RPA) | Good for CI; RPA may need `continue-on-error` |
| **GitHub Actions `ubuntu-latest`** | Yes | Yes (.deb / AppImage) | No WebView2 | WebKitGTK only |
| **Unraid Docker** | Yes (Linux containers) | Yes | **No** | Cannot show Windows GUI |
| **Unraid KVM VMs** | If guest has toolchain | If Linux guest | **Only if Windows guest + desktop + RDP/console** | See inventory below |

## Host resources (Unraid)

| Resource | Observed |
|----------|----------|
| CPU | 12 threads |
| RAM | 62 GiB total, ~34 GiB available when idle-ish |
| Array | 7.3T @ ~46% used (~4T free) |
| Cache | 7.3T SSD path available |

Headroom exists for **one** additional mid-size VM (4–8 GiB RAM) without starting the large Windows guests.

## Existing VMs (`virsh list --all`)

| Name | State | vCPU | RAM | Disk | Role for Void |
|------|-------|------|-----|------|----------------|
| `ha` | running | 4 | 4 GiB | Home Assistant OS qcow2 | **Not for browser QA** (HAOS) |
| `fresh-configured` | shut off | 12 | 24 GiB | `/mnt/user/VMs/fresh-configured/vdisk1.img` (500G) + virtio-win ISO | Likely **Windows** guest — candidate for WebView2/RPA if licensed & boots |
| `juliavm` | shut off | 12 | 32 GiB | `/mnt/user/VMs/juliavm/vdisk1.img` (500G) + virtio-win ISO | Likely **Windows** — personal/other; do not assume Void CI |
| `Windows 11` | shut off | 12 | 24 GiB | Points at `/mnt/user/appdata/junk/danielrig.img` + virtio-win ISO | Existing Win11 domain; heavy RAM; verify before use |

**No dedicated Linux build/test VM** was present. Ubuntu desktop ISO is on disk:

`/mnt/user/isos/test/ubuntu-23.10.1-desktop-amd64.iso`

Windows ISOs (license required to activate):

- `/mnt/user/isos/test/Win11_24H2_English_x64.iso`
- `/mnt/user/isos/test/Windows10.iso`
- VirtIO drivers: `/mnt/user/isos/virtio-win-0.1.285-1.iso`

## Recommendation (practical path)

1. **Default RPA + WebView2 QA** → this Windows PC (`.\scripts\run-rpa-windows.ps1`).
2. **Linux unit/build** → GitHub `ubuntu-latest` or Unraid Docker (`scripts/docker-linux-unit-test.sh`).
3. **Unraid Windows VM** → only after manually confirming `fresh-configured` or `Windows 11` boots, has a license, and exposes RDP/WinRM. Do **not** auto-start 24–32 GiB guests from agents without approval (RAM contention with Docker stack).
4. **Optional Linux VM** → create a small Ubuntu 24.04 cloud/desktop VM (4 vCPU / 8 GiB / 40G) named e.g. `void-linux-test` for `cargo test` + WebKitGTK under Xvfb. Do this when you want host-local Linux builds without GitHub.

## Windows license constraints

- ISOs on the server do **not** grant a license.
- Do not pirate or use unattended activation cracks.
- Use an existing licensed guest (`Windows 11` / `fresh-configured` if already activated), a retail/OEM key you own, or Microsoft evaluation/dev entitlements.
- Agents must **not** auto-create Windows VMs unless an ISO **and** explicit license confirmation are both present.

## Create a Linux test VM (Unraid-friendly sketch)

When ready (manual / approved):

1. Unraid UI → VMs → Add VM → Ubuntu, **4 vCPU, 8192 MB RAM**, 40G disk on cache/array.
2. Attach `/mnt/user/isos/test/ubuntu-23.10.1-desktop-amd64.iso` (or a newer 24.04 ISO you download).
3. Install OpenSSH; install Rust + WebKitGTK deps (same as GHA Linux job).
4. Clone via GitLab HTTP: `http://10.0.0.10:8929/lightfootcloud/void-browser.git`
5. Run: `cargo test --manifest-path src-tauri/Cargo.toml`
6. Optional GUI: `xvfb-run -a cargo tauri build` (headless) or console VNC for manual WebKit checks.

Virt-install example (host shell, adjust bridges/paths):

```bash
# Only after you confirm disk/bridge names — do not run blindly
virt-install \
  --name void-linux-test \
  --memory 8192 --vcpus 4 \
  --disk path=/mnt/cache/VMs/void-linux-test/vdisk1.img,size=40,format=raw \
  --cdrom /mnt/user/isos/test/ubuntu-23.10.1-desktop-amd64.iso \
  --network bridge=br0 \
  --os-variant ubuntu22.04 \
  --graphics vnc
```

## Wire RPA to a Windows VM (later)

Once a Windows guest has IP + RDP:

```powershell
# From the Windows guest (or WinRM session), after syncing the repo:
.\scripts\run-rpa-windows.ps1 -SmokeOnly
# Copy artifacts\rpa\<stamp>\ back to the agent host / GitLab artifacts
```

Document the guest IP in a private note (not secrets in git). Optional future GitLab runner tags: `vm-linux`, `vm-windows` — **not** required for the first working path.

## SSH to Unraid host

```powershell
ssh -i C:\Users\rud12\.ssh\id_ed25519_openclaw -o IdentitiesOnly=yes root@10.0.0.10
virsh list --all
```

## Related

- [RPA_TESTING.md](./RPA_TESTING.md)
- [LOCAL_WINDOWS_DEV.md](./LOCAL_WINDOWS_DEV.md)
- [GOOGLE_SIGNIN.md](./GOOGLE_SIGNIN.md)
