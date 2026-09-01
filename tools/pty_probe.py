#!/usr/bin/env python3
"""Run journal.py inside a pty and report which SGR attributes actually reach
the terminal. ncurses emits underline as the combined "0;4" form rather than a
bare "4", so check the parsed code lists, not a literal escape string.

    python3 scratch_pty_probe.py
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

CFG = os.path.expanduser("~/.journal-config-dev.json")
APP = os.path.expanduser("~/journal.py")


def probe(paper, term, theme="paper"):
    with open(CFG, "w") as f:
        json.dump({"theme": theme, "font": "default", "paper": paper,
                   "width": 40, "anchor": 62, "autosave": 5}, f)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = dict(os.environ, JOURNAL_DEV="1", TERM=term)
    proc = subprocess.Popen([sys.executable, APP], stdin=slave, stdout=slave,
                            stderr=slave, env=env, close_fds=True)
    os.close(slave)

    def drain(seconds):
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
        return out

    drain(1.5)                 # menu
    os.write(master, b"\r")    # choose Write
    drain(1.0)
    os.write(master, b"hello ruled world")
    out = drain(1.5)

    proc.terminate()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.kill()
    os.close(master)

    text = out.decode("utf-8", "replace")
    codes = sorted(set(re.findall(r"\x1b\[([0-9;]*)m", text)))
    underlined = any("4" in c.split(";") for c in codes)
    return codes, underlined, len(out)


print("%-18s %-8s %-10s %s" % ("TERM", "paper", "underline", "SGR codes seen"))
print("-" * 74)
for term in ("xterm-256color", "linux"):
    for paper in ("off", "lined", "margin"):
        codes, u, n = probe(paper, term)
        print("%-18s %-8s %-10s %s" % (term, paper, "YES" if u else "no",
                                       codes[:9]))

print()
print("--- and with a colourless theme, which is the ncv workaround ---")
for term in ("linux",):
    for theme in ("paper", "night"):
        codes, u, n = probe("lined", term, theme=theme)
        print("TERM=%-8s theme=%-7s underline=%-4s SGR=%s"
              % (term, theme, "YES" if u else "no", codes[:9]))
