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

start_ap() {
    if nmcli connection up "$AP" >/dev/null 2>&1; then
        date +%s > "$AP_STAMP"
        log "access point up, reachable at 10.42.0.1"
        return 0
    fi
    log "failed to start access point $AP"
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
    nmcli connection down "$AP" >/dev/null 2>&1 || true
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
