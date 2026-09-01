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

Ordered least to most risky. Stop at any step that misbehaves — each one is
independent of the ones after it.

Stage all of it on the device first, from the repo root:

```bash
scp device/journal-net-guard.sh device/journal-net-guard.service device/journal-net-guard.timer device/011_journal-hotspot device/profile walker@192.168.1.246:/tmp/
```

### 0. Delete the duplicate `journal-ap` profile — do this first

The device has (had) **two** connection profiles both named `journal-ap`:

| UUID | mode | ipv4 | what it is |
|------|------|------|------------|
| `a154f5df-…` | `infrastructure` | auto | a **client** trying to join a network called journal-ap |
| `077c4e8c-…` | `ap` | shared → 10.42.0.1 | the real access point |

`nmcli connection show journal-ap` resolves to the first one. So
`nmcli connection up journal-ap` — what `wifi-fallback.service` ran, and what
the app's Hotspot item runs — activated a client profile hunting for a network
that does not exist. The access point never came up. This is very likely the
real reason AP mode never worked away from home, independent of the timing and
connection-state problems.

Check what is there now:

```bash
nmcli -t -f NAME,UUID,TYPE connection show | grep journal-ap
```

If two are listed, confirm which is the client before removing anything:

```bash
nmcli -t -f connection.uuid,802-11-wireless.mode connection show journal-ap
```

Delete the one reporting `infrastructure`, by UUID, not by name:

```bash
sudo nmcli connection delete a154f5df-b243-4514-909b-00a27227afe4
```

Then confirm the name now resolves to the AP-mode profile:

```bash
nmcli -t -f connection.uuid,802-11-wireless.mode,ipv4.method connection show journal-ap
```

It must say `ap` and `shared`. The Hotspot menu item in `journal.py` depends on
this, because its sudoers rule pins the literal command `nmcli connection up
journal-ap` and so cannot address the profile by UUID. `journal-net-guard.sh`
resolves by mode and is safe either way.

### 1. The reachability guard

Additive — it does not change how you log in, so this is safe to do first.

The guard uses `iw` to count clients attached to the AP. Without it the guard
assumes a client is always present, so it never hands back to the house network
— safe, but it means arriving home needs a reboot. It ships with the OS image
here (`/usr/sbin/iw`), so this is normally a no-op; run it to be sure:

```bash
sudo apt install -y iw
```

Note that `/usr/sbin` is not on a plain user's PATH, so `command -v iw` from a
non-login shell reports it missing even when it is installed. Check with
`ls /usr/sbin/iw` instead.

```bash
sudo install -m 755 -o root -g root /tmp/journal-net-guard.sh /usr/local/bin/journal-net-guard.sh
```

```bash
sudo install -m 644 -o root -g root /tmp/journal-net-guard.service /tmp/journal-net-guard.timer /etc/systemd/system/
```

Run it once by hand before trusting it to a timer, and read what it decided:

```bash
sudo /usr/local/bin/journal-net-guard.sh; journalctl -t journal-net -n 20 --no-pager
```

Connected to the house network, it should exit silently having done nothing.

Then enable the timer and retire the old one-shot service:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now journal-net-guard.timer && sudo systemctl disable --now wifi-fallback.service
```

### 2. The sudoers entry for the Hotspot menu item

**Validate before installing.** A malformed file in `/etc/sudoers.d/` breaks
`sudo` for everything, including the `sudo` you would need to repair it. Check
the staged copy first, and only install if it passes:

```bash
sudo visudo -cf /tmp/011_journal-hotspot
```

It must print `parsed OK`. Confirm the nmcli path matches the file, since sudo
matches the full command string:

```bash
command -v nmcli
```

Then install, and verify sudo still works in the same breath:

```bash
sudo install -m 440 -o root -g root /tmp/011_journal-hotspot /etc/sudoers.d/011_journal-hotspot && sudo -l | grep nmcli
```

If `sudo` has broken, you are not locked out: `pkexec visudo` or a root shell
over SSH can remove the file.

### 3. `~/.profile`

Riskiest, because it changes what happens when tty1 logs in. Keep a copy:

```bash
cp ~/.profile ~/.profile.backup && install -m 644 /tmp/profile ~/.profile
```

**Replacing this file does not affect the shell already running on tty1.** That
shell read the old copy at login and is still sitting inside it; killing the
tmux session just drops it to an interactive prompt, as before. The new file
takes effect on the next tty1 login, so the relaunch behaviour cannot be tested
until after a reboot. Reboot first:

```bash
sudo reboot
```

Once it comes back, confirm the app started from the new file:

```bash
ssh walker@192.168.1.246 tmux ls
```

*Then* test the relaunch, which is the part that makes `deploy.ps1` work:

```bash
ssh walker@192.168.1.246 tmux kill-session -t journal
```

Wait about eight seconds — five for the escape-hatch prompt to time out, a
couple more for autologin — and check that a *new* session exists, with a
`created` timestamp only seconds old:

```bash
ssh walker@192.168.1.246 tmux ls
```

If no session comes back, restore the backup and reboot again:

```bash
cp ~/.profile.backup ~/.profile && sudo reboot
```

If tty1 ends up in a respawn loop, press Enter on the physical keyboard within
the five-second window to get a shell.

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

## Boot does not wait for the network

`NetworkManager-wait-online.service` is disabled:

```bash
sudo systemctl disable NetworkManager-wait-online.service
```

Its `ExecStart` is `nm-online -s -q` with no `--timeout`, which defaults to 30
seconds. On the home network that cost about 1.5s, but away from any known
network it would wait out the full 30 before `multi-user.target` — delaying the
app by half a minute every time the device is used somewhere new, which is the
main thing it is for.

Nothing here needs a network at boot. `journal.py` contains no socket, HTTP or
URL code at all; the network exists only so a phone can attach over SSH as a
display. Boot is about 34 seconds with this disabled.

`cloud-init-network.service` is still enabled and may add some delay off-network.
It has not been measured away from wifi; if a café boot feels slow, that is the
next thing to look at, and cloud-init has already done its job on this install.

## Still outstanding

- Reflash with Raspberry Pi OS Lite. The Desktop image's packages are still
  installed and cost RAM on a 512MB board.
- `setfont` from `.profile` for a better console face.
