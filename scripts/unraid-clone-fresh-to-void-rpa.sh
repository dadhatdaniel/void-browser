#!/bin/bash
# Clone fresh-configured -> void-rpa-windows (VNC, no GPU). Leave original untouched.
set -euo pipefail

SRC_NAME=fresh-configured
SRC_DISK=/mnt/user/VMs/fresh-configured/vdisk1.img
SRC_NVRAM=/etc/libvirt/qemu/nvram/68726d3c-dfb8-2c8d-eef2-874d856912ae_VARS-pure-efi-tpm.fd
DST_NAME=void-rpa-windows
SCRATCH_NAME=void-rpa-windows-scratch
VM_ROOT=/mnt/user/VMs
NVRAM_DIR=/etc/libvirt/qemu/nvram
OVMF_VARS_TPM=/usr/share/qemu/ovmf-x64/OVMF_VARS-pure-efi-tpm.fd

echo "=== 1) Retire installer void-rpa-windows if present ==="
if virsh dominfo "$DST_NAME" >/dev/null 2>&1; then
  STATE=$(virsh domstate "$DST_NAME" 2>/dev/null || true)
  echo "Found $DST_NAME state=$STATE"
  if [[ "$STATE" == "running" ]]; then
    virsh destroy "$DST_NAME" || true
  fi
  if virsh dominfo "$SCRATCH_NAME" >/dev/null 2>&1; then
    echo "Scratch name already exists — undefining old installer domain only (keep its disk if any)"
    virsh undefine "$DST_NAME" --nvram 2>/dev/null || virsh undefine "$DST_NAME" || true
  else
    # Rename domain + move disk dir
    virsh dumpxml "$DST_NAME" > "/tmp/${SCRATCH_NAME}.xml"
    SCRATCH_UUID=$(python3 -c "import uuid; print(uuid.uuid4())")
    # Update XML name/uuid/disk path
    python3 - <<PY
from pathlib import Path
import re, uuid
xml = Path("/tmp/${SCRATCH_NAME}.xml").read_text()
xml = re.sub(r"<name>.*?</name>", "<name>${SCRATCH_NAME}</name>", xml, count=1)
xml = re.sub(r"<uuid>.*?</uuid>", f"<uuid>{uuid.uuid4()}</uuid>", xml, count=1)
xml = xml.replace("/mnt/user/VMs/void-rpa-windows/", "/mnt/user/VMs/void-rpa-windows-scratch/")
xml = re.sub(r'\s*<graphics[\s\S]*?</graphics>', '', xml)
# keep as-is otherwise
Path("/tmp/${SCRATCH_NAME}.xml").write_text(xml)
print("scratch xml prepared")
PY
    virsh undefine "$DST_NAME" --nvram 2>/dev/null || virsh undefine "$DST_NAME" || true
    if [[ -d "$VM_ROOT/void-rpa-windows" ]]; then
      mv "$VM_ROOT/void-rpa-windows" "$VM_ROOT/void-rpa-windows-scratch"
    fi
    # Fix NVRAM path in scratch xml to avoid conflict — copy template
    SCRATCH_NVRAM="$NVRAM_DIR/${SCRATCH_UUID}_VARS-pure-efi-tpm.fd"
    cp -n "$OVMF_VARS_TPM" "$SCRATCH_NVRAM"
    python3 - <<PY
from pathlib import Path
import re, uuid
u = "${SCRATCH_UUID}"
xml = Path("/tmp/${SCRATCH_NAME}.xml").read_text()
xml = re.sub(r"<uuid>.*?</uuid>", f"<uuid>{u}</uuid>", xml, count=1)
xml = re.sub(r"<nvram[^>]*>.*?</nvram>", f"<nvram format='raw'>{u}</nvram>".replace(u, "/etc/libvirt/qemu/nvram/"+u+"_VARS-pure-efi-tpm.fd"), xml, count=1)
# simpler nvram replace
xml = re.sub(r"(<nvram[^>]*>)[^<]+(</nvram>)", r"\1/etc/libvirt/qemu/nvram/"+u+r"_VARS-pure-efi-tpm.fd\2", xml, count=1)
Path("/tmp/${SCRATCH_NAME}.xml").write_text(xml)
PY
    virsh define "/tmp/${SCRATCH_NAME}.xml"
    echo "Installer renamed to $SCRATCH_NAME (shut off)"
  fi
fi

# Ensure destination name free
if virsh dominfo "$DST_NAME" >/dev/null 2>&1; then
  echo "ERROR: $DST_NAME still exists"; virsh list --all; exit 1
fi

echo "=== 2) Verify source untouched ==="
test -f "$SRC_DISK"
virsh dominfo "$SRC_NAME" >/dev/null
echo "Source OK: $SRC_NAME disk=$(du -h "$SRC_DISK" | awk '{print $1}') virtual=$(qemu-img info "$SRC_DISK" | awk '/virtual size/{print $3,$4}')"

echo "=== 3) Clone disk (sparse) ==="
DST_DIR="$VM_ROOT/$DST_NAME"
DST_DISK="$DST_DIR/vdisk1.img"
mkdir -p "$DST_DIR"
if [[ -f "$DST_DISK" ]]; then
  echo "Destination disk already exists — reusing $DST_DISK"
else
  # Prefer reflink on same FS for instant clone; fall back to sparse copy
  if cp --reflink=always --sparse=always "$SRC_DISK" "$DST_DISK" 2>/dev/null; then
    echo "Cloned via reflink"
  else
    echo "Reflink unavailable — sparse copy (may take several minutes)..."
    # qemu-img convert preserves sparseness well
    qemu-img convert -p -f raw -O raw "$SRC_DISK" "$DST_DISK"
  fi
fi
ls -lah "$DST_DISK"
qemu-img info "$DST_DISK" | head -8

echo "=== 4) New UUID / NVRAM / MAC ==="
DST_UUID=$(cat /proc/sys/kernel/random/uuid)
DST_NVRAM="$NVRAM_DIR/${DST_UUID}_VARS-pure-efi-tpm.fd"
if [[ -f "$SRC_NVRAM" ]]; then
  cp -f "$SRC_NVRAM" "$DST_NVRAM"
else
  cp -f "$OVMF_VARS_TPM" "$DST_NVRAM"
fi
chmod 600 "$DST_NVRAM"
# Generate unique MAC (void-ish)
DST_MAC="52:54:00:76:72:$(printf '%02x' $((RANDOM%256)))"

echo "UUID=$DST_UUID MAC=$DST_MAC"

echo "=== 5) Build clone XML (strip GPU, add VNC, 16GiB RAM) ==="
virsh dumpxml "$SRC_NAME" > /tmp/fresh-src.xml
python3 - <<PY
from pathlib import Path
import re, uuid

src = Path("/tmp/fresh-src.xml").read_text()
name = "${DST_NAME}"
uid = "${DST_UUID}"
mac = "${DST_MAC}"
disk = "${DST_DISK}"
nvram = "${DST_NVRAM}"

# Remove live-only id
src = re.sub(r"<domain type='kvm'[^>]*>", "<domain type='kvm'>", src, count=1)
src = re.sub(r"<name>.*?</name>", f"<name>{name}</name>", src, count=1)
src = re.sub(r"<uuid>.*?</uuid>", f"<uuid>{uid}</uuid>", src, count=1)
src = re.sub(r"<description>.*?</description>",
             "<description>Void Browser RPA clone of fresh-configured — VNC, no GPU passthrough</description>",
             src, count=1)
# Memory: 16 GiB (safer than 24 with void-test-linux + ha)
src = re.sub(r"<memory unit='KiB'>\d+</memory>", "<memory unit='KiB'>16777216</memory>", src, count=1)
src = re.sub(r"<currentMemory unit='KiB'>\d+</currentMemory>", "<currentMemory unit='KiB'>16777216</currentMemory>", src, count=1)
# vCPU: keep 8 instead of 12 to leave headroom
src = re.sub(r"<vcpu placement='static'>\d+</vcpu>", "<vcpu placement='static'>8</vcpu>", src, count=1)
# Strip cputune pins (were for 12 vCPU)
src = re.sub(r"<cputune>[\s\S]*?</cputune>\s*", "", src, count=1)
# Topology cores/threads for 8 = 4c*2t
src = re.sub(
    r"<topology sockets='1' dies='1' clusters='1' cores='\d+' threads='\d+'/>",
    "<topology sockets='1' dies='1' clusters='1' cores='4' threads='2'/>",
    src, count=1,
)
# Disk path
src = re.sub(
    r"<source file='/mnt/user/VMs/fresh-configured/vdisk1\.img'/>",
    f"<source file='{disk}'/>",
    src, count=1,
)
# NVRAM
src = re.sub(r"<nvram[^>]*>.*?</nvram>", f"<nvram format='raw'>{nvram}</nvram>", src, count=1)
# New MAC
src = re.sub(r"<mac address='[^']+'/>", f"<mac address='{mac}'/>", src, count=1)
# Strip ALL hostdev (GPU)
src = re.sub(r"\s*<hostdev[\s\S]*?</hostdev>", "", src)
# Strip existing graphics/video if any
src = re.sub(r"\s*<graphics[\s\S]*?</graphics>", "", src)
src = re.sub(r"\s*<video[\s\S]*?</video>", "", src)
# Ensure tablet input exists for VNC mouse
if "<input type='tablet'" not in src:
    src = src.replace(
        "<input type='mouse' bus='ps2'/>",
        "<input type='tablet' bus='usb'/>\n    <input type='mouse' bus='ps2'/>",
    )
# Insert VNC + QXL before memballoon or before </devices>
vnc_block = """
    <graphics type='vnc' port='-1' autoport='yes' websocket='-1' listen='0.0.0.0' keymap='en-us'>
      <listen type='address' address='0.0.0.0'/>
    </graphics>
    <video>
      <model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1' primary='yes'/>
    </video>"""
if "<memballoon" in src:
    src = src.replace("<memballoon", vnc_block + "\n    <memballoon", 1)
else:
    src = src.replace("</devices>", vnc_block + "\n  </devices>", 1)

# Metadata storage note
src = src.replace('storage="cache"', 'storage="default"')
src = src.replace(
    'xmlns="http://unraid"',
    'xmlns="unraid"',
)

Path(f"/tmp/{name}.xml").write_text(src)
print(f"Wrote /tmp/{name}.xml")
# sanity
assert "hostdev" not in src
assert "vnc" in src
assert disk in src
assert name in src
print("sanity OK")
PY

virsh define "/tmp/${DST_NAME}.xml"
echo "Defined $DST_NAME"

echo "=== 6) Confirm original fresh-configured unchanged ==="
virsh dumpxml fresh-configured | grep -E "name>|source file|hostdev|graphics" | head -20
test -f "$SRC_DISK"
ls -lah "$SRC_DISK"

echo "=== 7) Start clone ==="
virsh start "$DST_NAME"
sleep 3
virsh list --all
echo "VNC:"
virsh vncdisplay "$DST_NAME" || true
ss -ltnp | grep -E '590[0-9]|570[0-9]' | head -10
echo "=== DONE ==="
virsh dumpxml "$DST_NAME" | grep -E "name>|memory|vcpu|source file|graphics|hostdev|mac address" | head -30
