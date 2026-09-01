#!/bin/bash
# Provision a freshly flashed Raspberry Pi OS Lite install for the journal
# device. Run it on the Pi, once, after first boot:
#
#     scp device/setup-fresh.sh walker@journal.local:/tmp/
#     ssh -t walker@journal.local "bash /tmp/setup-fresh.sh"
#
# Assumes Imager already set the hostname, user, wifi and SSH key, so the
# device is reachable when this runs.
#
# It deliberately does NOT install journal-net-guard. That service is what took
# the device off the network and left it unreachable; it goes back only after
# the access point has been proven to work by hand.

set -euo pipefail

AP_PSK="${AP_PSK:-journal12345}"     # override: AP_PSK=... bash setup-fresh.sh

say() { printf '\n=== %s ===\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
    echo "run as walker, not root -- it uses sudo where needed" >&2
    exit 1
fi

say "packages"
# Lite does not ship tmux, and the app is launched inside it. python3 and
# NetworkManager are already present.
sudo apt-get update -qq
sudo apt-get install -y -qq tmux iw

say "console boot target"
# If this is the Desktop image rather than Lite, lightdm holds tty7 as the
# active console and keystrokes typed on tty1 go nowhere -- diagnosed once
# already via `cat /sys/class/tty/tty0/active` returning tty7. Both commands
# are harmless no-ops on Lite.
if systemctl is-enabled lightdm >/dev/null 2>&1; then
    sudo systemctl disable lightdm
    echo "  disabled lightdm"
fi
sudo systemctl set-default multi-user.target
echo "  default target: $(systemctl get-default)"

say "console autologin on tty1"
# Without this nothing logs in on tty1, so .profile never runs and the app
# never starts. B2 = console autologin.
sudo raspi-config nonint do_boot_behaviour B2

say "passwordless poweroff"
# The app's ^D and Shut down both call this; it must not prompt.
printf '%s ALL=(ALL) NOPASSWD: /sbin/poweroff\n' "$USER" | \
    sudo tee /etc/sudoers.d/010_poweroff >/dev/null
sudo chmod 440 /etc/sudoers.d/010_poweroff
sudo visudo -c >/dev/null && echo "sudoers OK"

say "access point profile"
# Create it fresh with a known password. The old install had two profiles both
# named journal-ap -- one a client profile trying to join a network that did
# not exist -- and `nmcli connection up journal-ap` resolved to the wrong one,
# so the AP never came up. Delete any leftovers first.
while IFS=: read -r name uuid; do
    [ "$name" = "journal-ap" ] || continue
    echo "  removing existing journal-ap $uuid"
    sudo nmcli connection delete "$uuid" || true
done < <(nmcli -t -f NAME,UUID connection show)

sudo nmcli connection add type wifi ifname wlan0 con-name journal-ap \
    autoconnect no ssid journal-ap \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.psk "$AP_PSK" >/dev/null
echo "  created journal-ap, psk: $AP_PSK"

# Prove there is now exactly one, and that it is in AP mode.
count=$(nmcli -t -f NAME connection show | grep -cx journal-ap || true)
mode=$(nmcli -t -f 802-11-wireless.mode connection show journal-ap | cut -d: -f2)
echo "  profiles named journal-ap: $count (must be 1)"
echo "  resolves to mode: $mode (must be ap)"
[ "$count" = "1" ] && [ "$mode" = "ap" ] || { echo "AP profile wrong, stopping" >&2; exit 1; }

say "persistent logs"
# So the next failure leaves evidence behind instead of vanishing on reboot.
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true

say "app launcher"
if [ -f /tmp/profile ]; then
    cp ~/.profile ~/.profile.backup 2>/dev/null || true
    install -m 644 /tmp/profile ~/.profile
    echo "  installed ~/.profile from /tmp/profile"
else
    echo "  /tmp/profile not staged -- copy device/profile over and install it"
fi

say "done"
cat <<'NOTES'
Next:
  1. Copy the app over:      ./deploy.ps1
  2. Reboot to start it:     sudo reboot
  3. Test the AP by hand before installing journal-net-guard:
         sudo nmcli connection up journal-ap
     then join it from a phone and confirm ssh walker@10.42.0.1 works.
     Bring the house network back with:
         sudo nmcli connection down journal-ap
     Do that while you have physical access, not over SSH -- it drops the
     connection you are using.
  4. Only once that round trip works, install the timer from device/README.md.
NOTES
