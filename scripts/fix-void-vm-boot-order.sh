#!/bin/bash
# Fix Void test VM boot order: CDROM/ISO first, empty disk second.
set -euo pipefail

fix_boot() {
  local name="$1"
  local xml="/etc/libvirt/qemu/${name}.xml"
  cp -a "$xml" "${xml}.bak.$(date +%s)"
  python3 - "$xml" <<'PY'
import sys, re
path = sys.argv[1]
xml = open(path).read()

def fix_disk(m):
    block = m.group(0)
    if "device='cdrom'" in block or 'device="cdrom"' in block:
        # Win11 install ISO should be boot 1; virtio ISO has no boot tag (leave alone)
        if "Win11" in block or "ubuntu" in block or "Ubuntu" in block or "linuxmint" in block:
            if "<boot " in block:
                block = re.sub(r"<boot order=['\"]\d+['\"]/>", "<boot order='1'/>", block)
            else:
                block = block.replace("</disk>", "      <boot order='1'/>\n    </disk>")
        elif "virtio-win" in block:
            # driver ISO — never boot
            block = re.sub(r"\s*<boot order=['\"]\d+['\"]/>\s*", "\n", block)
    elif "device='disk'" in block or 'device="disk"' in block:
        if "<boot " in block:
            block = re.sub(r"<boot order=['\"]\d+['\"]/>", "<boot order='2'/>", block)
        else:
            block = block.replace("</disk>", "      <boot order='2'/>\n    </disk>")
    return block

xml2 = re.sub(r"<disk[\s\S]*?</disk>", fix_disk, xml)
open(path, "w").write(xml2)
print(f"Updated {path}")
PY
}

fix_boot void-test-linux
fix_boot void-rpa-windows

echo "=== Boot order preview ==="
grep -E "device=|boot order|source file" /etc/libvirt/qemu/void-test-linux.xml
echo "---"
grep -E "device=|boot order|source file" /etc/libvirt/qemu/void-rpa-windows.xml

echo "=== Restarting ==="
virsh destroy void-test-linux || true
virsh destroy void-rpa-windows || true
sleep 2
virsh define /etc/libvirt/qemu/void-test-linux.xml
virsh define /etc/libvirt/qemu/void-rpa-windows.xml
virsh start void-test-linux
virsh start void-rpa-windows
sleep 2
virsh list --all
echo "VNC displays:"
virsh vncdisplay void-test-linux
virsh vncdisplay void-rpa-windows
echo "Live boot:"
virsh dumpxml void-test-linux | grep -E "device=|boot order|source file"
echo "---"
virsh dumpxml void-rpa-windows | grep -E "device=|boot order|source file"
