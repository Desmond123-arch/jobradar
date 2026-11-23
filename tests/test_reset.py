"""Reset, and surviving an interrupted run: atomic saves, a damaged seen list, Ctrl+C."""

import contextlib
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import jobradar as jr  # noqa: E402
import paths  # noqa: E402
import reset  # noqa: E402
import settings  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def put(self, name, text="x"):
        (self.root / name).write_text(text)
        return self.root / name


class ResetTests(Folder):
    def test_plan_lists_only_files_that_exist(self):
        self.put("seen.json")
        self.assertEqual([(k, p.name) for k, p, _ in reset.plan(self.root, ["seen", "results", "cache"])], [("seen", "seen.json")])

    def test_disposable_files_are_deleted_without_a_backup(self):
        for n in ("seen.json", "jobs.json", "report.html", "discovered.json"):
            self.put(n)
        done = reset.apply(self.root, ["seen", "results", "cache"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [])
        self.assertEqual(len(done), 4)

    def test_settings_applied_and_excluded_keep_a_backup(self):
        self.put("settings.json", '{"roles": "web"}')
        self.put("applied.txt", "Acme | Engineer\n")
        self.put("exclude.txt", "Acme\n")
        reset.apply(self.root, ["settings", "applied", "excluded"])
        self.assertFalse((self.root / "settings.json").exists())
        self.assertEqual((self.root / "settings.json.bak").read_text(), '{"roles": "web"}')
        self.assertEqual(settings.load(self.root / "settings.json"), settings.DEFAULTS)  # back to the defaults
        self.assertEqual((self.root / "applied.txt").read_text(), "")  # emptied, not deleted
        self.assertEqual((self.root / "applied.txt.bak").read_text(), "Acme | Engineer\n")
        self.assertEqual((self.root / "exclude.txt.bak").read_text(), "Acme\n")

    def test_your_company_list_and_cv_are_never_touched_by_all(self):
        mine = self.put("companies.txt", "https://jobs.lever.co/mine\n")
        cv = self.put("cv.pdf", "my cv")
        with mock.patch.object(paths, "starter_file", lambda name: None):  # a git checkout has no starter copy
            reset.apply(self.root, ["all"])
        self.assertEqual(mine.read_text(), "https://jobs.lever.co/mine\n")
        self.assertEqual(cv.read_text(), "my cv")

    def test_companies_restore_the_starter_list_when_installed(self):
        starter = self.root / "starter.txt"
        starter.write_text("https://jobs.ashbyhq.com/starter\n")
        work = self.root / "work"
        work.mkdir()
        (work / "companies.txt").write_text("mangled\n")
        with mock.patch.object(paths, "starter_file", lambda name: starter):
            self.assertIn("companies", reset.available(work))
            reset.apply(work, ["companies"])
        self.assertEqual((work / "companies.txt").read_text(), "https://jobs.ashbyhq.com/starter\n")
        self.assertEqual((work / "companies.txt.bak").read_text(), "mangled\n")

    def test_companies_is_not_offered_without_a_starter_copy(self):
        with mock.patch.object(paths, "starter_file", lambda name: None):
            self.assertNotIn("companies", reset.available(self.root))
            self.assertEqual(reset.expand(["companies"], self.root), [])

    def test_unknown_names_are_an_error_not_a_silent_no_op(self):
        with self.assertRaises(ValueError) as cm:
            reset.plan(self.root, ["seen", "everything"])
        self.assertIn("everything", str(cm.exception))

    def test_leftovers_from_interrupted_runs_are_cleaned_up(self):
        self.put("seen.json")
        self.put("report.html.tmp")
        self.put("seen.json.damaged")
        reset.apply(self.root, ["seen"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_a_second_reset_is_harmless(self):
        self.put("seen.json")
        reset.apply(self.root, ["seen"])
        self.assertEqual(reset.apply(self.root, ["seen"]), [])


class ResetFlowTests(Folder):
    """The question-and-answer side, with a scripted terminal."""

    def run_flow(self, answers, **kw):
        lines, answers = [], list(answers)
        ui = cli.UI(self.root, inp=lambda p: answers.pop(0), out=lines.append)
        code = cli.reset_flow(ui, **kw)
        return code, "\n".join(lines)

    def test_it_shows_what_will_happen_and_does_nothing_unless_you_say_yes(self):
        seen = self.put("seen.json")
        code, text = self.run_flow(["n"], keys=["seen"])
        self.assertIn("remove seen.json", text)
        self.assertIn("Nothing was changed", text)
        self.assertTrue(seen.exists())
        code, text = self.run_flow([""], keys=["seen"])  # Enter means no
        self.assertTrue(seen.exists())

    def test_yes_does_it(self):
        seen = self.put("seen.json")
        code, text = self.run_flow(["y"], keys=["seen"])
        self.assertFalse(seen.exists())
        self.assertIn("removed seen.json", text)

    def test_assume_yes_skips_the_question(self):
        self.put("seen.json")
        code, text = self.run_flow([], keys=["seen"], assume_yes=True)  # an answer would raise: none are queued
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "seen.json").exists())

    def test_picking_from_the_list_and_nothing_to_reset(self):
        code, text = self.run_flow(["1"])  # first option is "seen"; no such file
        self.assertIn("Nothing to reset", text)

    def test_an_unknown_name_gives_a_clear_error_and_exit_code_2(self):
        code, text = self.run_flow([], keys=["bogus"], assume_yes=True)
        self.assertEqual(code, 2)
        self.assertIn("bogus", text)

    def test_reset_settings_resets_them_instead_of_opening_the_settings_screen(self):
        settings.save(self.root / "settings.json", {"roles": "web"})
        lines = []
        code = cli.main(["reset", "settings", "applied", "--yes"], inp=lambda p: (_ for _ in ()).throw(AssertionError("no question expected")),
                        out=lines.append, root=self.root, runner=lambda a: 0, fancy=False)
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "settings.json").exists())
        self.assertEqual((self.root / "settings.json.bak").read_text().count("web"), 1)
        self.assertNotIn("Look back", "\n".join(lines))  # the settings screen never opened

    def test_the_settings_list_keeps_a_gap_between_every_label_and_its_value(self):
        lines = []
        cli.main(["settings"], inp=lambda p: "13", out=lines.append, root=self.root, runner=lambda a: 0, fancy=False)
        for line in "\n".join(lines).splitlines():
            if "Open report after run" in line:
                self.assertRegex(line, r"Open report after run\s{2,}\S")

    def test_the_reset_command_and_the_settings_row(self):
        seen = self.put("seen.json")
        lines = []
        self.assertEqual(cli.main(["reset", "seen", "--yes"], inp=lambda p: "", out=lines.append, root=self.root, runner=lambda a: 0, fancy=False), 0)
        self.assertFalse(seen.exists())
        applied = self.put("applied.txt", "A | B\n")
        # settings list: row 12 is Reset, row 13 is Done. Reset menu: 1 seen 2 results 3 cache 4 settings
        # 5 applied 6 excluded (companies is hidden without a starter copy). So: Reset, applied, yes, Done.
        answers = ["12", "5", "y", "13"]
        with mock.patch.object(paths, "starter_file", lambda name: None):
            cli.main(["settings"], inp=lambda p: answers.pop(0), out=lines.append, root=self.root, runner=lambda a: 0, fancy=False)
        self.assertEqual(applied.read_text(), "")


class AtomicWriteTests(Folder):
    def test_writes_the_whole_file_and_leaves_no_temp_file(self):
        target = self.root / "seen.json"
        paths.atomic_write(target, "[1]")
        paths.atomic_write(target, "[1, 2]")
        self.assertEqual(target.read_text(), "[1, 2]")
        self.assertEqual([p.name for p in self.root.iterdir()], ["seen.json"])

    def test_a_failure_part_way_leaves_the_old_file_untouched(self):
        target = self.put("seen.json", "[1]")
        with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                paths.atomic_write(target, "[1, 2, 3]")
        self.assertEqual(target.read_text(), "[1]")

    def test_the_applied_list_is_appended_whole(self):
        path = self.root / "applied.txt"
        cli.append_line(path, "Acme | Engineer")
        cli.append_line(path, "Beta | Developer")
        self.assertEqual(path.read_text(), "Acme | Engineer\nBeta | Developer\n")


class DamagedSeenTests(Folder):
    def test_missing_and_valid_files(self):
        self.assertEqual(jr.load_seen(self.root / "seen.json"), (set(), None))
        self.put("seen.json", '["a", "b"]')
        self.assertEqual(jr.load_seen(self.root / "seen.json"), ({"a", "b"}, None))

    def test_a_cut_off_file_is_set_aside_instead_of_crashing_every_run(self):
        self.put("seen.json", '["greenhouse:x:1", "ashby:y')
        seen, note = jr.load_seen(self.root / "seen.json")
        self.assertEqual(seen, set())
        self.assertIn("damaged", note)
        self.assertIn("seen.json.damaged", note)
        self.assertFalse((self.root / "seen.json").exists())
        self.assertEqual((self.root / "seen.json.damaged").read_text(), '["greenhouse:x:1", "ashby:y')
        self.assertEqual(jr.load_seen(self.root / "seen.json"), (set(), None))  # the next run is clean

    def test_wrong_shapes_count_as_damaged_too(self):
        for text in ('{"a": 1}', '"x"', "null", ""):
            self.put("seen.json", text)
            self.assertIsNotNone(jr.load_seen(self.root / "seen.json")[1], text)


class InterruptTests(unittest.TestCase):
    def tearDown(self):
        jr.STOP.clear()

    def test_ctrl_c_ends_a_search_cleanly(self):
        out = io.StringIO()
        with mock.patch.object(jr, "_main", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(out):
            code = jr.main([])
        self.assertEqual(code, 130)
        self.assertIn("Stopped", out.getvalue())
        self.assertIn("as they were", out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())

    def test_interrupting_the_pool_cancels_queued_work_quickly(self):
        done = []

        def slow(i):
            time.sleep(0.15)
            done.append(i)
            return i
        started = time.time()
        with self.assertRaises(KeyboardInterrupt):
            for result in jr.pooled(2, slow, range(40)):  # would take 3 seconds if it ran to the end
                raise KeyboardInterrupt
        time.sleep(0.4)
        self.assertLess(time.time() - started, 1.5)
        self.assertLess(len(done), 10)  # only what was already running finished
        self.assertTrue(jr.STOP.is_set())

    def test_requests_give_up_at_once_after_a_stop(self):
        jr.STOP.set()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("must not make a request")):
            with self.assertRaises(ConnectionError):
                jr.fetch_json("https://example.com/x")

    def test_a_normal_run_clears_a_previous_stop(self):
        jr.STOP.set()
        out = io.StringIO()
        with mock.patch.object(jr, "parse_args", side_effect=SystemExit(0)), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                jr.main([])
        self.assertFalse(jr.STOP.is_set())

    def test_the_progress_bar_line_is_cleared_when_a_run_is_stopped(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True
        stream = Tty()
        p = jr.Progress(stream=stream)
        p.start("Reading job boards", 10)
        p.abort()
        self.assertTrue(stream.getvalue().endswith("\r\x1b[K"))
        self.assertFalse(p.active)
        p.advance()  # harmless after an abort


if __name__ == "__main__":
    unittest.main()
