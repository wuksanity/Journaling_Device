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
THEME_COLORS = {
    "paper": (curses.COLOR_BLACK, curses.COLOR_WHITE),
    "night": (curses.COLOR_WHITE, curses.COLOR_BLACK),
    "amber": (curses.COLOR_YELLOW, curses.COLOR_BLACK),
    "green": (curses.COLOR_GREEN, curses.COLOR_BLACK),
    "ocean": (curses.COLOR_CYAN, curses.COLOR_BLUE),
}
DEFAULTS = {"theme": "night", "width": 58, "anchor": 62, "autosave": 5}

AP_NAME = "journal-ap"
AP_GATEWAY = "10.42.0.1"

def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
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

def apply_theme(stdscr, cfg):
    try:
        curses.start_color()
        fg, bg = THEME_COLORS.get(cfg["theme"], THEME_COLORS["night"])
        curses.init_pair(1, fg, bg)
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
        centered(stdscr, h - 2, "arrows to move    enter to choose", curses.A_DIM)
        stdscr.refresh()
        ch = read_key(stdscr, -1)
        if ch in (curses.KEY_UP, "k"):
            sel = (sel - 1) % len(items)
        elif ch in (curses.KEY_DOWN, "j"):
            sel = (sel + 1) % len(items)
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            return items[sel]

def write_mode(stdscr, cfg):
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

            anchor = max(1, int(body_h * cfg["anchor"] / 100))
            start = max(0, len(lines) - anchor)
            view = lines[start:start + body_h]

            stdscr.erase()
            try:
                stdscr.addstr(0, left,
                    datetime.date.today().strftime("%A, %d %B %Y")[:col], curses.A_DIM)
            except curses.error:
                pass
            for i, line in enumerate(view):
                try:
                    stdscr.addstr(i + 1, left, line[:col])
                except curses.error:
                    pass
            status = "%d words" % sum(len(p.split()) for p in paras)
            if not dirty:
                status += "    saved"
            keys = "^X menu   ^D off"
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
        elif isinstance(ch, str) and (ch.isprintable() or ch == "\t"):
            paras[-1] += ch
            dirty = True

def read_entry(stdscr, cfg, path):
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
        max_off = max(0, len(lines) - body_h)
        offset = min(offset, max_off)
        stdscr.erase()
        try:
            stdscr.addstr(0, left, os.path.basename(path)[:-3], curses.A_DIM)
        except curses.error:
            pass
        for i, line in enumerate(lines[offset:offset + body_h]):
            try:
                stdscr.addstr(i + 1, left, line[:col])
            except curses.error:
                pass
        centered(stdscr, h - 1, "arrows to scroll    q to go back", curses.A_DIM)
        stdscr.refresh()
        ch = read_key(stdscr, -1)
        if ch == "q":
            return
        elif ch == curses.KEY_DOWN:
            offset = min(max_off, offset + 1)
        elif ch == curses.KEY_UP:
            offset = max(0, offset - 1)
        elif ch == curses.KEY_NPAGE:
            offset = min(max_off, offset + (h - 3))
        elif ch == curses.KEY_PPAGE:
            offset = max(0, offset - (h - 3))
        elif ch == curses.KEY_HOME:
            offset = 0
        elif ch == curses.KEY_END:
            offset = max_off

def browse(stdscr, cfg):
    files = entries()
    if not files:
        return
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
        elif ch == "q":
            return
        elif ch in ("\n", "\r", curses.KEY_ENTER):
            read_entry(stdscr, cfg, os.path.join(JOURNAL_DIR, files[sel]))

def settings(stdscr, cfg):
    fields = ["theme", "width", "anchor", "autosave"]
    sel = 0
    stdscr.timeout(-1)
    curses.curs_set(0)
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        centered(stdscr, 1, "settings", curses.A_BOLD)
        for i, f in enumerate(fields):
            centered(stdscr, 4 + i * 2, "  %-9s %-8s  " % (f, cfg[f]),
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
            elif f == "width":
                cfg[f] = max(30, min(100, cfg[f] + d * 2))
            elif f == "anchor":
                cfg[f] = max(20, min(95, cfg[f] + d * 5))
            elif f == "autosave":
                cfg[f] = max(1, min(60, cfg[f] + d))
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

def main(stdscr):
    curses.use_default_colors()
    cfg = load_config()
    apply_theme(stdscr, cfg)
    stdscr.keypad(True)
    while True:
        choice = menu(stdscr, cfg)
        if choice == "Write":
            if write_mode(stdscr, cfg) == "off":
                power_off()
                return
        elif choice == "Browse entries":
            browse(stdscr, cfg)
        elif choice == "Settings":
            settings(stdscr, cfg)
        elif choice == "Hotspot":
            hotspot_screen(stdscr, cfg)
        elif choice == "Shut down":
            power_off()
            return

# Guarded so the module can be imported by the tests without launching the UI.
if __name__ == "__main__":
    curses.wrapper(main)
