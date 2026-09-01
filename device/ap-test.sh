#!/bin/bash
# Bring the access point up for a bounded window, then put the house network
# back -- automatically, whether or not anyone is still connected to say so.
#
#     sudo systemd-run --unit=ap-test /usr/local/bin/ap-test.sh 600
#
# Run it detached like that, because raising the AP kills the wifi connection
# you started it from. systemd keeps it alive after your SSH session dies.
#
# The timeout is the point. Every previous attempt at this left the device with
# no way back if the AP did not work; here the worst case is that you wait out
# the window and the house network returns by itself.
#
# Watch what it did afterwards with:
#
#     journalctl -t journal-ap-test --no-pager

set -u

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

AP="journal-ap"
IFACE="wlan0"
WINDOW="${1:-600}"          # seconds to hold the AP up, default 10 minutes

log() { logger -t journal-ap-test -- "$*"; echo "$*"; }

# Resolve to a profile genuinely in AP mode. The device once had two profiles
# with this name and the bare name resolved to a client profile, so the access
# point never came up.
ap_uuid() {
    local uuid mode
    for uuid in $(nmcli -t -f UUID,NAME connection show 2>/dev/null \
        | awk -F: -v want="$AP" '$2 == want { print $1 }'); do
        mode=$(nmcli -t -f 802-11-wireless.mode connection show "$uuid" 2>/dev/null \
            | cut -d: -f2)
        [ "$mode" = "ap" ] && { printf '%s\n' "$uuid"; return 0; }
    done
    return 1
}

restore() {
    log "window over, restoring the house network"
    nmcli connection down "$AP" >/dev/null 2>&1 || true
    nmcli device disconnect "$IFACE" >/dev/null 2>&1 || true
    sleep 2
    nmcli device connect "$IFACE" >/dev/null 2>&1 || true
    for i in $(seq 1 30); do
        ip=$(ip -4 -o addr show dev "$IFACE" scope global 2>/dev/null \
            | awk '{print $4}' | cut -d/ -f1 | grep -v '^10\.42\.0\.' | head -n1)
        if [ -n "$ip" ]; then
            log "back on the house network at $ip"
            return 0
        fi
        sleep 2
    done
    log "WARNING: did not regain a routable address within 60s"
    return 1
}

# Restore on any exit path, including being killed.
trap restore EXIT INT TERM

uuid=$(ap_uuid) || { log "no $AP profile in AP mode -- nothing to test"; exit 1; }
log "raising $AP ($uuid) for ${WINDOW}s"

if ! nmcli connection up "$uuid" >/dev/null 2>&1; then
    log "FAILED to raise the access point"
    exit 1
fi

sleep 3
apip=$(ip -4 -o addr show dev "$IFACE" scope global 2>/dev/null \
    | awk '{print $4}' | cut -d/ -f1 | head -n1)
log "access point up, address $apip -- join '$AP' and ssh walker@10.42.0.1"

# Report who joins, so the log shows whether the phone actually associated.
end=$(( $(date +%s) + WINDOW ))
last=-1
while [ "$(date +%s)" -lt "$end" ]; do
    n=$(iw dev "$IFACE" station dump 2>/dev/null | awk '/^Station/ { c++ } END { print c + 0 }')
    if [ "$n" != "$last" ]; then
        log "clients associated: $n"
        last="$n"
    fi
    sleep 5
done
