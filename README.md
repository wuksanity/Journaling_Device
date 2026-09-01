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

### `led` — knowing where you are with no screen

The point of writing on this thing screenless is that there is nothing to look
at. But then nothing tells you which screen the app is on, and typing into a
menu does nothing. Two answers, both opt-in; the default is `off`.

**`blink`** drives the keyboard's **Scroll Lock LED**, chosen over the Pi's board
light because it is under your fingers rather than in your bag, and over Caps
and Num Lock because nothing else in the system uses Scroll Lock.

| Signal | Meaning |
|--------|---------|
| long slow pulse | writing |
| fast flutter | a menu is waiting — deliberately the most urgent rhythm, since typing here does nothing |
| even medium beat | browsing entries |
| short mark, long gap | reading an entry |
| quick pips | settings |
| single flash | an autosave just fsynced |

The kernel's `timer` LED trigger maintains the rhythm, so the app writes two
small sysfs files on screen changes only and never on the keystroke path.

**`color`** sets the keyboard's **RGB backlight** to a colour naming the screen:
green writing, blue menu, cyan browse, amber settings, violet reading. Read at a
glance, no counting.

**Colour is written only when you ask for it, with `^L`.** Every EVision lighting
mode carries `MODE_FLAG_AUTOMATIC_SAVE`, meaning the keyboard persists each
change to its own flash. Writing a colour on every screen change would put
thousands of writes a month through a part rated for tens of thousands total. So
in `color` mode there is no ambient signalling at all: one write per press, and
the colour then stands as the answer to the last question you asked rather than a
live readout. `tests/test_journal.py` has a `ColorModeWearsNothing` case that
holds this to it.

### The compass — `^L`

Press `^L` to ask where you are. In `blink` mode you get a countable number of
flashes — write 1, menu 2, browse 3, settings 4, reading 5 — with a pause either
side so you know where the count starts. Counting is unambiguous in a way that
judging a rhythm's tempo is not. In `color` mode the backlight simply turns the
colour of the current screen.

It is `^L` and not `l` while writing, because that is a writing surface and a
bare letter has to stay a letter. Plain `l` also works in the menus, where it is
free. `^L` conventionally means redraw, which the loop already does, so the
compass comes free with it.

### RGB protocol

The keyboard is an EVision/Huafenda unit on a rebranded Sonix MCU
(`320F:5084`), and its lighting is driven by 64-byte HID reports on vendor usage
page `0xFF1C`, the second HID interface. OpenRGB's `EVisionKeyboardController`
lists this exact product id, so rather than building OpenRGB — Qt and C++ on a
512MB board — [device/journal-rgb](device/journal-rgb) implements just the
packets needed, in standard-library Python. The format is documented in that
file's docstring.

The backlight is *not* addressable per key on this model through this path; the
mode colour applies to the whole keyboard, which is all a state indicator needs.

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
