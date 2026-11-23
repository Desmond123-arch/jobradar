"""Long menus (the time zone dropdown) scroll instead of running off the screen."""

import curses
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import timezones as tz  # noqa: E402
import tui  # noqa: E402

OPTIONS = [(f"k{i}", f"Option {i}") for i in range(34)]


def frame(cursor=0, top=0, h=20, w=60, options=OPTIONS, **kw):
    hits = []
    rows = tui.plain(tui.select_frame("Job Radar", "Pick one", options, cursor, h, w, hits=hits, top=top, **kw))
    return rows, hits


class WindowTests(unittest.TestCase):
    def test_a_short_list_shows_everything_with_no_markers(self):
        rows, hits = frame(options=OPTIONS[:5])
        self.assertEqual(len(hits), 5)
        self.assertFalse([r for r in rows if "more" in r])

    def test_a_long_list_is_cut_to_the_screen_and_says_how_much_is_hidden(self):
        rows, hits = frame()
        window = tui.option_window(20, (), (), len(OPTIONS))
        self.assertEqual(len(hits), window)
        self.assertLess(window, len(OPTIONS))
        self.assertIn(f"↓ {len(OPTIONS) - window} more", "\n".join(rows))
        self.assertNotRegex("\n".join(rows), r"↑ \d+ more")  # nothing above at the top

    def test_scrolling_down_shows_markers_both_ways_and_the_right_options(self):
        window = tui.option_window(20, (), (), len(OPTIONS))
        rows, hits = frame(cursor=15, top=10)
        text = "\n".join(rows)
        self.assertIn("↑ 10 more", text)
        self.assertIn(f"↓ {len(OPTIONS) - 10 - window} more", text)
        self.assertIn("Option 10", text)
        self.assertNotIn("Option 9\n", text)
        self.assertEqual([h[5] for h in hits], list(range(10, 10 + window)))  # absolute indexes for clicks

    def test_the_end_of_the_list_has_no_marker_below(self):
        window = tui.option_window(20, (), (), len(OPTIONS))
        rows, _ = frame(cursor=33, top=len(OPTIONS) - window)
        self.assertNotRegex("\n".join(rows), r"↓ \d+ more")  # nothing below at the end
        self.assertIn("Option 33", "\n".join(rows))

    def test_an_out_of_range_top_is_pulled_back(self):
        rows, hits = frame(top=500)
        self.assertEqual(hits[-1][5], len(OPTIONS) - 1)

    def test_the_key_hints_stay_on_the_last_row_at_every_height(self):
        for h in range(8, 40):
            rows, _ = frame(h=h)
            self.assertLessEqual(len(rows), h, h)
            self.assertIn("esc cancel", rows[-1], h)

    def test_notes_and_extra_rows_shrink_the_window(self):
        plain_window = tui.option_window(30, (), (), 34)
        self.assertLess(tui.option_window(30, ["one", "two"], (), 34), plain_window)
        self.assertLess(tui.option_window(30, (), [[("x", "dim")]] * 4, 34), plain_window)
        self.assertGreaterEqual(tui.option_window(5, ["a"] * 9, [[]] * 20, 34), 1)  # never zero, even when cramped

    def test_the_whole_time_zone_list_fits_a_normal_terminal_by_scrolling(self):
        options = [(tz.AUTO, "Detect")] + [(tz.key_for(o), f"{tz.fmt(o)} {p}") for o, p in tz.ZONES]
        for h in (24, 30, 40):
            rows, hits = frame(options=options, h=h)
            self.assertEqual(len(rows) <= h, True)
            self.assertGreater(len(hits), 5)


class FakeWin:
    def __init__(self, keys, height=20):
        self.keys, self.height = list(keys), height

    def getmaxyx(self):
        return self.height, 100

    def erase(self): pass
    def addstr(self, *a): pass
    def refresh(self): pass

    def get_wch(self):
        return self.keys.pop(0)


class FakeScreen:
    theme, attrs = "glow", {}

    def __init__(self, keys, height=20):
        self.win = FakeWin(keys, height)

    def open(self):
        return self.win

    def set_theme(self, name):
        self.theme = name

    def close(self): pass


class NavigationTests(unittest.TestCase):
    def pick(self, keys, height=20, options=OPTIONS, default="k0"):
        return tui.run_select("Job Radar", "Pick", options, default, screen=FakeScreen(keys, height))

    def test_arrowing_to_the_bottom_selects_the_last_option(self):
        self.assertEqual(self.pick([curses.KEY_DOWN] * 33 + [10]), "k33")

    def test_going_up_from_the_top_wraps_to_the_bottom_and_scrolls_there(self):
        self.assertEqual(self.pick([curses.KEY_UP, 10]), "k33")

    def test_starting_on_a_late_option_still_works(self):
        self.assertEqual(self.pick([10], default="k30"), "k30")
        self.assertEqual(self.pick([curses.KEY_DOWN, 10], default="k30"), "k31")

    def test_the_cursor_is_always_inside_the_visible_window(self):
        seen = []
        real = tui.select_frame

        def spy(*args, **kw):
            seen.append((args[3], kw.get("top", 0), tui.option_window(args[4], kw.get("notes", ()), kw.get("extra", ()), len(args[2]))))
            return real(*args, **kw)
        with mock.patch.object(tui, "select_frame", spy):
            self.pick([curses.KEY_DOWN] * 40 + [curses.KEY_UP] * 40 + [10])
        for cursor, top, window in seen:
            self.assertTrue(top <= cursor < top + window, (cursor, top, window))

    def test_a_click_after_scrolling_chooses_the_entry_that_was_clicked(self):
        window = tui.option_window(20, (), (), len(OPTIONS))
        keys = [curses.KEY_DOWN] * 25 + [curses.KEY_MOUSE]
        # after 25 downs the window has scrolled; the first visible option index is cursor - window + 1
        top = 25 - window + 1
        first_row = 4 + 1  # badge, blank, prompt, blank, then the "up" marker row
        with mock.patch.object(curses, "getmouse", return_value=(0, 5, first_row + 2, 0, curses.BUTTON1_CLICKED)):
            self.assertEqual(self.pick(keys), f"k{top + 2}")

    def test_the_wheel_scrolls_long_lists(self):
        keys = [curses.KEY_MOUSE] * 30 + [10]
        with mock.patch.object(curses, "getmouse", return_value=(0, 1, 1, 0, curses.BUTTON5_PRESSED)):
            self.assertEqual(self.pick(keys), "k30")

    def test_a_very_short_terminal_still_works(self):
        self.assertEqual(self.pick([curses.KEY_DOWN] * 5 + [10], height=9), "k5")


if __name__ == "__main__":
    unittest.main()
