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
| `width`    | 30-100    | text column width in characters       |
| `anchor`   | 20-95     | cursor position down the screen, as % |
| `autosave` | 1-60      | seconds between fsyncs                |

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
