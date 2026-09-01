"""Tests for journal.py. Standard library only, like the app.

Run from the repo root:

    python -m unittest discover -s tests -v

The curses UI functions are driven through a fake stdscr that records what was
drawn, so the browse/read_entry viewport behaviour is tested for real rather
than by re-implementing the arithmetic here.
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JOURNAL_DEV"] = "1"

import curses

# curs_set talks to a real terminal; there isn't one under a test runner.
curses.curs_set = lambda n: None

import journal


class FakeScr:
    """Minimal stdscr. Records addstr calls so assertions can inspect the
    screen. get_wch replays a scripted key sequence; always end the script
    with a key that exits the loop under test, or it will spin forever."""

    def __init__(self, h=24, w=80, keys=()):
        self.h, self.w = h, w
        self.keys = list(keys)
        self.drawn = []

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.drawn = []

    def addstr(self, y, x, s, attr=0):
        self.drawn.append((y, x, s, attr))

    def get_wch(self):
        if not self.keys:
            raise curses.error("key script exhausted")
        return self.keys.pop(0)

    def refresh(self):
        pass

    def timeout(self, t):
        pass

    def keypad(self, flag):
        pass

    def move(self, y, x):
        pass

    def bkgd(self, *a):
        pass

    def reversed_rows(self):
        return [(y, s) for y, x, s, attr in self.drawn if attr & curses.A_REVERSE]

    def text_at(self, y):
        for dy, x, s, attr in self.drawn:
            if dy == y:
                return s
        return None


class DevSandbox(unittest.TestCase):
    def test_dev_flag_redirects_paths(self):
        self.assertTrue(journal.DEV)
        self.assertTrue(journal.JOURNAL_DIR.endswith("journal-dev"))
        self.assertIn("config-dev", journal.CONFIG_PATH)

    def test_power_off_is_a_noop_under_dev(self):
        # Would shell out to sudo /sbin/poweroff otherwise.
        self.assertIsNone(journal.power_off())


class WrapPara(unittest.TestCase):
    def test_trailing_space_is_preserved(self):
        # textwrap.wrap strips trailing whitespace, which used to stop the
        # cursor advancing until the character after a space was typed.
        self.assertEqual(journal.wrap_para("hello ", 20), ["hello "])

    def test_empty_paragraph_yields_one_blank_line(self):
        self.assertEqual(journal.wrap_para("", 20), [""])

    def test_trailing_space_overflowing_the_column_wraps(self):
        out = journal.wrap_para("abcde ", 5)
        self.assertEqual("".join(out), "abcde ")
        self.assertTrue(all(len(line) <= 5 for line in out))

    def test_long_paragraph_respects_column(self):
        para = " ".join(["word"] * 40)
        for line in journal.wrap_para(para, 30):
            self.assertLessEqual(len(line), 30)


class AtomicSave(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "entry.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_writes_content(self):
        journal.save_text("hello\nworld", self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello\nworld")

    def test_leaves_no_temp_file_behind(self):
        journal.save_text("x", self.path)
        self.assertEqual(os.listdir(self.dir), ["entry.md"])

    def test_overwrite_replaces_rather_than_truncating(self):
        journal.save_text("original long content", self.path)
        journal.save_text("short", self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "short")
        self.assertEqual(os.listdir(self.dir), ["entry.md"])

    def test_existing_entry_survives_a_failure_before_the_rename(self):
        # This is the whole point of writing to a temp file first: the previous
        # entry stays intact instead of being truncated to nothing.
        journal.save_text("precious", self.path)
        real_replace = os.replace

        def boom(*a):
            raise OSError("simulated failure before the rename")

        journal.os.replace = boom
        try:
            with self.assertRaises(OSError):
                journal.save_text("replacement", self.path)
        finally:
            journal.os.replace = real_replace
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "precious")

    def test_sync_false_still_writes(self):
        journal.save_text("no fsync", self.path, sync=False)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "no fsync")

    def test_unicode_round_trip(self):
        journal.save_text("café — tiếng Việt", self.path)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "café — tiếng Việt")


class BrowseViewport(unittest.TestCase):
    """browse() used to draw only the first h-5 entries while letting the
    selection wrap over the whole list, so past that point nothing was
    highlighted and Enter opened a file you could not see."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._real = journal.JOURNAL_DIR
        journal.JOURNAL_DIR = self.dir
        self.names = ["2026-%02d-%02d.md" % (m, d)
                      for m in (1, 2) for d in range(1, 16)]
        for n in self.names:
            with open(os.path.join(self.dir, n), "w", encoding="utf-8") as f:
                f.write("body of %s" % n)
        self.newest_first = sorted(self.names, reverse=True)

    def tearDown(self):
        journal.JOURNAL_DIR = self._real
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, keys, h=24):
        scr = FakeScr(h=h, keys=list(keys) + ["q"])
        journal.browse(scr, dict(journal.DEFAULTS))
        return scr

    def test_selection_past_the_first_screenful_stays_visible(self):
        h = 24
        rows = h - 5                      # 19 visible, 30 entries total
        target = 25                       # well past the old cutoff
        self.assertGreater(target, rows)
        scr = self._run([curses.KEY_DOWN] * target, h=h)

        rev = scr.reversed_rows()
        self.assertEqual(len(rev), 1, "exactly one row should be highlighted")
        self.assertIn(self.newest_first[target][:-3], rev[0][1])

    def test_never_draws_more_rows_than_fit(self):
        h = 24
        scr = self._run([curses.KEY_DOWN] * 25, h=h)
        body = [y for y, x, s, attr in scr.drawn if 3 <= y < h - 1]
        self.assertLessEqual(len(body), h - 5)

    def test_end_key_reaches_the_last_entry(self):
        scr = self._run([curses.KEY_END])
        rev = scr.reversed_rows()
        self.assertEqual(len(rev), 1)
        self.assertIn(self.newest_first[-1][:-3], rev[0][1])

    def test_wrapping_upward_from_the_top_shows_the_last_entry(self):
        scr = self._run([curses.KEY_UP])
        rev = scr.reversed_rows()
        self.assertEqual(len(rev), 1)
        self.assertIn(self.newest_first[-1][:-3], rev[0][1])

    def test_page_down_then_page_up_returns_to_the_top(self):
        scr = self._run([curses.KEY_NPAGE, curses.KEY_PPAGE])
        rev = scr.reversed_rows()
        self.assertEqual(len(rev), 1)
        self.assertIn(self.newest_first[0][:-3], rev[0][1])

    def test_position_indicator_appears_when_the_list_overflows(self):
        scr = self._run([], h=24)
        footer = scr.text_at(23)
        self.assertIsNotNone(footer)
        self.assertIn("1/30", footer)

    def test_short_list_needs_no_indicator(self):
        scr = self._run([], h=80)          # 75 rows for 30 entries
        rev = scr.reversed_rows()
        self.assertEqual(len(rev), 1)
        self.assertIn(self.newest_first[0][:-3], rev[0][1])
        self.assertNotIn("/30", scr.text_at(79) or "")

    def test_tiny_screen_still_highlights_something(self):
        scr = self._run([curses.KEY_DOWN] * 3, h=7)
        self.assertEqual(len(scr.reversed_rows()), 1)

    def test_empty_directory_returns_immediately(self):
        for n in self.names:
            os.remove(os.path.join(self.dir, n))
        scr = FakeScr(keys=[])            # no keys: must not read any
        journal.browse(scr, dict(journal.DEFAULTS))

    def test_interrupted_write_temp_files_are_not_listed(self):
        with open(os.path.join(self.dir, "2026-03-01.md.tmp"), "w") as f:
            f.write("interrupted write")
        self.assertNotIn("2026-03-01.md.tmp", journal.entries())


class ReadEntryScrolling(unittest.TestCase):
    """read_entry used to allow scrolling until the final line sat at the top
    of the screen, leaving a nearly empty page."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "2026-01-01.md")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join("line %d" % i for i in range(100)))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, keys, h=24):
        scr = FakeScr(h=h, keys=list(keys) + ["q"])
        journal.read_entry(scr, dict(journal.DEFAULTS), self.path)
        return scr

    def test_scrolling_to_the_bottom_still_shows_a_full_page(self):
        h = 24
        scr = self._run([curses.KEY_DOWN] * 400, h=h)
        body = [s for y, x, s, attr in scr.drawn if 1 <= y <= h - 2]
        self.assertEqual(len(body), h - 2)
        self.assertIn("line 99", body[-1])

    def test_page_down_does_not_overshoot(self):
        h = 24
        scr = self._run([curses.KEY_NPAGE] * 40, h=h)
        body = [s for y, x, s, attr in scr.drawn if 1 <= y <= h - 2]
        self.assertEqual(len(body), h - 2)

    def test_end_then_home_returns_to_the_top(self):
        scr = self._run([curses.KEY_END, curses.KEY_HOME])
        body = [s for y, x, s, attr in scr.drawn if y == 1]
        self.assertIn("line 0", body[0])

    def test_short_entry_does_not_scroll(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("just one line")
        scr = self._run([curses.KEY_DOWN] * 10)
        body = [s for y, x, s, attr in scr.drawn if y == 1]
        self.assertIn("just one line", body[0])


class Config(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._real = journal.CONFIG_PATH
        journal.CONFIG_PATH = os.path.join(self.dir, "cfg.json")

    def tearDown(self):
        journal.CONFIG_PATH = self._real
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_defaults_load_when_the_file_is_missing(self):
        self.assertEqual(journal.load_config(), journal.DEFAULTS)

    def test_round_trip(self):
        cfg = dict(journal.DEFAULTS)
        cfg["theme"] = "amber"
        cfg["width"] = 72
        journal.save_config(cfg)
        loaded = journal.load_config()
        self.assertEqual(loaded["theme"], "amber")
        self.assertEqual(loaded["width"], 72)

    def test_corrupt_config_falls_back_to_defaults(self):
        with open(journal.CONFIG_PATH, "w") as f:
            f.write("{not json")
        self.assertEqual(journal.load_config(), journal.DEFAULTS)

    def test_partial_config_keeps_defaults_for_missing_keys(self):
        with open(journal.CONFIG_PATH, "w") as f:
            f.write('{"theme": "ocean"}')
        cfg = journal.load_config()
        self.assertEqual(cfg["theme"], "ocean")
        self.assertEqual(cfg["autosave"], journal.DEFAULTS["autosave"])

    def test_every_theme_has_colours(self):
        for name in journal.THEMES:
            self.assertIn(name, journal.THEME_COLORS)


class RuledPaper(unittest.TestCase):
    """The ruling is an underline attribute on a space-padded line, so it costs
    no rows. The padding is what makes the rule span the full measure instead
    of stopping at the end of the text."""

    def cfg(self, paper):
        c = dict(journal.DEFAULTS)
        c["paper"] = paper
        return c

    def test_attr_is_off_by_default(self):
        self.assertEqual(journal.DEFAULTS["paper"], "off")
        self.assertEqual(journal.paper_attr(journal.DEFAULTS), 0)

    def test_attr_set_for_lined_and_margin(self):
        for p in ("lined", "margin"):
            self.assertEqual(journal.paper_attr(self.cfg(p)),
                             curses.A_UNDERLINE, p)

    def test_missing_key_does_not_raise(self):
        # An older config file will not have the key.
        self.assertEqual(journal.paper_attr({}), 0)

    def test_unruled_lines_are_not_padded(self):
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("off"), ["short"], 1, 5, 40, 3)
        drawn = [s for y, x, s, attr in scr.drawn]
        self.assertEqual(drawn[0], "short")
        self.assertTrue(all(a == 0 for y, x, s, a in scr.drawn))

    def test_ruled_lines_are_padded_to_the_full_column(self):
        col = 40
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("lined"), ["short"], 1, 5, col, 3)
        for y, x, s, attr in scr.drawn:
            self.assertEqual(len(s), col, "every ruled row spans the column")
            self.assertTrue(attr & curses.A_UNDERLINE)

    def test_rows_past_the_text_are_still_ruled(self):
        # This is what makes it read as ruled paper rather than underlined text.
        rows = 6
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("lined"), ["one"], 1, 5, 30, rows)
        ruled = [s for y, x, s, attr in scr.drawn if attr & curses.A_UNDERLINE]
        self.assertEqual(len(ruled), rows)
        self.assertEqual(ruled[-1].strip(), "", "trailing rows are blank but ruled")

    def test_margin_draws_a_rule_left_of_the_column(self):
        left = 8
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("margin"), ["x"], 1, left, 30, 4)
        margin = [(y, x, s) for y, x, s, attr in scr.drawn if x == left - 2]
        self.assertEqual(len(margin), 4, "one margin mark per row")
        self.assertEqual(margin[0][2], "│")

    def test_margin_is_skipped_when_there_is_no_room(self):
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("margin"), ["x"], 1, 1, 30, 2)
        self.assertFalse([1 for y, x, s, attr in scr.drawn if x < 0])

    def test_long_lines_are_truncated_to_the_column(self):
        col = 20
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("lined"), ["y" * 100], 1, 2, col, 1)
        self.assertEqual(len(scr.drawn[0][2]), col)


class ConsoleFont(unittest.TestCase):
    def test_default_is_a_known_option(self):
        self.assertIn(journal.DEFAULTS["font"], journal.FONTS)
        self.assertEqual(journal.FONTS[0], "default")

    def test_every_size_maps_to_a_real_family_name(self):
        for size in journal.FONTS[1:]:
            name = journal.FONT_FAMILY % size
            self.assertTrue(name.startswith("Uni2-Terminus"))

    def test_apply_font_is_a_noop_under_dev(self):
        # Must not shell out to setfont during local development.
        called = []
        real = journal.subprocess.run
        journal.subprocess.run = lambda *a, **k: called.append(a)
        try:
            cfg = dict(journal.DEFAULTS)
            cfg["font"] = "24x12"
            journal.apply_font(cfg)
        finally:
            journal.subprocess.run = real
        self.assertEqual(called, [], "DEV must not run setfont")

    def test_apply_font_skips_the_default(self):
        called = []
        real = journal.subprocess.run
        journal.subprocess.run = lambda *a, **k: called.append(a)
        try:
            journal.apply_font({"font": "default"})
        finally:
            journal.subprocess.run = real
        self.assertEqual(called, [])

    def test_missing_font_key_does_not_raise(self):
        journal.apply_font({})


class Menu(unittest.TestCase):
    def _choose(self, index):
        scr = FakeScr(keys=[curses.KEY_DOWN] * index + ["\n"])
        return journal.menu(scr, dict(journal.DEFAULTS))

    def test_each_item_is_selectable(self):
        expected = ["Write", "Browse entries", "Settings", "Hotspot",
                    "Shut down"]
        for i, name in enumerate(expected):
            self.assertEqual(self._choose(i), name)

    def test_wraps_upward_to_the_last_item(self):
        scr = FakeScr(keys=[curses.KEY_UP, "\n"])
        self.assertEqual(journal.menu(scr, dict(journal.DEFAULTS)), "Shut down")


class Hotspot(unittest.TestCase):
    def test_does_nothing_under_dev(self):
        # Must not shell out to nmcli during local development.
        self.assertIn("dev mode", journal.hotspot())

    def test_screen_shows_how_to_connect(self):
        scr = FakeScr(keys=["x"])
        journal.hotspot_screen(scr, dict(journal.DEFAULTS))
        text = " ".join(s for y, x, s, attr in scr.drawn)
        self.assertIn(journal.AP_NAME, text)
        self.assertIn(journal.AP_GATEWAY, text)


class ReadKey(unittest.TestCase):
    def test_escape_sequences_are_swallowed(self):
        # A terminal answering a colour query sends this back through stdin;
        # without the filter it landed in the document as garbage.
        scr = FakeScr(keys=list("\x1b]11;rgb:0c0c/0c0c/0c0c\x07"))
        self.assertIsNone(journal.read_key(scr))

    def test_ordinary_character_passes_through(self):
        self.assertEqual(journal.read_key(FakeScr(keys=["a"])), "a")

    def test_exhausted_input_returns_none(self):
        self.assertIsNone(journal.read_key(FakeScr(keys=[])))


if __name__ == "__main__":
    unittest.main(verbosity=2)
