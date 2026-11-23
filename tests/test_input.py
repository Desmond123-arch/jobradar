"""Text questions stay inside the full-screen session, and a search gets a clean screen of its own."""

import curses
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import tui  # noqa: E402


class FakeWin:
    def __init__(self, keys, width=80):
        self.keys, self.width = list(keys), width

    def getmaxyx(self):
        return 24, self.width

    def erase(self): pass
    def addstr(self, *a): pass
    def refresh(self): pass

    def get_wch(self):
        return self.keys.pop(0)


class FakeScreen:
    theme, attrs = "glow", {}

    def __init__(self, keys, width=80):
        self.win, self.closed = FakeWin(keys, width), 0

    def open(self):
        return self.win

    def close(self):
        self.closed += 1


def ask(keys, **kw):
    screen = FakeScreen(keys, kw.pop("width", 80))
    return tui.run_input("Job Radar", "Days?", screen=screen, **kw), screen


class RunInputTests(unittest.TestCase):
    def test_typing_and_enter(self):
        self.assertEqual(ask(list("14") + [10])[0], "14")
        self.assertEqual(ask(list("Accra, Ghana") + ["\r"])[0], "Accra, Ghana")

    def test_enter_on_nothing_returns_empty_so_a_default_can_apply(self):
        self.assertEqual(ask([10])[0], "")

    def test_escape_cancels(self):
        self.assertIsNone(ask(list("14") + [27])[0])

    def test_backspace_in_every_form_and_ctrl_u(self):
        self.assertEqual(ask(list("145") + [127, 10])[0], "14")
        self.assertEqual(ask(list("145") + [8, 10])[0], "14")
        self.assertEqual(ask(list("145") + [curses.KEY_BACKSPACE, 10])[0], "14")
        self.assertEqual(ask(list("145") + [21] + list("7") + [10])[0], "7")
        self.assertEqual(ask([127, 127, 10])[0], "")  # nothing to delete is fine

    def test_unicode_and_spaces_are_kept_and_control_keys_are_not(self):
        self.assertEqual(ask(list("São Paulo") + [10])[0], "São Paulo")
        self.assertEqual(ask(["a", curses.KEY_UP, curses.KEY_DOWN, curses.KEY_RESIZE, "b", 10])[0], "ab")

    def test_mouse_events_are_drained_and_ignored(self):
        with mock.patch.object(curses, "getmouse", return_value=(0, 1, 1, 0, curses.BUTTON1_CLICKED)) as g:
            self.assertEqual(ask(["a", curses.KEY_MOUSE, "b", 10])[0], "ab")
            self.assertEqual(g.call_count, 1)

    def test_it_starts_from_given_text(self):
        self.assertEqual(ask([curses.KEY_BACKSPACE, "9", 10], text="14")[0], "19")

    def test_a_shared_screen_stays_open_for_the_next_question(self):
        _, screen = ask(["a", 10])
        self.assertEqual(screen.closed, 0)


class InputFrameTests(unittest.TestCase):
    def render(self, text="", placeholder="", w=60, h=14, notes=()):
        return tui.plain(tui.input_frame("Job Radar", "How many days?", text, placeholder, h, w, notes))

    def test_shows_the_prompt_the_field_and_the_key_hints(self):
        rows = "\n".join(self.render(text="14"))
        self.assertIn("How many days?", rows)
        self.assertIn("> 14█", rows)
        self.assertIn("enter confirm", rows)
        self.assertIn("esc cancel", rows)

    def test_an_empty_field_shows_the_placeholder_after_the_cursor(self):
        self.assertIn("> █15  (press Enter to keep it)", "\n".join(self.render(placeholder="15  (press Enter to keep it)")))

    def test_a_long_entry_scrolls_to_show_its_end_and_never_overflows(self):
        long = "x" * 200 + "END"
        for w in (30, 60, 100):
            for row in tui.input_frame("Job Radar", "p", long, "", 14, w):
                self.assertLessEqual(tui.spans_width(row), w)
            self.assertIn("END█", "\n".join(self.render(text=long, w=w)))

    def test_notes_appear_under_the_prompt(self):
        self.assertIn("Enter a whole number", "\n".join(self.render(notes=["  Enter a whole number, like 14."])))

    def test_every_size_fits(self):
        for h in (5, 8, 24):
            for w in (20, 80):
                self.assertLessEqual(len(tui.input_frame("t", "p", "abc", "", h, w)), h)


class FancyUITextTests(unittest.TestCase):
    def ui(self, answers, pauses=None, printed=None):
        ui = cli.FancyUI(Path("."), inp=lambda p: (pauses if pauses is not None else []).append(p) or "",
                         out=(printed if printed is not None else []).append, runner=lambda a: 0)
        ui.screen = FakeScreen([])
        self.answers = list(answers)
        return ui

    def patched(self):
        return mock.patch.object(tui, "run_input", lambda title, prompt, screen=None, placeholder="", notes=(), text="":
                                 self.calls.append((prompt, placeholder, list(notes))) or self.answers.pop(0))

    def setUp(self):
        self.calls = []

    def test_ask_stays_in_the_session_and_tidies_the_prompt(self):
        ui = self.ui(["  Accra, Ghana  "])
        with self.patched():
            self.assertEqual(ui.ask("\nWhere would you work on-site? (blank = anywhere):"), "Accra, Ghana")
        self.assertEqual(self.calls[0][0], "Where would you work on-site? (blank = anywhere)")
        self.assertEqual(ui.screen.closed, 0)  # it never left the full-screen session

    def test_escape_goes_back(self):
        ui = self.ui([None])
        with self.patched(), self.assertRaises(cli.Back):
            ui.ask("Anything")

    def test_number_keeps_the_default_on_enter_and_rejects_nonsense_with_a_note(self):
        ui = self.ui([""])
        with self.patched():
            self.assertEqual(ui.number("Posted within how many days?", 15), 15)
        self.assertIn("15", self.calls[0][1])
        self.calls.clear()
        ui = self.ui(["abc", "-3", "0", "14"])
        with self.patched():
            self.assertEqual(ui.number("Days?", 15), 14)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[0][2], [])
        self.assertTrue(any("whole number" in n for n in self.calls[1][2]))  # the reason is shown on the retry

    def test_a_search_is_cleared_headed_and_followed_by_a_pause(self):
        printed, pauses = [], []
        ui = self.ui([], pauses, printed)
        ui._real_runner = lambda argv: printed.append(("ran", argv)) or 130
        ui.say("First discovery scan")
        self.assertEqual(ui.runner(["--open"]), 130)  # the search's own exit code is passed back
        self.assertTrue(printed[0].startswith("\x1b[2J\x1b[H"))
        self.assertEqual(printed[1:], ["First discovery scan", ("ran", ["--open"])])
        self.assertEqual(len(pauses), 1)
        self.assertEqual(ui.screen.closed, 1)

    def test_the_pause_survives_ctrl_d_and_ctrl_c(self):
        for error in (EOFError, KeyboardInterrupt):
            ui = cli.FancyUI(Path("."), inp=mock.Mock(side_effect=error), out=lambda t: None, runner=lambda a: 0)
            ui.screen = FakeScreen([])
            ui._real_runner = lambda argv: 0
            self.assertEqual(ui.runner([]), 0)


if __name__ == "__main__":
    unittest.main()
