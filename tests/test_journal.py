"""Tests for journal.py. Standard library only, like the app.

Run from the repo root:

    python -m unittest discover -s tests -v

The curses UI functions are driven through a fake stdscr that records what was
drawn, so the browse/read_entry viewport behaviour is tested for real rather
than by re-implementing the arithmetic here.
"""
import json
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
        self.moved = []

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
        self.moved.append((y, x))

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
    """Ruled pages put text on every other row with a drawn line beneath it.
    The line is characters in their own colour, not an underline attribute --
    an underline takes the text colour and reads as emphasis rather than paper."""

    def cfg(self, paper):
        c = dict(journal.DEFAULTS)
        c["paper"] = paper
        return c

    def rows_at(self, scr, y):
        return [s for dy, x, s, attr in scr.drawn if dy == y]

    def test_off_by_default(self):
        self.assertEqual(journal.DEFAULTS["paper"], "off")
        self.assertFalse(journal.is_ruled(journal.DEFAULTS))

    def test_both_ruled_modes_are_ruled(self):
        for p in ("ruled", "margin"):
            self.assertTrue(journal.is_ruled(self.cfg(p)), p)

    def test_missing_key_does_not_raise(self):
        # A config written by an older version will not have the key.
        self.assertFalse(journal.is_ruled({}))

    def test_ruling_halves_the_line_capacity(self):
        self.assertEqual(journal.line_capacity(self.cfg("off"), 22), 22)
        self.assertEqual(journal.line_capacity(self.cfg("ruled"), 22), 11)
        self.assertEqual(journal.line_capacity(self.cfg("margin"), 21), 11)

    def test_capacity_never_drops_below_one(self):
        for mode in ("off", "ruled", "margin"):
            self.assertGreaterEqual(journal.line_capacity(self.cfg(mode), 1), 1)

    def test_unruled_draws_text_on_consecutive_rows(self):
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("off"), ["one", "two"], 1, 5, 40, 3)
        self.assertEqual(self.rows_at(scr, 1), ["one"])
        self.assertEqual(self.rows_at(scr, 2), ["two"])
        self.assertNotIn(journal.RULE_CHAR,
                         "".join(s for y, x, s, a in scr.drawn))

    def test_ruled_puts_a_line_under_each_text_row(self):
        col = 30
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("ruled"), ["one", "two"], 1, 5, col, 6)
        self.assertEqual(self.rows_at(scr, 1), ["one"])       # text
        self.assertEqual(self.rows_at(scr, 2), [journal.RULE_CHAR * col])
        self.assertEqual(self.rows_at(scr, 3), ["two"])       # next text
        self.assertEqual(self.rows_at(scr, 4), [journal.RULE_CHAR * col])

    def test_the_rule_spans_the_full_column(self):
        for col in (20, 40, 58):
            scr = FakeScr()
            journal.draw_page(scr, self.cfg("ruled"), ["x"], 1, 3, col, 4)
            rules = [s for y, x, s, a in scr.drawn if journal.RULE_CHAR in s]
            self.assertTrue(rules)
            for r in rules:
                self.assertEqual(len(r), col)

    def test_rules_continue_past_the_end_of_the_text(self):
        # An empty page is still a ruled page; that is what makes it paper.
        rows = 8
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("ruled"), [], 1, 5, 30, rows)
        rules = [s for y, x, s, a in scr.drawn if journal.RULE_CHAR in s]
        self.assertEqual(len(rules), journal.line_capacity(self.cfg("ruled"), rows))

    def test_nothing_is_drawn_outside_the_given_rows(self):
        top, rows = 1, 5
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("margin"), ["a"] * 20, top, 5, 30, rows)
        ys = [y for y, x, s, a in scr.drawn]
        self.assertTrue(all(top <= y < top + rows for y in ys), sorted(set(ys)))

    def test_margin_marks_every_row(self):
        left, rows = 8, 4
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("margin"), ["x"], 1, left, 30, rows)
        margin = [s for y, x, s, a in scr.drawn if x == left - 2]
        self.assertEqual(len(margin), rows)
        self.assertEqual(set(margin), {journal.MARGIN_CHAR})

    def test_ruled_alone_draws_no_margin(self):
        left = 8
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("ruled"), ["x"], 1, left, 30, 4)
        self.assertEqual([s for y, x, s, a in scr.drawn if x == left - 2], [])

    def test_margin_is_skipped_when_there_is_no_room(self):
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("margin"), ["x"], 1, 1, 30, 2)
        self.assertFalse([1 for y, x, s, attr in scr.drawn if x < 0])

    def test_long_lines_are_truncated_to_the_column(self):
        col = 20
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("ruled"), ["y" * 100], 1, 2, col, 2)
        text = [s for y, x, s, a in scr.drawn if journal.RULE_CHAR not in s]
        self.assertEqual(len(text[0]), col)

    def test_the_rule_is_not_an_underline_attribute(self):
        # The point of the rework: emphasis is not paper.
        scr = FakeScr()
        journal.draw_page(scr, self.cfg("ruled"), ["x"], 1, 5, 20, 4)
        self.assertFalse(any(a & curses.A_UNDERLINE for y, x, s, a in scr.drawn))


class StaleConfig(unittest.TestCase):
    """The settings screen looks enum values up by index, so a value that no
    longer exists has to be corrected on load, not left to raise there."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._real = journal.CONFIG_PATH
        journal.CONFIG_PATH = os.path.join(self.dir, "cfg.json")

    def tearDown(self):
        journal.CONFIG_PATH = self._real
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, obj):
        with open(journal.CONFIG_PATH, "w") as f:
            json.dump(obj, f)

    def test_retired_paper_mode_falls_back(self):
        # "lined" was the old underline-based mode.
        self.write({"paper": "lined"})
        self.assertEqual(journal.load_config()["paper"], journal.DEFAULTS["paper"])

    def test_unknown_values_fall_back_for_every_enum(self):
        self.write({"theme": "zzz", "font": "zzz", "paper": "zzz"})
        cfg = journal.load_config()
        for key in ("theme", "font", "paper"):
            self.assertEqual(cfg[key], journal.DEFAULTS[key], key)

    def test_valid_values_are_kept(self):
        self.write({"theme": "paper", "paper": "margin"})
        cfg = journal.load_config()
        self.assertEqual(cfg["theme"], "paper")
        self.assertEqual(cfg["paper"], "margin")

    def test_non_integer_numbers_fall_back(self):
        self.write({"width": "wide", "anchor": None, "autosave": True})
        cfg = journal.load_config()
        for key in ("width", "anchor", "autosave"):
            self.assertEqual(cfg[key], journal.DEFAULTS[key], key)

    def test_every_enum_default_is_in_its_list(self):
        for key, allowed in journal.ENUMS.items():
            self.assertIn(journal.DEFAULTS[key], allowed, key)


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


class LedSignalling(unittest.TestCase):
    """The LED is the only feedback channel when no display is attached, so
    what it is asked to do matters, and it must never touch the keystroke path."""

    def capture(self, fn, dev=False):
        """Run fn with DEV forced off, recording what would be executed."""
        calls = []
        real_run, real_dev = journal.subprocess.run, journal.DEV
        journal.subprocess.run = lambda *a, **k: calls.append(a[0])
        journal.DEV = dev
        try:
            fn()
        finally:
            journal.subprocess.run = real_run
            journal.DEV = real_dev
        return calls

    def on(self, mode=None):
        return dict(journal.DEFAULTS)

    def test_there_is_no_setting_to_switch_it_off(self):
        # The compass is the only feedback this device has with nothing
        # attached. A setting that can leave it silent is worse than none, and
        # defaulting it off silently broke a real away-from-home test.
        self.assertNotIn("led", journal.DEFAULTS)
        self.assertNotIn("led", journal.ENUMS)
        self.assertFalse(hasattr(journal, "LEDS"))

    def test_it_works_on_a_default_config(self):
        calls = self.capture(lambda: journal.compass(dict(journal.DEFAULTS),
                                                     "write"))
        self.assertEqual(len(calls), 1, "must work with no configuration")

    def test_it_works_on_an_empty_config(self):
        calls = self.capture(lambda: journal.compass({}, "menu"))
        self.assertEqual(len(calls), 1)

    def test_a_stale_led_key_does_not_disable_it(self):
        # Configs written by earlier versions carry led=off / state / blink.
        for stale in ("off", "state", "blink", "color", "nonsense"):
            calls = self.capture(
                lambda s=stale: journal.compass(dict(journal.DEFAULTS, led=s),
                                                "write"))
            self.assertEqual(len(calls), 1,
                             "a stale led=%r must not silence the compass" % stale)

    def test_the_settings_screen_does_not_offer_it(self):
        scr = FakeScr(keys=["q"])
        journal.settings(scr, dict(journal.DEFAULTS))
        text = " ".join(s for y, x, s, attr in scr.drawn)
        self.assertNotIn("led", text)

    def test_nothing_runs_under_dev(self):
        # Local development must not shell out to the device helper.
        calls = self.capture(lambda: journal.compass(self.on(), "write"),
                             dev=True)
        self.assertEqual(calls, [])

    def test_compass_passes_two_arguments(self):
        calls = self.capture(lambda: journal.compass(self.on(), "browse"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ["compass", "browse"])
        self.assertIn(journal.LED_HELPER, calls[0])
        self.assertEqual(calls[0][:2], ["sudo", "-n"])

    def test_every_screen_has_a_compass_name(self):
        for where in ("write", "menu", "browse", "read", "settings"):
            calls = self.capture(lambda w=where: journal.compass(self.on(), w))
            self.assertEqual(calls[0][-1], where)

    def test_there_is_no_ambient_signalling_left(self):
        # The LED must stay dark unless asked. journal.led() used to signal on
        # screen changes and on save; it is gone, and nothing replaced it.
        self.assertFalse(hasattr(journal, "led"),
                         "an ambient led() entry point came back")
        self.assertFalse(hasattr(journal, "led_release"))

    def test_a_whole_session_signals_nothing_without_a_keypress(self):
        cfg = self.on()
        d = tempfile.mkdtemp()
        real = journal.JOURNAL_DIR
        journal.JOURNAL_DIR = d
        try:
            def session():
                # Write something, let it autosave, browse, read, and quit --
                # none of which may light the LED.
                scr = FakeScr(keys=list("hello") + ["\n", "\x18"])
                journal.write_mode(scr, cfg)
                with open(os.path.join(d, "2026-01-01.md"), "w") as f:
                    f.write("older entry")
                journal.browse(FakeScr(keys=["\n", "q", "q"]), cfg)
            calls = self.capture(session)
        finally:
            journal.JOURNAL_DIR = real
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(calls, [], "something signalled without ^L")


class CompassKey(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._real = journal.JOURNAL_DIR
        journal.JOURNAL_DIR = self.dir

    def tearDown(self):
        journal.JOURNAL_DIR = self._real
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_ctrl_l_does_not_insert_a_character_while_writing(self):
        # The whole reason it is ^L and not "l": this is a writing surface.
        scr = FakeScr(keys=["a", "\x0c", "b", "\x18"])
        journal.write_mode(scr, dict(journal.DEFAULTS))
        written = journal.read_file(journal.today_path())
        self.assertEqual(written, "ab", "^L must not land in the document")

    def test_plain_l_is_still_a_letter_while_writing(self):
        scr = FakeScr(keys=list("hello") + ["\x18"])
        journal.write_mode(scr, dict(journal.DEFAULTS))
        self.assertEqual(journal.read_file(journal.today_path()), "hello")

    def test_l_in_the_menu_does_not_choose_anything(self):
        # It must blink and keep waiting, not fall through to a selection.
        scr = FakeScr(keys=["l", "l", "\n"])
        self.assertEqual(journal.menu(scr, dict(journal.DEFAULTS)), "Write")

    def test_l_in_browse_does_not_open_or_exit(self):
        for name in ("2026-01-01.md", "2026-01-02.md"):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("body")
        scr = FakeScr(keys=["l", curses.KEY_DOWN, "q"])
        journal.browse(scr, dict(journal.DEFAULTS))
        rev = scr.reversed_rows()
        self.assertEqual(len(rev), 1)
        self.assertIn("2026-01-01", rev[0][1])

    def test_write_mode_hint_mentions_the_key(self):
        scr = FakeScr(keys=["\x18"])
        journal.write_mode(scr, dict(journal.DEFAULTS))
        text = " ".join(s for y, x, s, attr in scr.drawn)
        self.assertIn("^L", text)


class Cursor(unittest.TestCase):
    """Where the next character will land has to be visible, and has to be in
    the right place. Ruled mode puts text on every other row, so a row number
    derived from the count of visible lines lands on a ruled line instead."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._real = journal.JOURNAL_DIR
        journal.JOURNAL_DIR = self.dir

    def tearDown(self):
        journal.JOURNAL_DIR = self._real
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_write(self, text, paper="off", h=24, w=80):
        # write_mode opens today's entry and appends, so each run has to start
        # from an empty file or the line count carries over from the last one.
        path = journal.today_path()
        if os.path.exists(path):
            os.remove(path)
        cfg = dict(journal.DEFAULTS, paper=paper, width=40)
        scr = FakeScr(h=h, w=w, keys=list(text) + ["\x18"])
        journal.write_mode(scr, cfg)
        return scr, cfg

    def caret(self, scr):
        blocks = [(y, x) for y, x, s, a in scr.drawn
                  if a & curses.A_REVERSE and s == " "]
        return blocks[-1] if blocks else None

    def test_a_caret_block_is_drawn(self):
        scr, _ = self.run_write("hello")
        self.assertIsNotNone(self.caret(scr),
                             "nothing marks the insertion point")

    def test_caret_sits_where_the_terminal_cursor_was_put(self):
        scr, _ = self.run_write("hello")
        self.assertEqual(self.caret(scr), scr.moved[-1])

    def test_caret_follows_the_text_across_a_line(self):
        short, _ = self.run_write("hi")
        longer, _ = self.run_write("hello there")
        self.assertGreater(self.caret(longer)[1], self.caret(short)[1])

    def test_unruled_single_line_sits_on_the_first_row(self):
        scr, _ = self.run_write("one line")
        self.assertEqual(self.caret(scr)[0], 1)

    def test_ruled_single_line_also_sits_on_the_first_row(self):
        scr, _ = self.run_write("one line", paper="ruled")
        self.assertEqual(self.caret(scr)[0], 1)

    def test_ruled_second_line_skips_the_rule_row(self):
        # Two paragraphs: unruled the cursor belongs on row 2, ruled on row 3,
        # because row 2 holds the ruled line under the first one.
        plain, _ = self.run_write("a\nb", paper="off")
        ruled, _ = self.run_write("a\nb", paper="ruled")
        self.assertEqual(self.caret(plain)[0], 2)
        self.assertEqual(self.caret(ruled)[0], 3)

    def test_ruled_cursor_never_lands_on_a_rule_row(self):
        # Rules are drawn on even rows starting at 2; text on odd rows from 1.
        for n in range(1, 7):
            scr, _ = self.run_write("\n".join("line %d" % i for i in range(n)),
                                    paper="ruled")
            row = self.caret(scr)[0]
            self.assertEqual(row % 2, 1,
                             "%d lines put the cursor on rule row %d" % (n, row))

    def test_caret_stays_inside_the_screen(self):
        scr, _ = self.run_write("x" * 200, paper="ruled", h=10, w=30)
        y, x = self.caret(scr)
        self.assertTrue(1 <= y <= 8, y)
        self.assertLess(x, 30)

    def test_margin_mode_places_the_cursor_like_ruled(self):
        ruled, _ = self.run_write("a\nb", paper="ruled")
        margin, _ = self.run_write("a\nb", paper="margin")
        self.assertEqual(self.caret(ruled)[0], self.caret(margin)[0])


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
    """Raising the AP drops the connection the screen would be read over, so
    this is operated blind. Both directions have to be reachable from the
    keyboard, and neither may be able to strand the device."""

    def screen(self, result, stop=False, h=24):
        """Drive the screen with a fixed helper result."""
        real_up, real_down = journal.hotspot, journal.hotspot_down
        journal.hotspot = lambda: result
        journal.hotspot_down = lambda: result
        try:
            scr = FakeScr(h=h, keys=["x"])
            journal.hotspot_screen(scr, dict(journal.DEFAULTS), stop=stop)
        finally:
            journal.hotspot = real_up
            journal.hotspot_down = real_down
        return " ".join(s for y, x, s, attr in scr.drawn)

    def test_neither_direction_touches_the_network_under_dev(self):
        self.assertIn("dev mode", journal.hotspot())
        self.assertIn("dev mode", journal.hotspot_down())

    def test_status_is_false_under_dev(self):
        self.assertFalse(journal.hotspot_active())

    def test_when_up_it_shows_how_to_connect(self):
        text = self.screen("hotspot up for 15 min")
        self.assertIn(journal.AP_NAME, text)
        self.assertIn(journal.AP_GATEWAY, text)
        self.assertIn("comes back by itself", text)

    def test_when_stopped_it_does_not_offer_a_dead_address(self):
        text = self.screen("hotspot stopped", stop=True)
        self.assertIn("stopped", text)
        self.assertNotIn(journal.AP_GATEWAY, text)

    def test_a_failure_is_shown_rather_than_swallowed(self):
        text = self.screen("failed to start", stop=False)
        self.assertIn("failed", text)
        self.assertNotIn(journal.AP_GATEWAY, text)

    def test_the_menu_names_the_action_not_the_feature(self):
        down = FakeScr(keys=["\n"])
        self.assertEqual(journal.menu(down, dict(journal.DEFAULTS),
                                      hotspot_up=False), "Write")
        # The fourth item is the one that changes.
        for up, expected in ((False, "Hotspot"), (True, "Stop hotspot")):
            scr = FakeScr(keys=[curses.KEY_DOWN] * 3 + ["\n"])
            self.assertEqual(
                journal.menu(scr, dict(journal.DEFAULTS), hotspot_up=up),
                expected)

    def test_shut_down_stays_the_last_item_either_way(self):
        for up in (False, True):
            scr = FakeScr(keys=[curses.KEY_UP, "\n"])
            self.assertEqual(
                journal.menu(scr, dict(journal.DEFAULTS), hotspot_up=up),
                "Shut down")


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
