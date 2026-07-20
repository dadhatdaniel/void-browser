#!/usr/bin/env bash
# Configure void-test-linux for headless Linux RPA after reboot.
# - GDM autologin as rpa-linux (Xorg)
# - No idle lock / screensaver
# - Mark gnome-initial-setup done
#
# Run ON the guest (or via scripts/ci/rpa-ssh.py --configure-desktop).
# Password stays on the VM / Unraid secrets — not in git.
set -euo pipefail

USER_NAME="${VOID_RPA_LINUX_USER:-rpa-linux}"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

echo "[rpa-linux-desktop] configuring GDM autologin for $USER_NAME"

$SUDO tee /etc/gdm3/custom.conf >/dev/null <<EOF
# GDM configuration — Void RPA Linux (managed by configure-rpa-linux-desktop.sh)
[daemon]
AutomaticLoginEnable=true
AutomaticLogin=${USER_NAME}
WaylandEnable=false

[security]

[xdmcp]

[chooser]

[debug]
EOF

$SUDO systemctl set-default graphical.target
$SUDO systemctl restart gdm || $SUDO systemctl restart gdm3 || true

# Per-user session prefs (best-effort; may need active session)
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
if [[ -n "$HOME_DIR" ]]; then
  mkdir -p "$HOME_DIR/.config"
  echo yes > "$HOME_DIR/.config/gnome-initial-setup-done"
  chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/.config/gnome-initial-setup-done" || true
fi

if [[ -S "/run/user/$(id -u)/bus" ]]; then
  export DISPLAY="${DISPLAY:-:0}"
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  gsettings set org.gnome.desktop.session idle-delay 0 || true
  gsettings set org.gnome.desktop.screensaver lock-enabled false || true
  gsettings set org.gnome.desktop.screensaver idle-activation-enabled false || true
  gsettings set org.gnome.desktop.lockdown disable-lock-screen true || true
  pkill -f gnome-initial-setup || true
fi

# Tools for smoke RPA
if command -v apt-get >/dev/null 2>&1; then
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    xdotool wmctrl scrot libfuse2t64 curl git 2>/dev/null \
    || $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      xdotool wmctrl scrot libfuse2 curl git || true
fi

sleep 5
echo "[rpa-linux-desktop] sessions:"
loginctl list-sessions || true
echo "[rpa-linux-desktop] DONE — VNC http://10.0.0.10:5701/"
