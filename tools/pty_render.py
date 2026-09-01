#!/usr/bin/env python3
"""Render what journal.py actually draws, by running it in a pty and replaying
the output into a character grid.

Curses output is escape sequences, not text, so the only way to see the page is
to interpret them. This handles the subset ncurses uses for absolute placement,
which is enough to show the layout faithfully.

    python3 tools/pty_render.py ruled
    python3 tools/pty_render.py margin paper
"""
import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

ROWS, COLS = 20, 74
CFG = os.path.expanduser("~/.journal-config-dev.json")
APP = os.path.expanduser("~/journal.py")

paper = sys.argv[1] if len(sys.argv) > 1 else "ruled"
theme = sys.argv[2] if len(sys.argv) > 2 else "paper"

with open(CFG, "w") as f:
    json.dump({"theme": theme, "font": "default", "paper": paper, "led": "off",
               "width": 44, "anchor": 62, "autosave": 5}, f)

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
proc = subprocess.Popen([sys.executable, APP], stdin=slave, stdout=slave,
                        stderr=slave, close_fds=True,
                        env=dict(os.environ, JOURNAL_DEV="1",
                                 TERM="tmux-256color"))
os.close(slave)


def drain(seconds):
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.2)
        if r:
            try:
                chunk = os.read(master, 1 << 20)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


# Accumulate every byte from the start. Keeping only the last drain captures an
# incremental repaint -- ncurses does not resend unchanged cells, so rules drawn
# during the first paint would be absent and look like a bug in the app.
raw = b""
raw += drain(1.5)
os.write(master, b"\r")                      # Write
raw += drain(1.0)
os.write(master, "Bearing north by the light alone. ".encode())
raw += drain(0.6)
os.write(master, b"\r")
os.write(master, "No screen, only the sound of keys.".encode())
raw += drain(1.5)

proc.terminate()
try:
    proc.wait(timeout=4)
except subprocess.TimeoutExpired:
    proc.kill()
os.close(master)

text = raw.decode("utf-8", "replace")

# --- replay into a grid ----------------------------------------------------
grid = [[" "] * COLS for _ in range(ROWS)]
cy = cx = 0
i = 0
CSI = re.compile(r"\x1b\[([0-9;?]*)([a-zA-Z@])")

while i < len(text):
    ch = text[i]
    if ch == "\x1b":
        m = CSI.match(text, i)
        if not m:
            i += 2
            continue
        args, cmd = m.group(1), m.group(2)
        nums = [int(p) for p in args.split(";") if p.isdigit()]
        if cmd == "H":
            cy = (nums[0] - 1) if nums else 0
            cx = (nums[1] - 1) if len(nums) > 1 else 0
        elif cmd == "J":
            grid = [[" "] * COLS for _ in range(ROWS)]
        elif cmd == "K":
            for x in range(cx, COLS):
                grid[cy][x] = " "
        elif cmd in "ABCD":
            d = nums[0] if nums else 1
            if cmd == "A":
                cy -= d
            elif cmd == "B":
                cy += d
            elif cmd == "C":
                cx += d
            elif cmd == "D":
                cx -= d
        i = m.end()
        continue
    if ch == "\r":
        cx = 0
    elif ch == "\n":
        cy += 1
        cx = 0
    elif ch == "\x08":
        cx = max(0, cx - 1)
    elif ch >= " ":
        if 0 <= cy < ROWS and 0 <= cx < COLS:
            grid[cy][cx] = ch
        cx += 1
    i += 1
    cy = max(0, min(cy, ROWS - 1))
    cx = max(0, min(cx, COLS - 1))

print("paper=%s theme=%s   (%d bytes of escape output)" % (paper, theme, len(raw)))
print("+" + "-" * COLS + "+")
for row in grid:
    print("|" + "".join(row) + "|")
print("+" + "-" * COLS + "+")
