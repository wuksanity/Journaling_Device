#!/usr/bin/env python3
import curses, os, time, json, locale, datetime, textwrap, subprocess

locale.setlocale(locale.LC_ALL, "")

# JOURNAL_DEV=1 redirects to a sandbox and makes shutdown a no-op, so every
# code path can be exercised on a laptop without touching real entries.
DEV = os.environ.get("JOURNAL_DEV") == "1"
JOURNAL_DIR = os.path.expanduser("~/journal-dev" if DEV else "~/journal")
CONFIG_PATH = os.path.expanduser(
    "~/.journal-config-dev.json" if DEV else "~/.journal-config.json")

THEMES = ["night", "paper", "amber", "green", "ocean"]
# 256-colour equivalents, used when the terminal has them. tmux advertises
# tmux-256color to the app, so on this device it does; a bare console or
# TERM=screen reports 8 and falls back to THEME_COLORS below.
#
# For the paper theme these are the difference between something that looks
# like a sheet of ruled paper and something that looks like underlined text:
# warm off-white ground, soft graphite ink, and a rule in notebook blue rather
# than in the text colour.
THEME_COLORS_256 = {
    "paper": (236, 230),        # graphite on warm cream
    "night": (252, 234),        # soft white on near-black
    "amber": (215, 233),        # amber on near-black
    "green": (114, 233),        # muted green on near-black
    "ocean": (152, 24),         # pale cyan on deep blue
}

# (rule, margin) per theme. The rule is the ruled line; the margin is the
# vertical mark down the left edge.
PAPER_COLORS_256 = {
    "paper": (110, 174),        # notebook blue, dusty rose
    "night": (60, 96),
    "amber": (94, 130),
    "green": (65, 100),
    "ocean": (67, 97),
}

THEME_COLORS = {
    "paper": (curses.COLOR_BLACK, curses.COLOR_WHITE),
    "night": (curses.COLOR_WHITE, curses.COLOR_BLACK),
    "amber": (curses.COLOR_YELLOW, curses.COLOR_BLACK),
    "green": (curses.COLOR_GREEN, curses.COLOR_BLACK),
    "ocean": (curses.COLOR_CYAN, curses.COLOR_BLUE),
}
# Console font sizes, smallest to largest. Fonts belong to the terminal, not
# the app: on the physical console that means setfont, and over SSH it is
# whatever the terminal app is set to, so this only bites on tty1.
FONTS = ["default", "12x6", "14", "16", "18x10", "20x10",
         "22x11", "24x12", "28x14", "32x16"]
FONT_FAMILY = "Uni2-Terminus%s"     # Uni2 covers the widest charset of the set

# Ruled-paper backgrounds. "lined" underlines the full text column on every row,
# including the empty ones below the cursor, which is what makes the page read
# as ruled rather than just underlined text. "margin" adds a notebook rule down
# the left edge.
# "ruled" draws real lines: text on every other row with a ruled line beneath
# it, so the page has the spacing of a notebook rather than an underline under
# every row. It halves how many lines fit on screen, which is the honest cost of
# looking like paper -- and no great loss on a device where you only ever write
# at the bottom. "margin" adds the vertical mark down the left edge.
PAPERS = ["off", "ruled", "margin"]
RULE_CHAR = "\u2500"           # box drawings light horizontal
MARGIN_CHAR = "\u2502"         # box drawings light vertical

# How the app tells you which screen it is on with no display attached.
#
#   blink  the keyboard's Scroll Lock LED: an ambient rhythm per screen, a
#          flash on each autosave, and a countable compass on demand. Costs
#          nothing but two sysfs writes on screen changes.
#   color  the keyboard's RGB backlight, set ONLY when you ask for it with ^L.
#          Every EVision lighting mode carries AUTOMATIC_SAVE, meaning the
#          keyboard persists it to its own flash on every write -- so writing a
#          colour on every screen change would wear that memory out. On demand
#          means one write per press, and the colour then stands as the answer
#          to the last question you asked, not a live readout.
LEDS = ["off", "blink", "color"]
LED_HELPER = "/usr/local/bin/journal-led"
RGB_HELPER = "/usr/local/bin/journal-rgb"

DEFAULTS = {"theme": "night", "font": "default", "paper": "off",
            "led": "off", "width": 58, "anchor": 62, "autosave": 5}

AP_NAME = "journal-ap"
AP_GATEWAY = "10.42.0.1"

# Settings whose value has to be one of a fixed list. A config written by an
# older version can name a mode that no longer exists, and the settings screen
# looks the value up by index, so an unknown one would raise there rather than
# here.
ENUMS = {"theme": THEMES, "font": FONTS, "paper": PAPERS, "led": LEDS}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    for key, allowed in ENUMS.items():
        if cfg.get(key) not in allowed:
            cfg[key] = DEFAULTS[key]
    for key in ("width", "anchor", "autosave"):
        if not isinstance(cfg.get(key), int) or isinstance(cfg.get(key), bool):
            cfg[key] = DEFAULTS[key]
    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

def entries():
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    return sorted([f for f in os.listdir(JOURNAL_DIR) if f.endswith(".md")], reverse=True)

def today_path():
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    return os.path.join(JOURNAL_DIR, datetime.date.today().isoformat() + ".md")

def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def save_text(text, path, sync=True):
    """Write to a temp file and rename over the target. Opening the real path
    with "w" truncates it first, so losing power mid-write could leave the
    day's entry empty. os.replace is atomic, and ext4 flushes the data before
    committing a rename-over-existing, so a crash costs at most the last few
    keystrokes and never the file itself."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        if sync:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)

def wrap_para(para, col):
    if para == "":
        return [""]
    trailing = len(para) - len(para.rstrip(" "))
    body = textwrap.wrap(para, col, drop_whitespace=True) or [""]
    if trailing:
        if len(body[-1]) + trailing <= col:
            body[-1] += " " * trailing
        else:
            body.append(" " * trailing)
    return body

def read_key(stdscr, timeout=250):
    try:
        ch = stdscr.get_wch()
    except curses.error:
        return None
    if ch == "\x1b":
        stdscr.timeout(15)
        try:
            for _ in range(64):
                c = stdscr.get_wch()
                if isinstance(c, str) and (c.isalpha() or c in ("\x07", "\\", "~")):
                    break
        except curses.error:
            pass
        stdscr.timeout(timeout)
        return None
    return ch

def run_helper(helper, args):
    try:
        subprocess.run(["sudo", "-n", helper] + list(args),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5)
    except Exception:
        pass


def led(cfg, *args):
    """Ambient signalling on the Scroll Lock LED: a rhythm per screen, a flash
    on save, and handing the LED back on exit.

    Only in "blink" mode. In "color" mode nothing happens here on purpose --
    every write to the RGB backlight is persisted by the keyboard, so the
    backlight is touched only when explicitly asked for.

    The kernel's timer trigger maintains the rhythm, so this runs on screen
    changes only, never on the keystroke path."""
    if DEV or cfg.get("led", "off") != "blink":
        return
    run_helper(LED_HELPER, args)


def compass(cfg, where):
    """Answer "where am I", on demand, when there is no display.

    In blink mode: a countable number of flashes, unambiguous in a way that
    judging a rhythm's tempo is not. In color mode: the backlight turns the
    colour of the current screen -- read at a glance, no counting.

    This is the only thing that writes to the RGB backlight, and it writes once
    per press."""
    mode = cfg.get("led", "off")
    if DEV or mode == "off":
        return
    if mode == "color":
        run_helper(RGB_HELPER, [where])
    else:
        run_helper(LED_HELPER, ["compass", where])


def apply_font(cfg):
    """Set the console font. setfont acts on the current virtual console via
    /dev/tty0, which is root-owned, so this needs the sudoers entry in
    device/011_journal-console. Over SSH it changes the console nobody is
    looking at, which is harmless."""
    if DEV or cfg.get("font", "default") == "default":
        return
    try:
        subprocess.run(["sudo", "-n", "setfont", FONT_FAMILY % cfg["font"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10)
    except Exception:
        pass


def pair(n):
    """color_pair needs an initialised screen, and start_color can fail. Fall
    back to plain so drawing never depends on colour being available."""
    try:
        return curses.color_pair(n)
    except Exception:
        return 0


def is_ruled(cfg):
    return cfg.get("paper", "off") in ("ruled", "margin")


def line_capacity(cfg, rows):
    """How many lines of text fit in `rows` screen rows. Ruled pages spend
    every other row on a line, so callers must slice the text to this."""
    if is_ruled(cfg):
        return max(1, (rows + 1) // 2)
    return max(1, rows)


def draw_page(stdscr, cfg, view, top, left, col, rows):
    """Draw the page: text, and if ruled, a line beneath each row of it.

    The rule is drawn as characters in their own colour rather than as an
    underline attribute, because an underline takes the text colour and reads
    as emphasis. A separate row in notebook blue reads as paper."""
    ruled = is_ruled(cfg)
    margin = cfg.get("paper", "off") == "margin"
    rule_attr = pair(2) if ruled else 0
    margin_attr = pair(3) | curses.A_DIM

    def put(y, x, s, attr=0):
        if y < top or y >= top + rows:
            return
        try:
            stdscr.addstr(y, x, s, attr)
        except curses.error:
            pass

    if ruled:
        for i in range(line_capacity(cfg, rows)):
            y = top + i * 2
            put(y, left, (view[i] if i < len(view) else "")[:col])
            put(y + 1, left, RULE_CHAR * col, rule_attr)
    else:
        for i in range(rows):
            put(top + i, left, (view[i] if i < len(view) else "")[:col])

    if margin and left >= 2:
        for i in range(rows):
            put(top + i, left - 2, MARGIN_CHAR, margin_attr)


def apply_theme(stdscr, cfg):
    """Pair 1 is the page, 2 the ruled lines, 3 the margin mark. Drawing the
    rules in their own colour is what stops them reading as underlined text."""
    try:
        curses.start_color()
        theme = cfg["theme"] if cfg["theme"] in THEME_COLORS else "night"
        rich = getattr(curses, "COLORS", 8) >= 256

        if rich:
            fg, bg = THEME_COLORS_256.get(theme, THEME_COLORS_256["night"])
            rule, margin = PAPER_COLORS_256.get(theme,
                                                PAPER_COLORS_256["night"])
        else:
            fg, bg = THEME_COLORS[theme]
            # Only eight colours: the rule borrows blue, or falls back to the
            # text colour when blue is already the ground.
            rule = curses.COLOR_BLUE if bg != curses.COLOR_BLUE else fg
            margin = curses.COLOR_RED if bg != curses.COLOR_RED else fg

        curses.init_pair(1, fg, bg)
        curses.init_pair(2, rule, bg)
        curses.init_pair(3, margin, bg)
        stdscr.bkgd(" ", curses.color_pair(1))
    except Exception:
        pass

def centered(stdscr, y, s, attr=0):
    h, w = stdscr.getmaxyx()
    try:
        stdscr.addstr(y, max(0, (w - len(s)) // 2), s[:w - 1], attr)
    except curses.error:
        pass

def menu(stdscr, cfg):
    items = ["Write", "Browse entries", "Settings", "Hotspot", "Shut down"]
    sel = 0
    stdscr.timeout(-1)
    curses.curs_set(0)
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        top = max(1, h // 2 - 5)
        centered(stdscr, top, "journal", curses.A_BOLD)
        centered(stdscr, top + 1, datetime.date.today().strftime("%A, %d %B %Y"), curses.A_DIM)
        for i, item in enumerate(items):
            centered(stdscr, top + 4 + i, "  %s  " % item,
                     curses.A_REVERSE if i == sel else 0)
        centered(stdscr, h - 2,
                 "arrows to move    enter to choose    l for where", curses.A_DIM)
        stdscr.refresh()
        ch = read_key(stdscr, -1)
        if ch in (curses.KEY_UP, "k"):
            sel = (sel - 1) % len(items)
        elif ch in (curses.KEY_DOWN, "j"):
            sel = (sel + 1) % len(items)
        elif ch in ("l", "\x0c"):
            compass(cfg, "menu")
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            return items[sel]

def write_mode(stdscr, cfg):
    led(cfg, "write")
    path = today_path()
    text = read_file(path) if os.path.exists(path) else ""
    paras = text.split("\n")
    cache = {}
    stdscr.timeout(250)
    curses.curs_set(1)
    last_save, dirty, need_draw = time.time(), False, True

    while True:
        if need_draw:
            h, w = stdscr.getmaxyx()
            col = max(20, min(cfg["width"], w - 4))
            left = max(1, (w - col) // 2)
            body_h = max(1, h - 2)

            lines = []
            for i, p in enumerate(paras):
                c = cache.get(i)
                if c and c[0] == p and c[1] == col:
                    wl = c[2]
                else:
                    wl = wrap_para(p, col)
                    cache[i] = (p, col, wl)
                lines.extend(wl)
            if len(cache) > len(paras) + 16:
                cache = {k: v for k, v in cache.items() if k < len(paras)}

            fits = line_capacity(cfg, body_h)
            anchor = max(1, int(fits * cfg["anchor"] / 100))
            start = max(0, len(lines) - anchor)
            view = lines[start:start + fits]

            stdscr.erase()
            try:
                stdscr.addstr(0, left,
                    datetime.date.today().strftime("%A, %d %B %Y")[:col], curses.A_DIM)
            except curses.error:
                pass
            draw_page(stdscr, cfg, view, 1, left, col, body_h)
            status = "%d words" % sum(len(p.split()) for p in paras)
            if not dirty:
                status += "    saved"
            keys = "^X menu  ^L where  ^D off"
            try:
                stdscr.addstr(h - 1, left, status[:col], curses.A_DIM)
                if col - len(keys) > len(status) + 2:
                    stdscr.addstr(h - 1, left + col - len(keys), keys, curses.A_DIM)
            except curses.error:
                pass
            cy = max(1, min(h - 2, len(lines) - start))
            cx = min(left + min(len(lines[-1]), col), w - 1)
            try:
                stdscr.move(cy, cx)
            except curses.error:
                pass
            stdscr.refresh()
            need_draw = False

        ch = read_key(stdscr, 250)

        if ch is None:
            if dirty and time.time() - last_save > cfg["autosave"]:
                save_text("\n".join(paras), path)
                dirty, last_save, need_draw = False, time.time(), True
                led(cfg, "save")
            continue

        need_draw = True
        if ch == "\x18":
            save_text("\n".join(paras), path)
            return "menu"
        elif ch == "\x04":
            save_text("\n".join(paras), path)
            return "off"
        elif ch in ("\n", "\r"):
            paras.append("")
            save_text("\n".join(paras), path, sync=False)
            dirty = True
        elif ch in ("\x7f", "\b", curses.KEY_BACKSPACE):
            if paras[-1]:
                paras[-1] = paras[-1][:-1]
                dirty = True
            elif len(paras) > 1:
                paras.pop()
                cache.pop(len(paras), None)
                dirty = True
        elif ch == "\x17":
            p = paras[-1].rstrip(" ")
            idx = p.rfind(" ")
            paras[-1] = p[:idx + 1] if idx >= 0 else ""
            dirty = True
        elif ch == "\x15":
            paras[-1] = ""
            dirty = True
        elif ch == "\x0c":
            # ^L, not "l": this is a writing surface, so a bare letter has to
            # remain a letter. ^L conventionally means redraw, which need_draw
            # above already does, so the compass comes free with it.
            compass(cfg, "write")
        elif isinstance(ch, str) and (ch.isprintable() or ch == "\t"):
            paras[-1] += ch
            dirty = True

def read_entry(stdscr, cfg, path):
    led(cfg, "read")
    text = read_file(path)
    offset = 0
    stdscr.timeout(-1)
    curses.curs_set(0)
    while True:
        h, w = stdscr.getmaxyx()
        col = max(20, min(cfg["width"], w - 4))
        left = max(1, (w - col) // 2)
        lines = []
        for para in text.split("\n"):
            lines.extend(wrap_para(para, col))
        body_h = max(1, h - 2)
        fits = line_capacity(cfg, body_h)
        max_off = max(0, len(lines) - fits)
        offset = min(offset, max_off)
        stdscr.erase()
        try:
            stdscr.addstr(0, left, os.path.basename(path)[:-3], curses.A_DIM)
        except curses.error:
            pass
        draw_page(stdscr, cfg, lines[offset:offset + fits], 1, left, col, body_h)
        centered(stdscr, h - 1, "arrows to scroll    q to go back", curses.A_DIM)
        stdscr.refresh()
        ch = read_key(stdscr, -1)
        if ch == "q":
            return
        elif ch in ("l", "\x0c"):
            compass(cfg, "read")
        elif ch == curses.KEY_DOWN:
            offset = min(max_off, offset + 1)
        elif ch == curses.KEY_UP:
            offset = max(0, offset - 1)
        elif ch == curses.KEY_NPAGE:
            offset = min(max_off, offset + max(1, fits - 1))
        elif ch == curses.KEY_PPAGE:
            offset = max(0, offset - max(1, fits - 1))
        elif ch == curses.KEY_HOME:
            offset = 0
        elif ch == curses.KEY_END:
            offset = max_off

def browse(stdscr, cfg):
    files = entries()
    if not files:
        return
    led(cfg, "browse")
    sel, top = 0, 0
    stdscr.timeout(-1)
    curses.curs_set(0)
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        rows = max(1, h - 5)
        # Only `rows` entries fit on screen. Scroll to follow the selection,
        # and only when it would otherwise leave the window, so the list does
        # not jump around under you.
        if sel < top:
            top = sel
        elif sel >= top + rows:
            top = sel - rows + 1
        top = max(0, min(top, max(0, len(files) - rows)))
        centered(stdscr, 1, "entries", curses.A_BOLD)
        for i, f in enumerate(files[top:top + rows]):
            centered(stdscr, 3 + i, "  %s  " % f[:-3],
                     curses.A_REVERSE if top + i == sel else 0)
        footer = "enter to read    q to go back"
        if len(files) > rows:
            footer = "%d/%d    %s" % (sel + 1, len(files), footer)
        centered(stdscr, h - 1, footer, curses.A_DIM)
        stdscr.refresh()
        ch = read_key(stdscr, -1)
        if ch in (curses.KEY_UP, "k"):
            sel = (sel - 1) % len(files)
        elif ch in (curses.KEY_DOWN, "j"):
            sel = (sel + 1) % len(files)
        elif ch == curses.KEY_NPAGE:
            sel = min(len(files) - 1, sel + rows)
        elif ch == curses.KEY_PPAGE:
            sel = max(0, sel - rows)
        elif ch == curses.KEY_HOME:
            sel = 0
        elif ch == curses.KEY_END:
            sel = len(files) - 1
        elif ch in ("l", "\x0c"):
            compass(cfg, "browse")
        elif ch == "q":
            return
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            read_entry(stdscr, cfg, os.path.join(JOURNAL_DIR, files[sel]))
            led(cfg, "browse")      # read_entry changed it; put it back

def settings(stdscr, cfg):
    led(cfg, "settings")
    fields = ["theme", "font", "paper", "led", "width", "anchor", "autosave"]
    sel = 0
    stdscr.timeout(-1)
    curses.curs_set(0)
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        centered(stdscr, 1, "settings", curses.A_BOLD)
        # Single-spaced now that there are six of them.
        for i, f in enumerate(fields):
            centered(stdscr, 3 + i * 2, "  %-9s %-8s  " % (f, cfg[f]),
                     curses.A_REVERSE if i == sel else 0)
        centered(stdscr, h - 2, "left/right to change    q to go back", curses.A_DIM)
        stdscr.refresh()
        ch = read_key(stdscr, -1)
        f = fields[sel]
        if ch == curses.KEY_UP:
            sel = (sel - 1) % len(fields)
        elif ch == curses.KEY_DOWN:
            sel = (sel + 1) % len(fields)
        elif ch in (curses.KEY_LEFT, curses.KEY_RIGHT):
            d = 1 if ch == curses.KEY_RIGHT else -1
            if f == "theme":
                cfg[f] = THEMES[(THEMES.index(cfg[f]) + d) % len(THEMES)]
                apply_theme(stdscr, cfg)
            elif f == "font":
                cfg[f] = FONTS[(FONTS.index(cfg[f]) + d) % len(FONTS)]
                apply_font(cfg)
            elif f == "paper":
                cfg[f] = PAPERS[(PAPERS.index(cfg[f]) + d) % len(PAPERS)]
            elif f == "led":
                cfg[f] = LEDS[(LEDS.index(cfg[f]) + d) % len(LEDS)]
                # Demonstrate the choice immediately, so it is legible with no
                # screen. Uses the on-demand path, so picking "color" costs the
                # same single write as pressing ^L would.
                if cfg[f] == "off":
                    led(dict(cfg, led="blink"), "none")
                else:
                    compass(cfg, "settings")
            elif f == "width":
                cfg[f] = max(30, min(100, cfg[f] + d * 2))
            elif f == "anchor":
                cfg[f] = max(20, min(95, cfg[f] + d * 5))
            elif f == "autosave":
                cfg[f] = max(1, min(60, cfg[f] + d))
        elif ch in ("l", "\x0c"):
            compass(cfg, "settings")
        elif ch == "q":
            save_config(cfg)
            return

def hotspot():
    """Recovery path for the failure that keeps biting: the device joins a
    network that gives it no usable route, so there is no way in over SSH and
    no terminal on the device either. This brings the access point up from the
    keyboard. Needs the sudoers entry in device/011_journal-hotspot."""
    if DEV:
        return "dev mode: leaving the network alone"
    try:
        r = subprocess.run(
            ["sudo", "-n", "nmcli", "connection", "up", AP_NAME],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        if r.returncode == 0:
            return "hotspot up"
        out = r.stdout.decode("utf-8", "replace").strip().splitlines()
        return (out[-1] if out else "failed")[:70]
    except FileNotFoundError:
        return "sudo or nmcli not found"
    except subprocess.TimeoutExpired:
        return "timed out after 60s"
    except Exception as exc:
        return str(exc)[:70]


def hotspot_screen(stdscr, cfg):
    stdscr.timeout(-1)
    curses.curs_set(0)
    h, w = stdscr.getmaxyx()
    mid = max(3, h // 2)
    stdscr.erase()
    centered(stdscr, mid, "starting hotspot...", curses.A_BOLD)
    stdscr.refresh()

    result = hotspot()

    stdscr.erase()
    centered(stdscr, mid - 3, "hotspot", curses.A_BOLD)
    centered(stdscr, mid - 1, result)
    centered(stdscr, mid + 1, "network   %s" % AP_NAME, curses.A_DIM)
    centered(stdscr, mid + 2, "ssh       walker@%s" % AP_GATEWAY, curses.A_DIM)
    centered(stdscr, h - 2, "any key to go back", curses.A_DIM)
    stdscr.refresh()
    read_key(stdscr, -1)


def power_off():
    if not DEV:
        subprocess.run(["sudo", "/sbin/poweroff"])


def led_release(cfg):
    """Hand the LED back to whatever normally owns it, so it does not keep
    blinking a rhythm for an app that has exited."""
    led(dict(cfg, led="state") if cfg.get("led", "off") != "off" else cfg,
        "none")

def main(stdscr):
    curses.use_default_colors()
    cfg = load_config()
    apply_theme(stdscr, cfg)
    apply_font(cfg)
    stdscr.keypad(True)
    while True:
        led(cfg, "menu")
        choice = menu(stdscr, cfg)
        if choice == "Write":
            if write_mode(stdscr, cfg) == "off":
                led_release(cfg)
                power_off()
                return
        elif choice == "Browse entries":
            browse(stdscr, cfg)
        elif choice == "Settings":
            settings(stdscr, cfg)
            apply_font(cfg)
        elif choice == "Hotspot":
            hotspot_screen(stdscr, cfg)
        elif choice == "Shut down":
            led_release(cfg)
            power_off()
            return

# Guarded so the module can be imported by the tests without launching the UI.
if __name__ == "__main__":
    curses.wrapper(main)
