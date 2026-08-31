# Device-side configuration

Files that live on the Pi rather than in the app. **None of this has been tested
on hardware yet** — it is written from the notes in [../docs/STATE.md](../docs/STATE.md)
and needs verifying on the device before it is trusted away from the house.

| File | Goes to | Purpose |
|------|---------|---------|
| `profile` | `~/.profile` | Launch the app on tty1; attach over SSH |
| `journal-net-guard.sh` | `/usr/local/bin/` | Keep the device reachable |
| `journal-net-guard.service` | `/etc/systemd/system/` | Runs the script |
| `journal-net-guard.timer` | `/etc/systemd/system/` | Every 120s, not once at boot |
| `011_journal-hotspot` | `/etc/sudoers.d/` | Lets the Hotspot menu item run nmcli |

## Install

Copy the files over:

```bash
scp device/journal-net-guard.sh walker@192.168.1.246:/tmp/
```

Then on the device:

```bash
sudo install -m 755 /tmp/journal-net-guard.sh /usr/local/bin/journal-net-guard.sh
```

Sudoers files must be installed with `visudo -c` checking them first, because a
malformed file breaks sudo entirely:

```bash
sudo install -m 440 -o root -g root /tmp/011_journal-hotspot /etc/sudoers.d/011_journal-hotspot && sudo visudo -c
```

Enable the timer and retire the old one-shot service:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now journal-net-guard.timer && sudo systemctl disable --now wifi-fallback.service
```

## Verify at home before relying on it

This is the test the state doc calls for, and it is the whole point of the
rework. Do it while you still have a wired way in.

1. Confirm the timer is scheduled:

```bash
systemctl list-timers journal-net-guard.timer
```

2. Switch the router off. Wait a little over two minutes.

3. The `journal-ap` network should appear. Join it from a phone and confirm the
   gateway answers:

```bash
ssh walker@10.42.0.1
```

4. Switch the router back on, disconnect the phone from `journal-ap`, and wait.
   With no clients attached and the AP older than `HANDBACK_AFTER` (10 minutes),
   the guard drops the AP, finds the house network, and rejoins it.

5. Read the decisions it made:

```bash
journalctl -t journal-net --since "30 min ago"
```

## Things to know about the guard script

- **It checks for a routable IPv4 address, not connection state.** The original
  check passed for an association with no usable address, which is the failure
  that left the device unreachable in a café.
- **It will not disturb an AP that has a client attached.** You could be
  mid-sentence over SSH. `iw dev wlan0 station dump` is the check; if `iw` is
  missing it assumes a client rather than risking the tear-down.
- **A fresh AP is left alone for 10 minutes.** The radio cannot scan while
  serving an AP, so handing back means dropping it briefly. Doing that every
  two minutes would mean a phone never got a chance to join at all.
- **`/run/journal-net-ap-since`** records when the AP came up. It is on tmpfs,
  so it clears on reboot.

## The `exec` question in `~/.profile`

`deploy.ps1` kills the tmux session to pick up new code. Whether the app then
comes back depends on this file.

The original `.profile` ran `tmux new-session` plainly. When the session dies
the shell simply falls through to an interactive prompt, so the app does *not*
relaunch — you get a bash prompt on tty1 instead.

Ending the login shell fixes that: console autologin respawns it, `.profile`
runs again, and the app starts. The version here does that, but with a
five-second window to grab a shell first — otherwise an app that crashes on
startup turns tty1 into a respawn loop you cannot break into from the console.

Test it over SSH before rebooting:

```bash
tmux kill-session -t journal
```

## Still outstanding

- Reflash with Raspberry Pi OS Lite. The Desktop image's packages are still
  installed and cost RAM on a 512MB board.
- `setfont` from `.profile` for a better console face.
