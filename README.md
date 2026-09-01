# journal-device

Distraction-free writing device. A USB keyboard connects to a Raspberry Pi
Zero 2 W, which runs this Python curses app on `tty1` inside a tmux session.
Entries save as dated Markdown files.

The device is currently headless — the display is a phone or laptop attached
over SSH to the same tmux session. See [docs/STATE.md](docs/STATE.md) for the
hardware, the device configuration, and the open problems, and
[device/](device/) for the files that live on the Pi rather than in the app.

## Run locally

    JOURNAL_DEV=1 python journal.py

`JOURNAL_DEV=1` redirects to `~/journal-dev/` and `~/.journal-config-dev.json`,
and turns shutdown and the hotspot control into no-ops, so every code path is
safe to exercise.

Windows Python has no `_curses` in the standard library, so local runs need the
shim. The app itself stays dependency-free — this is a development-only tool:

    python -m venv .venv
    .venv\Scripts\python.exe -m pip install windows-curses

Then run with `.venv\Scripts\python.exe`. On the Pi, `curses` is already there
and no venv is needed.

curses behaves differently between a Windows terminal and the Pi console,
particularly for `Ctrl` keys and colour. Verify anything touching those on the
device. Windows Terminal or WSL is closer to the target than `powershell.exe`.

## Tests

    .venv\Scripts\python.exe -m unittest discover -s tests -v

Standard library `unittest`, no test dependencies. The curses screens are driven
through a fake `stdscr` that records what was drawn, so the entry-list and
reader viewports are tested against real behaviour rather than by
re-implementing their arithmetic in the test.

## Deploy

    ./deploy.ps1

Copies `journal.py` to the Pi and kills the running tmux session. It refuses to
deploy a file with CRLF line endings, since the Pi runs it directly. Whether the
app relaunches by itself depends on `~/.profile`; see [device/README.md](device/README.md).

## On the device

Launched from `~/.profile` when the login shell is on `/dev/tty1`, inside a tmux
session named `journal`. Attaching over SSH gives a second client on the same
session, so a phone acts as the display.

- Entries: `~/journal/YYYY-MM-DD.md`
- Config: `~/.journal-config.json`

## Keys

| Key  | Action                |
|------|-----------------------|
| `^X` | back to menu          |
| `^D` | save and power off    |
| `^W` | delete last word      |
| `^U` | clear current line    |

Arrow keys navigate menus and scroll entries. In the entry list and the reader,
`PgUp`/`PgDn`/`Home`/`End` also work.

## Menu

`Write`, `Browse entries`, `Settings`, `Hotspot`, `Shut down`.

`Hotspot` raises the `journal-ap` access point from the keyboard. It exists for
the failure this device keeps hitting: joining a network that gives it no usable
route, leaving no way in over SSH and no terminal on the device either.

**It is bounded.** The AP is held for 15 minutes and then the house network comes
back by itself, whether or not anyone connected. Pressing it cannot strand the
device — which the first version could, because it ran `nmcli connection up`
directly with no path back, and with a subprocess timeout shorter than nmcli's
own, so it killed the activation halfway and left neither network up.

You press this blind: raising the AP drops the connection you would have read the
screen over. So the LED confirms it — **six flashes** means the hotspot is up,
**one long flash** means it failed. `^L` repeats the answer.

Needs [device/journal-hotspot](device/journal-hotspot), which runs
[device/ap-test.sh](device/ap-test.sh) detached via `systemd-run`, and the
sudoers entry in [device/014_journal-hotspot](device/014_journal-hotspot).

## Settings

Adjustable in-app and persisted to JSON.

| Setting    | Range     | Notes                                 |
|------------|-----------|---------------------------------------|
| `theme`    | 5 presets | night, paper, amber, green, ocean     |
| `font`     | 9 sizes   | console font size, `12x6` to `32x16`  |
| `paper`    | 3 modes   | `off`, `ruled`, `margin`              |
| `width`    | 30-100    | text column width in characters       |
| `anchor`   | 20-95     | cursor position down the screen, as % |
| `autosave` | 1-60      | seconds between fsyncs                |

### `paper` — ruled background

`ruled` draws real lines: text on every other row with a ruled line directly
beneath it, so the page has the spacing of a notebook. `margin` adds a vertical
mark down the left edge. Both pair best with the `paper` theme.

The rule is **characters in their own colour**, not an underline attribute. An
underline takes the text colour, which reads as emphasis — underlined text
rather than paper. A separate row in notebook blue reads as a ruled sheet.

With 256 colours available the palette does the rest:

| Theme | Ground | Ink | Rule |
|-------|--------|-----|------|
| `paper` | warm cream (230) | graphite (236) | notebook blue (110) |
| `night` | near-black (234) | soft white (252) | blue-grey (60) |

Terminals reporting only 8 colours fall back to the basic palette, borrowing
blue for the rule. `tmux` advertises `tmux-256color` to the app, so on this
device the full palette applies; a bare console outside tmux reports 8.

**Ruling halves how many lines fit on screen**, since every other row is a line.
That is the honest cost of looking like paper, and little loss on a device where
you only ever write at the bottom. `line_capacity()` is the single place that
knows this, and the writing and reading screens both slice their text to it.

Verified end to end with `tools/pty_render.py`, which runs the app in a real pty
and replays the escape output into a character grid, so the layout can be
inspected rather than assumed.

### `font` — console font size

Fonts belong to the terminal, not the app. On the physical console that means
`setfont`, which this setting calls with `Uni2-Terminus<size>`; over SSH the font
is whatever your terminal app is set to, so the setting has no effect there.

**It needs a display attached to do anything.** With nothing on the HDMI port
there is no framebuffer console — no `/dev/fb0`, `vc4-drm` reports "Cannot find
any crtc or sizes" — and `setfont` fails with *"Unable to load such font with
such kernel version"*. The setting is stored and applied on the next start, so
it will take effect once a screen exists. `hdmi_force_hotplug=1` in
`config.txt` would create a framebuffer without a monitor attached, at some
power cost.

Requires the sudoers entry in [device/011_journal-console](device/011_journal-console),
because `setfont` writes to root-owned `/dev/tty0`.

## `^L` — knowing where you are with no screen

The point of writing on this thing screenless is that there is nothing to look
at. But then nothing tells you which screen the app is on, and typing into a
menu does nothing.

Press **`^L`** and it counts out where you are on the keyboard's **Scroll Lock
LED** — chosen over the Pi's board light because it is under your fingers rather
than in your bag, and over Caps and Num Lock because nothing else in the system
uses Scroll Lock.

**This is not a setting.** It is always available and there is nothing to switch
on. An indicator you can accidentally leave off is worse than no indicator, and
it having been a setting that defaulted to `off` silently broke a real
away-from-home test.

| Signal | Meaning |
|--------|---------|
| 1 flash | writing |
| 2 flashes | menu |
| 3 flashes | browsing entries |
| 4 flashes | settings |
| 5 flashes | reading an entry |
| 6 flashes | hotspot is up — join `journal-ap` |
| one long flash | something failed; deliberately not a count, so it cannot be miscounted |

There is a clear pause either side of the count so you know where it starts and
ends. The two screens you are in most often are the fewest to count.

**Nothing signals on its own.** No rhythm while you write, no flash on save. The
LED is dark and handed back to its normal Scroll Lock function except for the
two seconds after you ask — counting is unambiguous where judging a tempo is
not, and an indicator that blinks at you unprompted is the opposite of what this
device is for. `LedSignalling` in the test suite asserts that a whole session of
writing, autosaving, browsing and reading produces no LED activity at all
without a keypress — and that a config written by an earlier version, carrying a
stale `led` key, cannot silence it.

One implementation note, since it cost a debugging round: brightness must be
written *before* restoring the trigger. Writing brightness while a trigger owns
the LED clears that trigger straight back to `none`, silently undoing the
handback.

Driving the keyboard's **RGB backlight** by colour instead was attempted and
abandoned. The keyboard acknowledges every packet and echoes the payload back but
ignores the mode change, staying in whatever effect it was already running.
[device/journal-rgb](device/journal-rgb) keeps the protocol notes and lists what
was ruled out — wrong interface, wrong framing, checksum signedness — so a future
attempt starts from there rather than from scratch. It is not installed and not
wired into the app.

Worth carrying forward if anyone revisits it: every EVision lighting mode carries
`MODE_FLAG_AUTOMATIC_SAVE`, so the keyboard writes each change to its own flash.
Any integration has to be on demand only — a colour set on every screen change
would put thousands of writes a month through a part rated for tens of thousands
total.

### Why `^L` and not `l`

Writing mode is a writing surface, so a bare letter has to stay a letter — `l`
there must type an `l`. Plain `l` does work in the menus, where it is free.
`^L` conventionally means redraw, which the loop already does, so the compass
comes free with it.

## Design notes

- **Append-only buffer.** There is no cursor movement into earlier text. This is
  deliberate for a journaling device, and the buffer model assumes writing
  always happens at the end.
- **Wrapped lines are cached per paragraph**, so only the paragraph being typed
  is recomputed. Typing speed stays constant regardless of document length.
- **`wrap_para` preserves trailing spaces.** `textwrap.wrap` strips them, which
  made the cursor fail to advance until the character after a space was typed.
- **Enter writes without `fsync`**; the `fsync` happens on the autosave timer,
  so pressing Enter never stalls on SD card I/O.
- **Saves go through a temp file and a rename.** Writing over the entry directly
  truncates it first, so losing power mid-write could empty the day's entry. A
  crash now costs at most the last few keystrokes.
- **`read_key` swallows escape sequences.** Terminals answer colour queries by
  writing escape strings back through stdin. Without this filter they land in
  the document as garbage.
- **Redraw on state change, not on a timer.**

## Known limitations

- No editing of earlier text (by design, see above).
- Font is a property of the terminal, not the app. On the physical console use
  `setfont` with a face from `/usr/share/consolefonts`; over SSH it is whatever
  the terminal app is set to.
- The access-point fallback in [device/](device/) is rewritten but **not yet
  verified on hardware**. Until it is, the device is only reliable within reach
  of a known network.
