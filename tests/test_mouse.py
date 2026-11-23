"""Mouse support: what a click or a wheel turn does, without a terminal."""

import curses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings  # noqa: E402
import tui  # noqa: E402


def job(i, new=False):
    return dict(title=f"Backend Engineer {i}", company=f"Co{i}", location="Berlin", region="emea", region_reason="in EMEA",
                new=new, score=5, posted="2026-11-05", url=f"https://x/{i}", why=["engineering role"],
                description="Python.", pay="")


JOBS = [job(i, new=(i < 2)) for i in range(12)]
H, W = 32, 120
BODY_TOP = 3  # rows 0-2 are the header, filter line and a blank


def drawn(width=W, height=H, jobs=JOBS, **state_kw):
    st = tui.State(jobs, "Chrome")
    for k, v in state_kw.items():
        setattr(st, k, v)
    return st, tui.frame(st, height, width)


class MouseKindTests(unittest.TestCase):
    def test_left_button_variants_are_clicks(self):
        for b in (curses.BUTTON1_PRESSED, curses.BUTTON1_CLICKED, curses.BUTTON1_DOUBLE_CLICKED):
            self.assertEqual(tui.mouse_kind(b), "click")

    def test_wheel_and_everything_else(self):
        self.assertEqual(tui.mouse_kind(curses.BUTTON4_PRESSED), "up")
        self.assertEqual(tui.mouse_kind(curses.BUTTON5_PRESSED), "down")
        self.assertIsNone(tui.mouse_kind(curses.BUTTON3_PRESSED))  # right button
        self.assertIsNone(tui.mouse_kind(curses.BUTTON1_RELEASED))
        self.assertIsNone(tui.mouse_kind(0))


class BrowserClickTests(unittest.TestCase):
    def test_clicking_a_role_selects_it(self):
        st, _ = drawn()
        self.assertEqual(st.mouse("click", 10, BODY_TOP + 2 * tui.ITEM_ROWS + 1), None)  # the third role's second line
        self.assertEqual(st.cursor, 2)

    def test_the_whole_three_row_slot_counts_including_the_gap_line(self):
        st, _ = drawn()
        for dy in range(tui.ITEM_ROWS):
            st.cursor = 0
            st.mouse("click", 5, BODY_TOP + tui.ITEM_ROWS + dy)
            self.assertEqual(st.cursor, 1, dy)

    def test_a_quick_second_click_on_the_same_role_opens_it(self):
        st, _ = drawn()
        y = BODY_TOP + tui.ITEM_ROWS
        self.assertIsNone(st.mouse("click", 5, y, now=100.0))
        event = st.mouse("click", 5, y, now=100.2)
        self.assertEqual((event[0], event[1]["url"]), ("open", "https://x/1"))

    def test_a_slow_second_click_or_a_different_role_does_not_open(self):
        st, _ = drawn()
        y1, y2 = BODY_TOP + tui.ITEM_ROWS, BODY_TOP + 2 * tui.ITEM_ROWS
        st.mouse("click", 5, y1, now=100.0)
        self.assertIsNone(st.mouse("click", 5, y1, now=101.0))  # too slow: counts as a fresh first click
        self.assertIsNone(st.mouse("click", 5, y2, now=101.1))  # a different role
        self.assertIsNone(st.mouse("click", 5, y1, now=101.2))
        self.assertEqual(st.cursor, 1)

    def test_a_double_click_does_not_chain_into_a_triple(self):
        st, _ = drawn()
        y = BODY_TOP
        st.mouse("click", 5, y, now=10.0)
        self.assertIsNotNone(st.mouse("click", 5, y, now=10.1))
        self.assertIsNone(st.mouse("click", 5, y, now=10.2))

    def test_clicking_the_link_or_the_open_line_in_the_details_panel_opens_it(self):
        st, rows = drawn()
        text = tui.plain(rows)
        for needle in ("https://x/0", "enter open in Chrome"):
            y = next(i for i, line in enumerate(text) if needle in line)
            x = text[y].index(needle)
            event = st.mouse("click", x, y)
            self.assertEqual((event[0], event[1]["url"]), ("open", "https://x/0"), needle)

    def test_clicking_elsewhere_in_the_panel_or_on_blank_space_does_nothing(self):
        st, rows = drawn()
        text = tui.plain(rows)
        y = next(i for i, line in enumerate(text) if "Co0" in line and "Berlin" in line and i > BODY_TOP + 1)
        self.assertIsNone(st.mouse("click", 80, 5))  # inside the details panel, not on the link
        self.assertIsNone(st.mouse("click", 119, 0))
        self.assertIsNone(st.mouse("click", 0, 31 + 5))  # off the screen
        self.assertEqual(st.cursor, 0)

    def test_the_key_hints_along_the_bottom_are_buttons(self):
        st, rows = drawn()
        footer = tui.plain(rows)[-1]
        for label, check in (("region", lambda s: s.region == "emea"), ("new", lambda s: s.new_only), ("sort", lambda s: s.sort == "fit")):
            st.region, st.new_only, st.sort = "", False, "best"
            x = footer.index(label)
            self.assertIsNone(st.mouse("click", x, H - 1), label)
            self.assertTrue(check(st), label)

    def test_clicking_enter_open_in_the_footer_opens_the_selected_role(self):
        st, rows = drawn()
        footer = tui.plain(rows)[-1]
        event = st.mouse("click", footer.index("enter"), H - 1)
        self.assertEqual(event[0], "open")

    def test_quit_applied_and_exclude_hints_return_their_events(self):
        st, rows = drawn()
        footer = tui.plain(rows)[-1]
        self.assertEqual(st.mouse("click", footer.index("quit"), H - 1), ("quit", None))
        self.assertEqual(st.mouse("click", footer.index("applied"), H - 1)[0], "applied")
        self.assertEqual(st.mouse("click", footer.index("exclude"), H - 1)[0], "exclude")

    def test_the_wheel_moves_the_selection_and_stops_at_the_ends(self):
        st, _ = drawn()
        for _ in range(3):
            st.mouse("down", 5, 5)
        self.assertEqual(st.cursor, 3)
        for _ in range(10):
            st.mouse("up", 5, 5)
        self.assertEqual(st.cursor, 0)
        for _ in range(50):
            st.mouse("down", 5, 5)
        self.assertEqual(st.cursor, len(JOBS) - 1)

    def test_the_wheel_is_ignored_while_typing_a_filter_and_a_click_dismisses_help(self):
        st, _ = drawn()
        st.mode = "filter"
        st.mouse("down", 5, 5)
        self.assertEqual(st.cursor, 0)
        st.mode = "help"
        st.mouse("click", 5, 5)
        self.assertEqual(st.mode, "list")

    def test_clicking_a_role_while_filtering_leaves_filter_mode_and_selects_it(self):
        st, _ = drawn()
        st.mode = "filter"
        st.mouse("click", 5, BODY_TOP + tui.ITEM_ROWS)
        self.assertEqual((st.mode, st.cursor), ("list", 1))

    def test_clicking_enter_apply_in_filter_mode_finishes_the_filter(self):
        st, rows = drawn(mode="filter")
        footer = tui.plain(rows)[-1]
        st.mouse("click", footer.index("enter"), H - 1)
        self.assertEqual(st.mode, "list")

    def test_clicks_map_to_the_right_role_after_scrolling(self):
        st, _ = drawn()
        for _ in range(10):
            st.press("j")
        tui.frame(st, H, W)  # scroll_into_view moves the window down
        self.assertGreater(st.top, 0)
        st.mouse("click", 5, BODY_TOP)  # the first visible slot
        self.assertEqual(st.cursor, st.top)

    def test_hits_are_rebuilt_each_frame_and_stay_on_screen(self):
        st = tui.State(JOBS, "Chrome")
        for width, height in ((80, 24), (120, 32), (200, 50), (50, 12)):
            tui.frame(st, height, width)
            tui.frame(st, height, width)
            self.assertLessEqual(len(st.hits), len(JOBS) + 20)  # not growing frame after frame
            for y0, y1, x0, x1, action, _ in st.hits:
                self.assertTrue(0 <= y0 <= y1 < height or action == "key", (action, y0, y1, height))

    def test_a_narrow_terminal_has_no_details_panel_targets_but_still_works(self):
        st, rows = drawn(width=80, height=24)
        self.assertFalse([h for h in st.hits if h[4] == "open"])
        st.mouse("click", 3, 3 + tui.ITEM_ROWS)
        self.assertEqual(st.cursor, 1)

    def test_an_empty_list_has_nothing_to_click(self):
        st, _ = drawn(jobs=[])
        self.assertIsNone(st.mouse("click", 5, 5))
        self.assertIsNone(st.mouse("click", 5, H - 1))


class MenuClickTests(unittest.TestCase):
    OPTIONS = [("a", "One"), ("b", "Two"), ("c", "Three")]

    def test_every_option_row_is_recorded(self):
        hits = []
        rows = tui.select_frame("Job Radar", "Pick", self.OPTIONS, 0, 20, 60, hits=hits)
        self.assertEqual([h[5] for h in hits], [0, 1, 2])
        for (y0, y1, x0, x1, action, i) in hits:
            self.assertEqual(action, "opt")
            self.assertIn(self.OPTIONS[i][1], tui.plain(rows)[y0])

    def run_select(self, events, preview=False, options=None):
        class Win:
            def __init__(self, keys):
                self.keys = list(keys)

            def getmaxyx(self):
                return 30, 100

            def erase(self): pass
            def addstr(self, *a): pass
            def refresh(self): pass

            def get_wch(self):
                return self.keys.pop(0)

        class Screen:
            theme, attrs, closed = "glow", {}, 0

            def __init__(self, keys):
                self.win, self.themes = Win(keys), []

            def open(self):
                return self.win

            def set_theme(self, name):
                self.theme = name
                self.themes.append(name)

            def close(self):
                pass
        screen = Screen([e[0] for e in events])
        queue = [e[1] for e in events if e[1] is not None]
        with mock.patch.object(curses, "getmouse", side_effect=lambda: queue.pop(0)):
            got = tui.run_select("Job Radar", "Pick", options or self.OPTIONS, "a", screen=screen, preview=preview)
        return got, screen

    def test_clicking_an_option_chooses_it(self):
        # layout: row 0 badge, 1 blank, 2 prompt, 3 blank, 4.. options
        got, _ = self.run_select([(curses.KEY_MOUSE, (0, 5, 5, 0, curses.BUTTON1_CLICKED))])
        self.assertEqual(got, "b")

    def test_the_wheel_moves_and_enter_chooses(self):
        got, _ = self.run_select([(curses.KEY_MOUSE, (0, 1, 1, 0, curses.BUTTON5_PRESSED)),
                                  (curses.KEY_MOUSE, (0, 1, 1, 0, curses.BUTTON5_PRESSED)),
                                  (curses.KEY_MOUSE, (0, 1, 1, 0, curses.BUTTON5_PRESSED)), (10, None)])
        self.assertEqual(got, "c")  # clamped at the last option, no wrap

    def test_clicking_blank_space_changes_nothing(self):
        got, _ = self.run_select([(curses.KEY_MOUSE, (0, 5, 0, 0, curses.BUTTON1_CLICKED)), (10, None)])
        self.assertEqual(got, "a")

    def test_in_the_theme_picker_the_wheel_previews_and_a_click_keeps_the_theme(self):
        themes = [(k, v["label"]) for k, v in tui.THEMES.items()]
        got, screen = self.run_select([(curses.KEY_MOUSE, (0, 1, 1, 0, curses.BUTTON5_PRESSED)),
                                       (curses.KEY_MOUSE, (0, 5, 4 + 2, 0, curses.BUTTON1_CLICKED))], preview=True, options=themes)
        self.assertEqual(got, "tokyonight")  # options start on row 4, so row 6 is the third: glow, catppuccin, tokyonight
        self.assertEqual(screen.themes[0], "catppuccin")
        self.assertEqual(screen.theme, "tokyonight")


class MouseSettingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "settings.json"
        patcher = mock.patch.object(tui, "settings_file", lambda: self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("JOBRADAR_MOUSE", None)

    def test_on_by_default(self):
        self.assertTrue(tui.mouse_enabled())

    def test_the_setting_turns_it_off(self):
        settings.save(self.path, {"mouse": False})
        self.assertFalse(tui.mouse_enabled())

    def test_the_environment_beats_the_setting_both_ways(self):
        settings.save(self.path, {"mouse": False})
        os.environ["JOBRADAR_MOUSE"] = "1"
        self.assertTrue(tui.mouse_enabled())
        settings.save(self.path, {"mouse": True})
        for off in ("0", "off", "No", "false"):
            os.environ["JOBRADAR_MOUSE"] = off
            self.assertFalse(tui.mouse_enabled(), off)

    def test_a_bad_value_in_the_file_falls_back_to_on(self):
        self.path.write_text('{"mouse": "maybe"}')
        self.assertTrue(tui.mouse_enabled())


if __name__ == "__main__":
    unittest.main()
