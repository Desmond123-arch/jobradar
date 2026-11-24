"""CLI tests: scripted keystrokes in, files and printed text out. No network, no real browser."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import settings  # noqa: E402

JOBS = [
    {"company": "Acme", "title": "Backend Engineer", "url": "https://a/1", "location": "Berlin", "region": "emea", "provider": "greenhouse",
     "region_reason": "location in EMEA: Berlin", "posted": "2026-11-05T10:00:00Z", "pay": "", "score": 5,
     "why": ["engineering role"], "new": True, "description": "Python and Go."},
    {"company": "Beta Co", "title": "Frontend Engineer", "url": "https://b/2", "location": "Remote - Worldwide",
     "region": "worldwide", "region_reason": "location: Remote - Worldwide", "posted": "2026-11-06", "pay": "",
     "score": 3, "why": [], "new": False, "description": "", "provider": "lever"},
]


def row(name):
    """The number to type for a row on the settings screen, found by name so adding a setting never breaks a test."""
    return str((list(cli.FIELDS) + ["reset", "done"]).index(name) + 1)


class Session:
    """Feeds scripted answers and records everything printed."""

    def __init__(self, answers, root, runner=None):
        self.answers, self.lines, self.opened, self.runs = list(answers), [], [], []
        self.root, self.cv_dirs = root, None
        self.runner = runner or (lambda argv: self.runs.append(argv) or 0)

    def go(self):
        def inp(prompt):
            self.lines.append(prompt)
            if not self.answers:
                raise EOFError
            return self.answers.pop(0)
        return cli.main(inp=inp, out=self.lines.append, root=self.root, runner=self.runner, opener=self.opened.append, cv_dirs=self.cv_dirs)

    @property
    def text(self):
        return "\n".join(self.lines)


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "jobs.json").write_text(json.dumps(JOBS))

    def test_search_wizard_builds_flags(self):
        s = Session(["1", "2", "2", "4 5", "", "3", "y", "n", "14", "y", "n", "n", "7"], self.root)
        # search, change a few answers, cv roles, tick Europe and Middle East then done, all sources, drop senior,
        # hide locked, 14 days, run, don't save, no browse, quit
        s.go()
        self.assertEqual(s.runs, [["--no-settings", "--roles", "cv", "--remote", "worldwide,africa,emea,europe,middle-east,my-timezone,unclear", "--aggregators", "--discover", "--no-senior", "--days", "14"]])
        self.assertFalse((self.root / "settings.json").exists())
        self.assertIn("python jobradar.py --roles cv --remote worldwide,africa,emea,europe,middle-east,my-timezone,unclear --aggregators --discover --no-senior --days 14", s.text)

    def test_search_runs_with_the_saved_settings_and_asks_nothing_else(self):
        settings.save(self.root / "settings.json", {"roles": "cv", "remote": ["worldwide", "africa", "emea", "europe", "middle-east", "my-timezone", "unclear"], "sources": "all"})
        s = Session(["", "", "n", "7"], self.root)  # Search, Search now (the default), no browse, quit
        s.go()
        self.assertEqual(s.runs, [["--no-settings", "--roles", "cv", "--remote", "worldwide,africa,emea,europe,middle-east,my-timezone,unclear", "--aggregators", "--discover"]])
        for question in ("Which roles?", "Which remote roles do you want?", "Which companies?", "Posted within how many days?", "Run it now?"):
            self.assertNotIn(question, s.text)  # none of the questionnaire
        self.assertIn("Search with your saved settings?", s.text)

    def test_the_summary_shows_what_will_be_searched(self):
        settings.save(self.root / "settings.json", {"roles": "web", "days": 15, "timezone": "UTC+0", "tz_range": 8, "sources": "agg",
                                                    "remote": ["worldwide", "africa"], "no_senior": True})
        s = Session(["", "", "n", "7"], self.root)
        s.go()
        for expected in ("Roles", "web and software", "Remote roles", "Worldwide, Africa", "Time zone", "UTC+0",
                         "within 8h", "Companies", "plus job sites", "Drop senior titles", "yes", "Look back", "15"):
            self.assertIn(expected, s.text)

    def test_every_search_after_the_first_asks_nothing_new(self):
        settings.save(self.root / "settings.json", {"roles": "web", "days": 15})
        s = Session(["", "", "n", "", "", "n", "7"], self.root)  # two searches in a row, each: Search, Search now, no browse
        s.go()
        self.assertEqual(len(s.runs), 2)
        self.assertEqual(s.runs[0], s.runs[1])
        self.assertEqual(s.runs[0], ["--no-settings", "--roles", "web", "--days", "15"])

    def test_changing_things_for_one_search_still_works_and_is_the_old_questionnaire(self):
        settings.save(self.root / "settings.json", {"roles": "cv", "remote": ["worldwide", "africa", "emea", "europe", "middle-east", "my-timezone", "unclear"], "sources": "all"})
        s = Session(["", "2"] + [""] * 7 + ["n", "7"], self.root)  # Search, change, accept every default, run, no browse
        s.go()
        self.assertEqual(s.runs, [["--no-settings", "--roles", "cv", "--remote", "worldwide,africa,emea,europe,middle-east,my-timezone,unclear", "--aggregators", "--discover"]])
        self.assertIn("Which roles?", s.text)
        self.assertIn("Run it now?", s.text)

    def test_a_one_off_change_is_not_saved_unless_you_say_so(self):
        path = self.root / "settings.json"
        settings.save(path, {"days": 15})
        Session(["1", "2", "", "", "", "", "", "30", "y", "n", "n", "7"], self.root).go()  # change days to 30, run, don't save
        self.assertEqual(settings.load(path)["days"], 15)
        Session(["1", "2", "", "", "", "", "", "30", "y", "y", "n", "7"], self.root).go()  # and now do save
        self.assertEqual(settings.load(path)["days"], 30)

    def test_editing_the_saved_settings_from_the_search_question_comes_back_to_it(self):
        path = self.root / "settings.json"
        s = Session(["1", "3", row("days"), "7", row("done"), "", "n", "7"], self.root)  # Search, edit settings, days=7, Done, Search now
        s.go()
        self.assertEqual(settings.load(path)["days"], 7)
        self.assertEqual(s.runs, [["--no-settings", "--days", "7"]])
        self.assertEqual(s.text.count("Search with your saved settings?"), 2)  # asked again after the edit, with the new value

    def test_wizard_can_save_answers_as_defaults(self):
        s = Session(["1", "2", "3", "", "", "", "", "", "y", "y", "n", "7"], self.root)  # change, roles=web, run, save, no browse
        s.go()
        self.assertEqual(settings.load(self.root / "settings.json")["roles"], "web")
        self.assertEqual(s.runs, [["--no-settings", "--roles", "web"]])

    def test_declining_to_run_does_nothing(self):
        s = Session(["1", "2", "4", "", "1", "", "", "", "n", "7"], self.root)  # change, roles=any, remote done, ..., run? no
        s.go()
        self.assertEqual(s.runs, [])

    def test_browse_lists_filters_and_pages(self):
        s = Session(["2", "", "f", "1", "frontend", "q", "7"], self.root)
        s.go()
        self.assertIn("2 of 2 roles", s.text)
        self.assertIn("1 of 2 roles · filters: text \"frontend\"", s.text)

    def test_filter_new_only(self):
        s = Session(["2", "f", "4", "q", "7"], self.root)
        s.go()
        self.assertIn("1 of 2 roles · filters: new only", s.text)

    def test_the_plain_browser_filters_by_job_board(self):
        # browse, filter, "Job board" (3rd), then the 3rd choice: Any, Greenhouse, Lever -> Lever
        s = Session(["2", "f", "3", "3", "q", "7"], self.root)
        s.go()
        self.assertIn("Job board", s.text)
        self.assertIn("1 of 2 roles · filters: Lever", s.text)

    def test_the_plain_browser_lists_only_the_boards_in_the_results_and_searches_by_name(self):
        s = Session(["2", "f", "3", "1", "f", "1", "greenhouse", "q", "7"], self.root)
        s.go()
        options = s.text.split("\nJob board\n")[1].split(">")[0]  # the list's own heading, not the menu entry
        self.assertIn("Greenhouse", options)
        self.assertIn("Lever", options)
        self.assertNotIn("Ashby", options)
        self.assertIn("1 of 2 roles · filters: text \"greenhouse\"", s.text)

    def test_clearing_the_filters_clears_the_board_too(self):
        s = Session(["2", "f", "3", "2", "f", "5", "q", "7"], self.root)  # board Greenhouse, then Clear all filters
        s.go()
        self.assertIn("filters: Greenhouse", s.text)
        self.assertIn("2 of 2 roles · filters: none", s.text)

    def test_open_marks_applied_and_removes_role(self):
        s = Session(["2", "1", "o", "a", "q", "7"], self.root)
        s.go()
        self.assertEqual(s.opened, ["https://a/1"])
        self.assertEqual((self.root / "applied.txt").read_text(), "Acme | Backend Engineer\n")
        self.assertIn("1 of 1 roles", s.text.split("saved to applied.txt")[1])

    def test_exclude_company_removes_its_roles(self):
        s = Session(["2", "1", "x", "1", "x", "q", "7"], self.root)  # exclude Acme, then Beta Co
        s.go()
        self.assertEqual((self.root / "exclude.txt").read_text(), "Acme\nBeta Co\n")
        self.assertIn("0 of 0 roles", s.text)

    def test_excluding_twice_writes_once(self):
        ui = cli.UI(self.root, out=lambda t: None)
        cli.exclude_company(ui, JOBS[0])
        cli.exclude_company(ui, JOBS[0])
        self.assertEqual((self.root / "exclude.txt").read_text(), "Acme\n")

    def test_browse_without_results(self):
        (self.root / "jobs.json").unlink()
        s = Session(["2", "7"], self.root)
        s.go()
        self.assertIn("No results yet", s.text)

    def test_add_company_validates_and_dedupes(self):
        s = Session(["3", "https://example.com/careers", "3", "https://jobs.lever.co/zeta | Zeta", "3", "lever:zeta", "7"], self.root)
        s.go()
        self.assertIn("can't tell which job board", s.text)
        self.assertEqual((self.root / "companies.txt").read_text(), "https://jobs.lever.co/zeta | Zeta\n")
        self.assertIn("already in companies.txt", s.text)

    def test_open_report(self):
        (self.root / "report.html").write_text("<html></html>")
        s = Session(["4", "7"], self.root)
        s.go()
        report = self.root / "report.html"
        self.assertEqual(len(s.opened), 1)
        self.assertTrue(s.opened[0] in (report.as_uri(),) or s.opened[0].startswith("\\\\"), s.opened)  # file:// or a Windows path under WSL

    def test_settings_screen_changes_and_saves_each_kind_of_value(self):
        path = self.root / "settings.json"
        # open settings, then change: roles -> 4th choice (every role), remote roles -> also tick Europe and Middle East, drop senior -> yes,
        # days -> 14, browser -> text, theme -> 7th (Nord); then Done and quit
        s = Session(["5", row("roles"), "4", row("remote"), "4 5", "", row("no_senior"), "y", row("days"), "14", row("browser"),
                     "firefox {url}", row("theme"), "7", row("done"), "7"], self.root)
        s.go()
        got = settings.load(path)
        self.assertEqual((got["roles"], got["remote"], got["no_senior"], got["days"], got["browser"], got["theme"]),
                         ("any", ["worldwide", "africa", "emea", "europe", "middle-east", "my-timezone", "unclear"], True, 14, "firefox {url}", "nord"))
        self.assertIn("Roles", s.text)
        self.assertIn("every role", s.text)  # the list shows the current value

    def test_settings_keep_unknown_keys_and_show_current_values(self):
        path = self.root / "settings.json"
        path.write_text(json.dumps({"roles": "web", "note": "mine"}))
        s = Session(["5", row("done"), "7"], self.root)
        s.go()
        self.assertIn("web and software", s.text)
        self.assertEqual(json.loads(path.read_text())["note"], "mine")

    def test_cv_picker_lists_found_files_and_reads_skills(self):
        cvdir = self.root / "docs"
        cvdir.mkdir()
        (cvdir / "My_CV_General.txt").write_text("Python, Docker and PostgreSQL. React and TypeScript.")
        (cvdir / "notes.txt").write_text("not a cv")
        s = Session(["5", row("cv"), "1", row("done"), "7"], self.root)
        s.cv_dirs = [cvdir]
        s.go()
        saved = settings.load(self.root / "settings.json")["cv"]
        self.assertEqual(saved, str(cvdir / "My_CV_General.txt"))
        self.assertIn("My_CV_General.txt", s.text)
        self.assertNotIn("notes.txt", s.text)
        self.assertIn("Python", s.text)

    def test_cv_picker_typed_path_with_spaces_and_bad_files(self):
        cvfile = self.root / "my cv.txt"
        cvfile.write_text("Python and Go developer. Docker, AWS.")
        s = Session(["5", row("cv"), "1", str(cvfile), row("cv"), "1", "/nope/missing.pdf", row("cv"), "1", str(self.root / "empty.txt"),
                     row("done"), "7"], self.root)
        (self.root / "empty.txt").write_text("nothing relevant here")
        s.cv_dirs = []  # no files found: option 1 is "type a path"
        s.go()
        self.assertIn("can't find a file at /nope/missing.pdf", s.text)
        self.assertIn("found no known skills", s.text)
        self.assertEqual(settings.load(self.root / "settings.json")["cv"], str(cvfile))  # the failed ones didn't overwrite it

    def test_cv_picker_can_clear_the_cv(self):
        settings.save(self.root / "settings.json", {"cv": "/some/cv.pdf"})
        s = Session(["5", row("cv"), "2", row("done"), "7"], self.root)  # no files found: 1 type a path, 2 don't use a CV
        s.cv_dirs = []
        s.go()
        self.assertEqual(settings.load(self.root / "settings.json")["cv"], "")

    def test_settings_subcommand_goes_straight_to_the_screen(self):
        s = Session([row("done")], self.root)
        cli.main(["settings"], inp=lambda p: s.answers.pop(0), out=s.lines.append, root=self.root, runner=s.runner, opener=s.opened.append)
        self.assertIn("Settings", s.text)

    def test_windows_console_without_curses_browses_in_the_web_report(self):
        import tui

        class Console(cli.UI):
            def real_console(self):
                return True
        (self.root / "report.html").write_text("<html></html>")
        opened, lines = [], []
        ui = Console(self.root, inp=lambda p: "", out=lines.append, opener=opened.append)
        orig, tui.curses = tui.curses, None
        try:
            cli.browse(ui)
        finally:
            tui.curses = orig
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].endswith("report.html"), opened)
        self.assertIn("interactive report", "\n".join(lines))

    def test_scripted_sessions_keep_the_plain_browser(self):
        s = Session(["2", "q", "7"], self.root)
        s.go()
        self.assertIn("2 of 2 roles", s.text)  # not redirected to the web report

    def test_run_with_open_report_does_not_ask_to_browse_again(self):
        settings.save(self.root / "settings.json", {"open_report": True})
        s = Session(["1", "", "7"], self.root)  # search, search now; the report opens itself, so no "browse?" prompt
        s.go()
        self.assertEqual(s.runs, [["--no-settings", "--open"]])
        self.assertNotIn("Browse the results?", s.text)

    def test_open_report_row_in_settings(self):
        s = Session(["5", row("open_report"), "y", row("done"), "7"], self.root)
        s.go()
        self.assertTrue(settings.load(self.root / "settings.json")["open_report"])
        self.assertIn("Open report after run", s.text)

    def test_full_screen_ui_holds_messages_and_shows_them_in_the_next_menu(self):
        import tui
        calls, printed = [], []

        class Screen:
            closed = 0

            def close(self):
                Screen.closed += 1
        orig = tui.run_select
        tui.run_select = lambda title, prompt, options, default, screen=None, notes=(), preview=False: (
            calls.append((prompt, list(notes), preview)) or "y")
        try:
            pauses = []
            ui = cli.FancyUI(self.root, inp=lambda p: pauses.append(p) or "", out=printed.append, runner=lambda argv: printed.append(("ran", argv)))
            ui.screen = Screen()
            ui.say("  read 21 skills")
            ui.say("")  # blank messages are dropped
            self.assertEqual(printed, [])  # nothing is printed over the full-screen display
            self.assertEqual(ui.choose("Settings", [("y", "Yes")], "y", preview=True), "y")
            self.assertEqual(calls, [("Settings", ["  read 21 skills"], True)])
            self.assertEqual(ui.notes, [])  # shown once, then cleared
            ui.say("First discovery scan")
            ui.runner(["--open"])  # a run needs the normal terminal: cleared, headed, and what was held printed first
            self.assertEqual(len(printed), 3)
            self.assertTrue(printed[0].startswith("\x1b[2J\x1b[H"))  # the screen is cleared before anything prints
            self.assertIn("searching", printed[0])
            self.assertEqual(printed[1:], ["First discovery scan", ("ran", ["--open"])])
            self.assertEqual(len(pauses), 1)  # and it waits for Enter, so the result can be read
            self.assertIn("Press Enter", pauses[0])
            self.assertEqual(Screen.closed, 1)
            ui.say("bye")
            ui.close()
            self.assertEqual(printed[-1], "bye")  # leaving for good prints what is still held, without clearing
        finally:
            tui.run_select = orig

    def test_escape_on_the_settings_list_leaves_cleanly(self):
        for sub in ("settings", "browse"):
            lines = []
            self.assertEqual(cli.main([sub], inp=lambda p: (_ for _ in ()).throw(EOFError), out=lines.append,
                                      root=self.root, runner=lambda a: 0, fancy=False), 0)
            self.assertNotIn("bye", "\n".join(lines))  # subcommands don't say goodbye

    def test_ctrl_d_exits_cleanly(self):
        self.assertEqual(Session([], self.root).go(), 0)


if __name__ == "__main__":
    unittest.main()
