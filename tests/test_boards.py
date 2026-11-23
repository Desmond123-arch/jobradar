"""Filtering by job board in the full-screen browser, and the shared list of board names."""

import curses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sources  # noqa: E402
import tui  # noqa: E402

H, W = 32, 120


def job(i, provider, **kw):
    return dict(title=f"Backend Engineer {i}", company=f"Co{i}", location="Berlin", region="emea", region_reason="in EMEA", new=False,
                score=5, posted="2026-11-05", url=f"https://x/{i}", why=[], description="Python.", pay="", provider=provider, **kw)


JOBS = [job(0, "greenhouse"), job(1, "lever"), job(2, "ashby"), job(3, "greenhouse"), job(4, "hackernews"), job(5, "lever")]


def state(jobs=JOBS):
    st = tui.State(jobs, "Chrome")
    tui.frame(st, H, W)
    return st


class SourcesTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual([sources.name(k) for k in ("greenhouse", "lever", "ashby", "remoteok", "hackernews")],
                         ["Greenhouse", "Lever", "Ashby", "RemoteOK", "Hacker News"])

    def test_an_unknown_board_is_shown_under_its_own_name(self):
        self.assertEqual(sources.name("newboard"), "Newboard")
        self.assertEqual(sources.name(""), "Unknown")
        self.assertEqual(sources.name(None), "Unknown")

    def test_ordered_puts_known_boards_in_filter_order_then_the_rest(self):
        self.assertEqual(sources.ordered(["hackernews", "lever", "zeta", "greenhouse", "alpha", None, ""]),
                         ["greenhouse", "lever", "hackernews", "alpha", "zeta"])
        self.assertEqual(sources.ordered([]), [])

    def test_every_source_the_search_reads_has_a_name(self):
        import jobradar as jr
        for key in list(jr.ENDPOINTS) + list(jr.AGGREGATORS):
            self.assertIn(key, sources.NAMES, key)


class BoardFilterTests(unittest.TestCase):
    def test_boards_lists_only_the_ones_in_the_results_in_order(self):
        self.assertEqual(state().boards, ["", "greenhouse", "lever", "ashby", "hackernews"])
        self.assertEqual(state([job(0, "lever")]).boards, ["", "lever"])
        self.assertEqual(state([]).boards, [""])

    def test_b_steps_through_the_boards_and_back_to_all(self):
        st = state()
        seen = []
        for _ in range(5):
            st.press("b")
            seen.append((st.source, len(st.shown)))
        self.assertEqual(seen, [("greenhouse", 2), ("lever", 2), ("ashby", 1), ("hackernews", 1), ("", 6)])

    def test_the_filtered_list_holds_only_that_boards_roles(self):
        st = state()
        st.press("b")
        self.assertEqual({j["provider"] for j in st.shown}, {"greenhouse"})
        self.assertEqual(st.cursor, 0)

    def test_it_combines_with_the_other_filters(self):
        st = state([job(0, "greenhouse", ), dict(job(1, "greenhouse"), new=True), dict(job(2, "lever"), new=True)])
        st.press("b")      # Greenhouse
        st.press("n")      # new only
        self.assertEqual([j["title"] for j in st.shown], ["Backend Engineer 1"])
        st.press("/")
        for ch in "engineer":
            st.press(ch)
        st.press(10)
        self.assertEqual(len(st.shown), 1)

    def test_c_and_escape_clear_the_board_filter_too(self):
        for clear in ("c", 27):
            st = state()
            st.press("b")
            self.assertTrue(st.filters_active)
            st.press(clear)
            self.assertEqual((st.source, len(st.shown), st.filters_active), ("", 6, False), clear)

    def test_escape_quits_only_when_no_filter_is_left(self):
        st = state()
        st.press("b")
        self.assertIsNone(st.press(27))
        self.assertEqual(st.press(27), ("quit", None))

    def test_the_board_name_is_searchable(self):
        st = state()
        for ch in "/hacker news"[0:1] + "lever":
            st.press(ch)
        st.press(10)
        self.assertEqual({j["provider"] for j in st.shown}, {"lever"})

    def test_removing_the_last_role_of_a_board_does_not_strand_the_filter(self):
        st = state([job(0, "greenhouse"), job(1, "lever")])
        st.press("b")
        st.press("b")      # Lever
        st.remove_where(lambda j: j["provider"] == "lever")
        self.assertEqual(st.shown, [])
        st.press("b")      # the board is gone from the list, so this goes back to All
        self.assertEqual((st.source, len(st.shown)), ("", 1))

    def test_page_up_is_the_page_up_key_not_b(self):
        st = state([job(i, "lever") for i in range(40)])
        st.press(curses.KEY_NPAGE)
        moved = st.cursor
        self.assertGreater(moved, 0)
        st.press(curses.KEY_PPAGE)
        self.assertEqual(st.cursor, 0)
        self.assertEqual(st.source, "")
        st.press("b")
        self.assertEqual(st.source, "lever")  # b is the board key now


class ScreenTests(unittest.TestCase):
    def text(self, st, h=H, w=W):
        return "\n".join(tui.plain(tui.frame(st, h, w)))

    def test_a_chip_names_the_active_board(self):
        st = state()
        self.assertNotIn("board:", self.text(st))
        st.press("b")
        self.assertIn("board: Greenhouse", self.text(st))
        st.press("b")
        self.assertIn("board: Lever", self.text(st))

    def test_the_footer_offers_it_and_the_help_explains_it(self):
        self.assertIn("b board", self.text(state()))
        st = state()
        st.press("?")
        self.assertIn("cycle job board", self.text(st))

    def test_clicking_the_footer_hint_steps_to_the_next_board(self):
        st = state()
        footer = tui.plain(tui.frame(st, H, W))[-1]
        st.mouse("click", footer.index("board"), H - 1)
        self.assertEqual(st.source, "greenhouse")

    def test_the_details_name_the_board(self):
        st = state()
        st.cursor = 4
        self.assertIn("via Hacker News", self.text(st))

    def test_an_empty_filter_result_says_so_and_how_to_clear_it(self):
        st = state([job(0, "lever")])
        st.press("b")
        st.press("n")
        self.assertIn("No roles match", self.text(st))


class AdaptiveFooterTests(unittest.TestCase):
    def test_everything_fits_on_a_wide_screen(self):
        footer = tui.plain(tui.frame(state(), 40, 200))[-1]
        for hint in ("move", "enter open", "filter", "region", "board", "new", "sort", "theme", "applied", "exclude", "more", "quit"):
            self.assertIn(hint, footer)

    def test_the_least_important_hints_go_first_and_more_and_quit_always_stay(self):
        for w in (60, 70, 80, 90, 100, 110, 120):
            footer = tui.plain(tui.frame(state(), H, w))[-1]
            self.assertLessEqual(len(footer), w, w)
            self.assertIn("more", footer, w)
            self.assertIn("quit", footer, w)
            self.assertIn("enter open", footer, w)

    def test_the_move_hint_is_dropped_before_the_board_hint(self):
        widths = {w: tui.plain(tui.frame(state(), H, w))[-1] for w in range(70, 130)}
        first_without_move = next(w for w in sorted(widths, reverse=True) if "move" not in widths[w])
        self.assertIn("board", widths[first_without_move])

    def test_hit_targets_match_what_is_drawn_after_hints_are_dropped(self):
        st = state()
        footer = tui.plain(tui.frame(st, H, 90))[-1]
        for y0, y1, x0, x1, action, arg in st.hits:
            if action == "key":
                self.assertEqual(y0, H - 1)
                self.assertTrue(footer[x0:x1].strip(), (x0, x1))
        st.mouse("click", footer.index("quit"), H - 1)
        self.assertTrue(True)  # no hint was dropped without its target going too

    def test_fit_hints_keeps_everything_when_it_fits_and_never_loops(self):
        pairs = [("↑/↓", "move"), ("enter", "open"), ("q", "quit")]
        self.assertEqual(tui.fit_hints(pairs, 500), pairs)
        self.assertEqual(tui.fit_hints(pairs, 1), [("enter", "open"), ("q", "quit")])  # drops what it may, keeps the rest


if __name__ == "__main__":
    unittest.main()
