#!/bin/bash
# Verify that the app's view of the hotspot matches reality, in both states.
#
# Run detached -- raising the AP drops the network this would be watched over:
#
#     sudo systemd-run --unit=hscheck --collect /tmp/check_hotspot_state.sh
#     # then, once the device is back:
#     cat /tmp/hotspot-state-check.log
#
# Uses a short window so the device is only off the house network briefly.

set -u
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOG=/tmp/hotspot-state-check.log
exec >"$LOG" 2>&1

app_says() {
    sudo -u walker python3 - <<'PY'
import sys
sys.path.insert(0, "/home/walker")
import journal
print(journal.hotspot_active())
PY
}

echo "=== before: nothing raised ==="
echo "  helper says : $(journal-hotspot status)"
echo "  app says    : $(app_says)"

echo
echo "=== raising for 45s ==="
journal-hotspot up 45
sleep 12
echo "  helper says : $(journal-hotspot status)"
echo "  app says    : $(app_says)"
echo "  wlan0       : $(ip -4 -o addr show wlan0 scope global | awk '{print $4}')"

echo
echo "=== stopping early, which is what the menu item does ==="
journal-hotspot down
sleep 15
echo "  helper says : $(journal-hotspot status)"
echo "  app says    : $(app_says)"
echo "  wlan0       : $(ip -4 -o addr show wlan0 scope global | awk '{print $4}')"

echo
echo "=== done ==="
