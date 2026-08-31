#!/bin/bash
# journal-net-guard -- make sure there is always a way to reach the device.
#
# Replaces wifi-fallback.service. Three things were wrong with that one:
#
#   1. It tested `nmcli -t -f DEVICE,STATE device | grep -q "^wlan0:connected"`,
#      which is true for an association that never got a usable address. A
#      half-connected state therefore suppressed the access point entirely.
#   2. It ran once at boot, so a single failure was permanent until reboot.
#   3. It had no way back: once the AP was up, arriving home did not rejoin
#      the house network.
#
# This checks for a genuinely routable IPv4 address, runs on a timer, and hands
# back to a real network when one appears.
#
# NOT YET TESTED ON HARDWARE. See device/README.md for the verification steps.

set -u

# Be explicit rather than inheriting. Both `ip` and `iw` live in /usr/sbin,
# which is absent from a plain user's PATH -- running this by hand from a
# non-login shell would silently lose them, and `command -v iw` would report iw
# missing on a machine where it is installed. systemd and sudo both provide
# /usr/sbin, but do not rely on the caller.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

AP="journal-ap"
IFACE="wlan0"
AP_SUBNET='^10\.42\.0\.'
JOIN_WAIT=20                        # seconds to let NetworkManager find a network
HANDBACK_AFTER=600                  # leave a fresh AP alone for this long
AP_STAMP=/run/journal-net-ap-since   # tmpfs, so it clears on reboot

log() { logger -t journal-net -- "$*"; }

# An address outside the AP's own subnet. Association alone is not enough --
# that was the original bug.
routable_ip() {
    ip -4 -o addr show dev "$IFACE" scope global 2>/dev/null \
        | awk '{print $4}' | cut -d/ -f1 | grep -v "$AP_SUBNET" | head -n1
}

ap_active() {
    nmcli -t -f NAME connection show --active 2>/dev/null | grep -qxF "$AP"
}

# Stations associated with our AP. If iw is missing we cannot tell, so claim
# one client: never tear down an AP that someone might be writing over.
ap_client_count() {
    if ! command -v iw >/dev/null 2>&1; then
        echo 1
        return
    fi
    iw dev "$IFACE" station dump 2>/dev/null \
        | awk '/^Station/ { n++ } END { print n + 0 }'
}

ap_age() {
    if [ ! -f "$AP_STAMP" ]; then
        echo 0
        return
    fi
    echo $(( $(date +%s) - $(cat "$AP_STAMP" 2>/dev/null || echo 0) ))
}

# Resolve to a profile that is genuinely in AP mode.
#
# The device had two connection profiles both named journal-ap: the real
# access point, and an infrastructure (client) profile trying to *join* a
# network called journal-ap, which does not exist. `nmcli connection up
# journal-ap` resolved to the client one, so the access point never came up.
# That is almost certainly why AP mode never worked away from home.
#
# Deleting the duplicate fixes it, but resolving by mode means the same
# mistake cannot silently come back.
ap_profile() {
    local uuid mode
    for uuid in $(nmcli -t -f UUID,NAME connection show 2>/dev/null \
        | awk -F: -v want="$AP" '$2 == want { print $1 }'); do
        mode=$(nmcli -t -f 802-11-wireless.mode connection show "$uuid" 2>/dev/null \
            | cut -d: -f2)
        if [ "$mode" = "ap" ]; then
            printf '%s\n' "$uuid"
            return 0
        fi
    done
    return 1
}

start_ap() {
    local uuid
    uuid=$(ap_profile) || {
        log "no connection profile named $AP is in AP mode -- cannot start it"
        return 1
    }
    if nmcli connection up "$uuid" >/dev/null 2>&1; then
        date +%s > "$AP_STAMP"
        log "access point up ($uuid), reachable at 10.42.0.1"
        return 0
    fi
    log "failed to start access point $AP ($uuid)"
    return 1
}

# --- already on a real network ---------------------------------------------
if [ -n "$(routable_ip)" ]; then
    exit 0
fi

# --- the AP is serving: consider handing back ------------------------------
if ap_active; then
    [ -f "$AP_STAMP" ] || date +%s > "$AP_STAMP"

    if [ "$(ap_client_count)" -gt 0 ]; then
        exit 0                      # someone is connected, possibly mid-sentence
    fi

    # Don't flap. A just-started AP needs to stay up long enough for a phone to
    # find it and join; tearing it down every couple of minutes to go looking
    # for home wifi would mean it never gets connected to at all.
    if [ "$(ap_age)" -lt "$HANDBACK_AFTER" ]; then
        exit 0
    fi

    # The Pi's radio cannot scan while running an AP, so the only way to look
    # for a known network is to drop the AP briefly. Safe: nobody is on it.
    log "AP idle for $(ap_age)s, checking for a known network"
    nmcli connection down "$(ap_profile || echo "$AP")" >/dev/null 2>&1 || true
    rm -f "$AP_STAMP"
    sleep "$JOIN_WAIT"

    if [ -n "$(routable_ip)" ]; then
        log "rejoined a real network at $(routable_ip)"
        exit 0
    fi

    log "no known network in range, bringing the AP back"
    start_ap
    exit 0
fi

# --- no address and no AP --------------------------------------------------
# NetworkManager scans for known networks first, which takes longer than the
# old 45-second one-shot allowed for.
log "no routable address on $IFACE, waiting ${JOIN_WAIT}s"
sleep "$JOIN_WAIT"

if [ -n "$(routable_ip)" ]; then
    exit 0
fi

start_ap
