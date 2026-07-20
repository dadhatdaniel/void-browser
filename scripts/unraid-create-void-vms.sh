#!/bin/bash
# Create Void Browser test VMs on Unraid (VNC, no GPU passthrough).
# Run on lightfootserver as root.
set -euo pipefail

OVMF_CODE_TPM="/usr/share/qemu/ovmf-x64/OVMF_CODE-pure-efi-tpm.fd"
OVMF_VARS_TPM="/usr/share/qemu/ovmf-x64/OVMF_VARS-pure-efi-tpm.fd"
OVMF_CODE="/usr/share/qemu/ovmf-x64/OVMF_CODE-pure-efi.fd"
OVMF_VARS="/usr/share/qemu/ovmf-x64/OVMF_VARS-pure-efi.fd"
NVRAM_DIR="/etc/libvirt/qemu/nvram"
WIN_ISO="/mnt/user/isos/test/Win11_24H2_English_x64.iso"
VIRTIO_ISO="/mnt/user/isos/virtio-win-0.1.285-1.iso"
UBUNTU_ISO="/mnt/user/isos/test/ubuntu-23.10.1-desktop-amd64.iso"
VM_ROOT="/mnt/user/VMs"

for f in "$OVMF_CODE_TPM" "$OVMF_VARS_TPM" "$OVMF_CODE" "$OVMF_VARS" "$WIN_ISO" "$VIRTIO_ISO" "$UBUNTU_ISO"; do
  test -f "$f" || { echo "MISSING: $f"; exit 1; }
done

mkdir -p "$VM_ROOT/void-test-linux" "$VM_ROOT/void-rpa-windows" "$NVRAM_DIR"

create_linux() {
  local name=void-test-linux
  if virsh dominfo "$name" >/dev/null 2>&1; then
    echo "Domain $name already exists — skipping define"
    return 0
  fi
  local uuid
  uuid=$(uuidgen)
  local disk="$VM_ROOT/$name/vdisk1.img"
  if [[ ! -f "$disk" ]]; then
    echo "Creating $disk (40G sparse)..."
    qemu-img create -f raw "$disk" 40G
  fi
  local nvram="$NVRAM_DIR/${uuid}_VARS-pure-efi.fd"
  cp -n "$OVMF_VARS" "$nvram"
  cat > "/tmp/${name}.xml" <<EOF
<domain type='kvm'>
  <name>${name}</name>
  <uuid>${uuid}</uuid>
  <description>Void Browser Linux test (cargo test / WebKit / Xvfb)</description>
  <metadata>
    <vmtemplate xmlns="unraid" name="Ubuntu" icon="ubuntu.png" os="ubuntu" webui="" storage="default"/>
  </metadata>
  <memory unit='KiB'>8388608</memory>
  <currentMemory unit='KiB'>8388608</currentMemory>
  <memoryBacking>
    <nosharepages/>
  </memoryBacking>
  <vcpu placement='static'>4</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-8.0'>hvm</type>
    <loader readonly='yes' type='pflash' format='raw'>${OVMF_CODE}</loader>
    <nvram format='raw'>${nvram}</nvram>
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
      <source file='${disk}'/>
      <target dev='vda' bus='virtio'/>
      <boot order='1'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${UBUNTU_ISO}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
      <boot order='2'/>
    </disk>
    <controller type='usb' index='0' model='qemu-xhci'/>
    <controller type='sata' index='0'/>
    <controller type='virtio-serial' index='0'/>
    <controller type='pci' index='0' model='pcie-root'/>
    <interface type='bridge'>
      <mac address='52:54:00:76:6f:69'/>
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
  virsh define "/tmp/${name}.xml"
  echo "Defined $name"
}

create_windows() {
  local name=void-rpa-windows
  if virsh dominfo "$name" >/dev/null 2>&1; then
    echo "Domain $name already exists — skipping define"
    return 0
  fi
  local uuid
  uuid=$(uuidgen)
  local disk="$VM_ROOT/$name/vdisk1.img"
  if [[ ! -f "$disk" ]]; then
    echo "Creating $disk (80G sparse)..."
    qemu-img create -f raw "$disk" 80G
  fi
  local nvram="$NVRAM_DIR/${uuid}_VARS-pure-efi-tpm.fd"
  cp -n "$OVMF_VARS_TPM" "$nvram"
  cat > "/tmp/${name}.xml" <<EOF
<domain type='kvm'>
  <name>${name}</name>
  <uuid>${uuid}</uuid>
  <description>Void Browser Windows RPA (WebView2) — complete Win11 setup in VNC; bring your own license</description>
  <metadata>
    <vmtemplate xmlns="unraid" name="Windows 11" icon="windows11.png" os="windowstpm" webui="" storage="default"/>
  </metadata>
  <memory unit='KiB'>8388608</memory>
  <currentMemory unit='KiB'>8388608</currentMemory>
  <memoryBacking>
    <nosharepages/>
  </memoryBacking>
  <vcpu placement='static'>4</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-8.0'>hvm</type>
    <loader readonly='yes' type='pflash' format='raw'>${OVMF_CODE_TPM}</loader>
    <nvram format='raw'>${nvram}</nvram>
  </os>
  <features>
    <acpi/>
    <apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
      <vendor_id state='on' value='none'/>
    </hyperv>
  </features>
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <topology sockets='1' dies='1' clusters='1' cores='2' threads='2'/>
  </cpu>
  <clock offset='localtime'>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <emulator>/usr/local/sbin/qemu</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw' cache='writeback' discard='unmap'/>
      <source file='${disk}'/>
      <target dev='sda' bus='sata'/>
      <boot order='1'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${WIN_ISO}'/>
      <target dev='sdb' bus='sata'/>
      <readonly/>
      <boot order='2'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${VIRTIO_ISO}'/>
      <target dev='sdc' bus='sata'/>
      <readonly/>
    </disk>
    <controller type='usb' index='0' model='qemu-xhci'/>
    <controller type='sata' index='0'/>
    <controller type='virtio-serial' index='0'/>
    <controller type='pci' index='0' model='pcie-root'/>
    <interface type='bridge'>
      <mac address='52:54:00:72:70:61'/>
      <source bridge='br0'/>
      <model type='e1000'/>
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
    <tpm model='tpm-tis'>
      <backend type='emulator' version='2.0' persistent_state='yes'/>
    </tpm>
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
  virsh define "/tmp/${name}.xml"
  echo "Defined $name"
}

create_linux
create_windows

echo
echo "=== Starting VMs ==="
virsh start void-test-linux || true
virsh start void-rpa-windows || true

echo
echo "=== virsh list --all ==="
virsh list --all

echo
echo "=== VNC displays ==="
for d in void-test-linux void-rpa-windows ha; do
  if virsh dominfo "$d" 2>/dev/null | grep -q running; then
    disp=$(virsh vncdisplay "$d" 2>/dev/null || echo "?")
    echo "$d: vncdisplay=$disp  (Unraid UI: VMs → $d → VNC / http://10.0.0.10:5700+offset)"
  else
    echo "$d: not running"
  fi
done

echo
free -h
echo "DONE"
