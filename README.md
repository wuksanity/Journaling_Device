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

`Hotspot` brings up the `journal-ap` access point from the keyboard. It exists
for the failure this device keeps hitting: joining a network that gives it no
usable route, leaving no way in over SSH and no terminal on the device either.
It needs the sudoers entry in [device/011_journal-hotspot](device/011_journal-hotspot).

## Settings

Adjustable in-app and persisted to JSON.

| Setting    | Range     | Notes                                 |
|------------|-----------|---------------------------------------|
| `theme`    | 5 presets | night, paper, amber, green, ocean     |
| `font`     | 9 sizes   | console font size, `12x6` to `32x16`  |
| `paper`    | 3 modes   | `off`, `lined`, `margin`              |
| `width`    | 30-100    | text column width in characters       |
| `anchor`   | 20-95     | cursor position down the screen, as % |
| `autosave` | 1-60      | seconds between fsyncs                |

### `paper` — ruled background

`lined` underlines the full width of the text column on every row, including the
empty rows below the cursor. Padding short lines out to the column width is what
makes it read as ruled paper rather than as underlined text. `margin` adds a
`│` rule down the left edge, like a notebook. Pairs best with the `paper` theme.

It costs no rows and cannot collide with the text, because the rule is a
character attribute rather than drawn characters.

Verified with `tools/pty_probe.py`: under `tmux-256color` and `screen` — the
terminfo the app actually gets inside tmux, both on the console and over SSH —
`lined` and `margin` emit the underline attribute and `off` does not.

One caveat if you ever run the app outside tmux on the physical console: the
`linux` terminfo sets `ncv#18`, marking underline as unusable alongside colour,
so ncurses drops it and the ruling silently disappears. Inside tmux this does
not arise.

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
