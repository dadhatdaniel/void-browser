#!/bin/bash
# After unraid-recreate-void-test-linux.sh autoinstall completes (domain stops),
# redefine void-test-linux to boot from disk only and start it.
set -euo pipefail

NAME="${VOID_LINUX_VM_NAME:-void-test-linux}"
VM_ROOT="${VOID_VM_ROOT:-/mnt/user/VMs}"
DIR="$VM_ROOT/$NAME"
DISK="$DIR/vdisk1.img"
CIDATA="$DIR/cidata.img"
OVMF_CODE="/usr/share/qemu/ovmf-x64/OVMF_CODE-pure-efi.fd"
NVRAM_DIR="/etc/libvirt/qemu/nvram"
RAM_KIB="${VOID_LINUX_RAM_KIB:-4194304}"
VCPUS="${VOID_LINUX_VCPUS:-4}"
MAC="${VOID_LINUX_MAC:-52:54:00:76:6f:69}"

die() { echo "ERROR: $*" >&2; exit 1; }

test -f "$DISK" || die "missing disk $DISK"
USED=$(qemu-img info "$DISK" | awk '/^disk size:/{print $3,$4}')
echo "Disk usage: $USED"
# Refuse if still basically empty
BYTES=$(qemu-img info --output=json "$DISK" | python3 -c 'import json,sys; print(json.load(sys.stdin)["actual-size"])')
if [[ "$BYTES" -lt 500000000 ]]; then
  die "disk still tiny ($BYTES bytes) — autoinstall likely did not finish"
fi

# Wait if still running
if virsh dominfo "$NAME" >/dev/null 2>&1; then
  STATE=$(virsh domstate "$NAME")
  if [[ "$STATE" == "running" ]]; then
    echo "Domain still running — waiting up to 90m for install reboot/destroy..."
    for i in $(seq 1 540); do
      STATE=$(virsh domstate "$NAME" 2>/dev/null || echo shut)
      [[ "$STATE" != "running" ]] && break
      if (( i % 12 == 0 )); then
        echo "  still installing... $(date +%H:%M:%S) disk=$(qemu-img info "$DISK" | awk '/^disk size:/{print $3,$4}')"
      fi
      sleep 10
    done
  fi
  if [[ "$(virsh domstate "$NAME" 2>/dev/null || echo shut)" == "running" ]]; then
    die "timed out waiting for installer to finish"
  fi
  # Capture NVRAM path + uuid from old XML if present
  OLD_XML=$(virsh dumpxml "$NAME" 2>/dev/null || true)
  virsh undefine "$NAME" --nvram 2>/dev/null || virsh undefine "$NAME" || true
else
  OLD_XML=""
fi

UUID=$(printf '%s' "$OLD_XML" | python3 -c 'import re,sys,uuid; xml=sys.stdin.read(); m=re.search(r"<uuid>([^<]+)</uuid>", xml or ""); print(m.group(1) if m else str(uuid.uuid4()))')
NVRAM=$(printf '%s' "$OLD_XML" | python3 -c 'import re,sys,uuid; xml=sys.stdin.read(); m=re.search(r"<nvram[^>]*>([^<]+)</nvram>", xml or ""); print(m.group(1) if m else f"/etc/libvirt/qemu/nvram/{uuid.uuid4()}_VARS-pure-efi.fd")')

if [[ ! -f "$NVRAM" ]]; then
  cp /usr/share/qemu/ovmf-x64/OVMF_VARS-pure-efi.fd "$NVRAM"
fi

# Boot from installed disk only (no ISO, no direct kernel). Keep CIDATA optional (harmless).
cat > "/tmp/${NAME}-final.xml" <<EOF
<domain type='kvm'>
  <name>${NAME}</name>
  <uuid>${UUID}</uuid>
  <description>Void Browser Linux RPA/test — Ubuntu 24.04 (autoinstalled)</description>
  <metadata>
    <vmtemplate xmlns="unraid" name="Ubuntu" icon="ubuntu.png" os="ubuntu" webui="" storage="default"/>
  </metadata>
  <memory unit='KiB'>${RAM_KIB}</memory>
  <currentMemory unit='KiB'>${RAM_KIB}</currentMemory>
  <memoryBacking>
    <nosharepages/>
  </memoryBacking>
  <vcpu placement='static'>${VCPUS}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-8.0'>hvm</type>
    <loader readonly='yes' type='pflash' format='raw'>${OVMF_CODE}</loader>
    <nvram format='raw'>${NVRAM}</nvram>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <topology sockets='1' dies='1' clusters='1' cores='2' threads='2'/>
  </cpu>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <emulator>/usr/local/sbin/qemu</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw' cache='writeback' discard='unmap'/>
      <source file='${DISK}'/>
      <target dev='vda' bus='virtio'/>
      <boot order='1'/>
    </disk>
    <controller type='usb' index='0' model='qemu-xhci'/>
    <controller type='sata' index='0'/>
    <controller type='virtio-serial' index='0'/>
    <controller type='pci' index='0' model='pcie-root'/>
    <interface type='bridge'>
      <mac address='${MAC}'/>
      <source bridge='br0'/>
      <model type='virtio'/>
    </interface>
    <serial type='pty'>
      <target type='isa-serial' port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <graphics type='vnc' port='-1' autoport='yes' websocket='-1' listen='0.0.0.0' keymap='en-us' sharePolicy='ignore'>
      <listen type='address' address='0.0.0.0'/>
    </graphics>
    <audio id='1' type='none'/>
    <video>
      <model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1' primary='yes'/>
    </video>
    <memballoon model='virtio'/>
  </devices>
</domain>
EOF

virsh define "/tmp/${NAME}-final.xml"
virsh start "$NAME"
sleep 2
echo "Started $NAME — waiting for guest agent / SSH..."
for i in $(seq 1 120); do
  if virsh qemu-agent-command "$NAME" '{"execute":"guest-ping"}' >/dev/null 2>&1; then
    echo "Guest agent OK"
    virsh qemu-agent-command "$NAME" '{"execute":"guest-network-get-interfaces"}' --pretty 2>/dev/null | head -80 || true
    break
  fi
  sleep 5
done

DISP=$(virsh vncdisplay "$NAME" 2>/dev/null || echo "?")
echo "VNC display: $DISP  (noVNC http://10.0.0.10:5701/ if :1)"
virsh list --all
echo "DONE finalize"
