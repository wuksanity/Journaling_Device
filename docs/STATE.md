# journal-device — state of the build

Context document for picking up work on this project. Covers the hardware, what
is configured on the device, what works, what is broken, and what is planned.

Last updated: 31 August 2026

## What this is

A portable distraction-free writing device. A USB keyboard connects to a
Raspberry Pi Zero 2 W, which runs a Python curses journaling app. The intent is
to carry it, sit down anywhere, switch it on, and type — with entries saving to
the microSD card as dated Markdown files.

The device is headless. There is no screen attached yet. The display is
currently a phone or laptop connected over SSH, viewing the app through a
shared tmux session.

## Hardware

| Item | Detail |
|------|--------|
| Board | Raspberry Pi Zero 2 W, 512MB RAM |
| GPIO header | Not soldered. Bare through-holes. Nothing in the current design uses GPIO. |
| Storage | SanDisk Ultra 64GB microSDXC (Class 10 / UHS-I, no A1 marking), bought in person in Ho Chi Minh City |
| Keyboard | Evision RGB Keyboard, USB, VID:PID `320f:5084` |
| Keyboard connection | Micro-USB male → USB-A female OTG adapter into the inner USB port |
| Power | Wall supply into the outer PWR IN jack; power bank when portable |
| Display | None. Planned: 5" mini-HDMI IPS panel. Deferred on cost. |
| Spare part | 1.8" SPI OLED TFT, 65k colour — unused, needs the GPIO header. Intended as a future status display, not the main screen. |

### Hardware lessons learned the hard way

These cost several days and are worth not rediscovering.

- **The board must be powered from PWR IN, not from the data port.** Running it
  off USB data alone browns out at stage two of boot. This presented as a
  `bootcode.bin` reboot loop under `rpiboot`, and as "Device Descriptor Request
  Failed" in Windows Device Manager.
- The inner micro-USB port does carry power and can run the board from a
  computer, but not reliably under load.
- Two microSD cards failed before the current one: a Sony "120GB" (fake
  capacity — wrote fully but failed verification at 100%) and a SanDisk 32GB
  that died on first write.
- The original dual-headed USB card reader drops off the bus mid-write.
  Replaced with an Acer reader.
- `rpiboot` can expose the Pi's own SD slot as USB mass storage on Windows.
  This was the workaround while the reader was broken and is worth remembering.

## Operating system and device configuration

Raspberry Pi OS 64-bit, Debian trixie, kernel `6.18.39+rpt-rpi-v8`.

Note: this is the **Desktop** image, not Lite. That was unintentional and
caused a significant problem (see open problems). It has been neutered but not
replaced.

| Setting | Value |
|---------|-------|
| Hostname | `journal` |
| User | `walker` |
| IP on home network | `192.168.1.246` |
| SSH | enabled, password auth |
| Boot target | `multi-user.target` |
| lightdm | disabled |
| Console | `tty1` |
| Autologin | console autologin on `tty1` |

### Files that matter

**`/boot/firmware/cmdline.txt`** — single line, currently:

```
console=serial0,115200 console=tty1 root=PARTUUID=9857256e-02 rootfstype=ext4 fsck.repair=yes rootwait
```

`quiet`, `splash`, and `plymouth.ignore-serial-consoles` were deliberately
removed. **Do not add them back.**

**`~/.profile`** — launches the app on tty1 and auto-attaches over SSH:

```bash
if [ "$(tty)" = "/dev/tty1" ]; then
    tmux new-session -A -s journal "python3 $HOME/journal.py"
fi
if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ]; then
    tmux attach -t journal 2>/dev/null
fi
```

Note it is `.profile`, not `.bash_profile`. Raspberry Pi OS ships a `.profile`,
and bash ignores `.bash_profile` when one exists. This wasted significant time.

Without `exec` in front of `tmux`, killing the session leaves an interactive
shell on tty1 rather than relaunching the app — so `deploy.ps1`, which kills the
session, leaves the app stopped rather than restarting it. A replacement that
ends the login shell instead (letting autologin respawn it) is in
`device/profile`, with a five-second escape hatch so a crash-on-startup cannot
turn tty1 into a respawn loop. Not yet installed.

**`/etc/sudoers.d/010_poweroff`** — lets the app shut down without a password:

```
walker ALL=(ALL) NOPASSWD: /sbin/poweroff
```

**`~/journal.py`** — the app. Source is in this repo.

**`~/journal/YYYY-MM-DD.md`** — entries.

**`~/.journal-config.json`** — persisted settings.

## Networking

Two profiles under NetworkManager:

- Home WiFi (2.4GHz — the board has no 5GHz radio)
- `journal-ap` — access point mode, `ipv4.method shared`, gateway `10.42.0.1`

A systemd service `wifi-fallback.service` runs a script at boot that sleeps 45
seconds and starts the AP if `wlan0` is not connected. **This does not work.**
See open problems — a replacement is written but not yet verified on hardware.

## Open problems

### 1. AP mode fails away from home — blocking

The whole portability premise depends on this and it has never worked outside.
Tested at a café: the access point either never came up or was not reachable.

**Root cause found, 31 August 2026: there are two connection profiles both
named `journal-ap`.**

| UUID | mode | ipv4 | what it is |
|------|------|------|------------|
| `a154f5df-…` | `infrastructure` | auto | a client trying to *join* a network called journal-ap |
| `077c4e8c-…` | `ap` | shared → 10.42.0.1 | the real access point |

`nmcli connection show journal-ap` resolves to the first. So `nmcli connection
up journal-ap` — exactly what `wifi-fallback.service` ran — activated a client
profile hunting for a network that does not exist, and the access point never
came up. Neither profile has ever successfully connected (`TIMESTAMP-REAL:
never` on both), which is consistent with this having been broken from the
start. Removal instructions are in `device/README.md`.

The other suspected causes still stand and are worth fixing regardless:

- The check `nmcli -t -f DEVICE,STATE device | grep -q "^wlan0:connected"`
  returns true for an association with no usable IP. A half-connected state
  would suppress the AP.
- 45 seconds may be too short — NetworkManager scans for known networks first.
- One-shot at boot with no retry, so a single failure is permanent until reboot.
- The phone may auto-join a nearby café network and drop `journal-ap`.
- `iw` is not installed, so the guard cannot count AP clients. It fails safe
  (assumes a client is attached) but will therefore never hand back to the house
  network until `iw` is installed.

**A replacement is written, in `device/journal-net-guard.sh` plus a systemd
timer. It has not been run on the device.** It differs from the original in four
ways:

- Checks for a routable IPv4 address rather than connection state, so a
  half-connected association no longer suppresses the AP.
- Runs every 120 seconds on a timer instead of once at boot, so a single failure
  is no longer permanent.
- Hands back to a real network when one appears, so arriving home does not
  require a reboot to leave AP mode.
- Refuses to disturb an AP that has a client attached, and leaves a fresh AP up
  for ten minutes regardless — the radio cannot scan while serving an AP, so
  checking for home wifi means dropping it briefly, and doing that every two
  minutes would mean a phone never got the chance to join.

The address-parsing logic has been exercised against captured `ip` output for
the connected, AP-mode, and no-address cases. Everything else needs the device.
The verification procedure is in `device/README.md`: switch the router off, wait
a little over two minutes, confirm `journal-ap` appears and `10.42.0.1` answers.

The keyboard recovery path is done — `journal.py` has a `Hotspot` menu item that
runs `nmcli connection up journal-ap`, for when the device is unreachable and
there is no terminal on it. It needs the sudoers entry in
`device/011_journal-hotspot`.

### 2. Running the Desktop image

`lightdm` was holding `tty7` as the active console, which meant keystrokes went
nowhere. Diagnosed via `cat /sys/class/tty/tty0/active` returning `tty7`. Fixed
with `systemctl set-default multi-user.target` and disabling lightdm.

The desktop packages are still installed, consuming RAM on a 512MB board and
slowing boot. Reflashing with Raspberry Pi OS Lite is the proper fix. Not
urgent, but worth doing.

### 3. No screen

Deferred on cost. Until then the phone-over-SSH arrangement is the display,
which is why problem 1 is blocking rather than cosmetic.

### 4. No local development loop — resolved

`journal.py` now honours `JOURNAL_DEV=1`, redirecting to `~/journal-dev/` and
making shutdown and the hotspot control no-ops. `windows-curses` in a venv
supplies the missing `_curses` on Windows. There is a `unittest` suite under
`tests/`. Ctrl keys and colour still need checking on the device, since curses
differs between a Windows terminal and the Pi console.

## Planned, not started

- **Sync to an Xteink X4 e-reader.** The X4 runs CrossPoint firmware with a
  WiFi upload server and WebDAV. Entries are already plain text, so a `curl`
  from the Pi could push the day's entry for reading later. The X4 is an
  ESP32-C3 and cannot act as a display — this is a read-later loop, not a
  screen.
- **Font selection.** Fonts belong to the terminal, not the app. On the physical
  console this means `setfont` with a face from `/usr/share/consolefonts`,
  invoked from `.profile`.
- **Status display on the 1.8" SPI OLED**, once the GPIO header is soldered. A
  separate process reading the journal file and drawing word count, date, and
  battery.
- **Enclosure and strap.** Deferred until the screen exists, since dimensions
  depend on it.
