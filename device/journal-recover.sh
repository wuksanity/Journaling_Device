#!/bin/bash
# One-shot recovery and diagnostics for the journal device.
#
# Runs at boot from systemd.run= in cmdline.txt, as root, in a minimal boot
# (kernel-command-line.target). Everything it finds goes to
# /boot/firmware/journal-recover.log -- that partition is FAT, so it can be read
# from a Windows laptop. That is the whole point: the logs we need are on an
# ext4 partition Windows cannot mount, and the device is unreachable over the
# network.
#
# It strips its own hook out of cmdline.txt before exiting, so a successful run
# cannot turn into a reboot loop.

BOOT=/boot/firmware
LOG=$BOOT/journal-recover.log

mount -o remount,rw / 2>/dev/null
mount -o remount,rw "$BOOT" 2>/dev/null

{
    echo "=== journal-recover, $(date -u 2>/dev/null) ==="

    echo
    echo "### rfkill -- a soft-blocked radio would explain no wifi at all ###"
    for d in /sys/class/rfkill/*/; do
        [ -d "$d" ] || continue
        printf '  %s: soft=%s hard=%s\n' \
            "$(cat "$d/name" 2>/dev/null)" \
            "$(cat "$d/soft" 2>/dev/null)" \
            "$(cat "$d/hard" 2>/dev/null)"
    done
    rfkill list 2>&1 | sed 's/^/  /'

    echo
    echo "### wlan0 present at all? ###"
    ip link show wlan0 2>&1 | sed 's/^/  /'
    ls -l /sys/class/net/ 2>&1 | sed 's/^/  /'
    echo "  brcmfmac firmware messages:"
    dmesg 2>/dev/null | grep -iE "brcmfmac|wlan|firmware" | tail -25 | sed 's/^/    /'

    echo
    echo "### connection profiles on disk (psk redacted) ###"
    ls -l /etc/NetworkManager/system-connections/ 2>&1 | sed 's/^/  /'
    for f in /etc/NetworkManager/system-connections/*.nmconnection; do
        [ -f "$f" ] || continue
        echo "  ---- $f ----"
        grep -v "^psk=" "$f" 2>/dev/null | sed 's/^/    /'
    done

    echo
    echo "### was the guard timer active? ###"
    ls -l /etc/systemd/system/timers.target.wants/ 2>&1 | sed 's/^/  /'
    ls -l /etc/systemd/system/multi-user.target.wants/wifi-fallback.service 2>&1 | sed 's/^/  /'

    echo
    echo "### previous boot, from rsyslog (journald here is usually volatile) ###"
    for src in /var/log/syslog /var/log/daemon.log /var/log/messages; do
        [ -f "$src" ] || continue
        echo "  ---- $src : NetworkManager / wpa_supplicant / journal-net ----"
        grep -iE "NetworkManager|wpa_supplicant|journal-net|dhcp" "$src" 2>/dev/null \
            | tail -60 | sed 's/^/    /'
    done

    echo
    echo "### journald, in case it is persistent after all ###"
    journalctl -b -1 --no-pager -n 60 2>&1 | tail -40 | sed 's/^/  /'

    echo
    echo "=== FIXES ==="

    # 1. Remove the net guard. It is what took the device off the network, and
    #    it must not run again until it has been re-tested with a known-good AP.
    rm -fv /etc/systemd/system/timers.target.wants/journal-net-guard.timer 2>&1 | sed 's/^/  /'
    rm -fv /etc/systemd/system/multi-user.target.wants/wifi-fallback.service 2>&1 | sed 's/^/  /'

    # 2. Set a known PSK on the access point. Being locked out of the recovery
    #    path is what made this unrecoverable in the first place.
    AP=/etc/NetworkManager/system-connections/journal-ap.nmconnection
    if [ -f "$AP" ]; then
        if grep -q "^psk=" "$AP"; then
            sed -i 's|^psk=.*|psk=journal12345|' "$AP"
            echo "  journal-ap psk set to journal12345"
        else
            echo "  no psk= line in $AP -- adding one"
            printf '\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=journal12345\n' >> "$AP"
        fi
        chmod 600 "$AP"
        chown root:root "$AP"
    else
        echo "  $AP missing"
    fi

    # 3. Make sure the house network is allowed to autoconnect.
    for f in /etc/NetworkManager/system-connections/*.nmconnection; do
        [ -f "$f" ] || continue
        case "$f" in *journal-ap*) continue ;; esac
        if grep -q "^autoconnect=false" "$f"; then
            sed -i 's|^autoconnect=false|autoconnect=true|' "$f"
            echo "  re-enabled autoconnect on $f"
        fi
    done

    # 4. Clear any soft rfkill block.
    for s in /sys/class/rfkill/*/soft; do
        [ -f "$s" ] || continue
        if [ "$(cat "$s" 2>/dev/null)" = "1" ]; then
            echo 0 > "$s" 2>/dev/null && echo "  cleared soft block on $s"
        fi
    done

    # 5. Make journald persistent so the next failure leaves evidence behind.
    mkdir -p /var/log/journal 2>/dev/null && echo "  enabled persistent journald"

    echo
    echo "=== done ==="
} >"$LOG" 2>&1

# Strip the hook so the next boot is a normal one. Without this, the reboot
# triggered by systemd.run_success_action would run this script again, forever.
sed -i 's| systemd\.run=[^ ]*||g; s| systemd\.run_success_action=[^ ]*||g; s| systemd\.unit=[^ ]*||g; s| systemd\.mask=[^ ]*||g' \
    "$BOOT/cmdline.txt"

sync
exit 0
