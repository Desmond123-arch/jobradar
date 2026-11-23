"""A CV is optional, and a path to one is forgiving: relative, ~, spaces, Windows style. A bad one never stops a search."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import cvmatch  # noqa: E402
import jobradar as jr  # noqa: E402
import settings  # noqa: E402

CV_TEXT = "Python and Go developer. Docker and Kubernetes. PostgreSQL.\n"
ROWS = [("Backend Engineer", "python fastapi postgres docker"), ("Java Developer", "java spring boot"), ("Sales Manager", "sales")]


def board():
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    return json.dumps([{"id": str(i), "text": t, "hostedUrl": f"https://jobs.lever.co/acme/{i}",
                        "categories": {"location": "Remote - Worldwide", "allLocations": ["Remote - Worldwide"]},
                        "workplaceType": "remote", "createdAt": now, "descriptionPlain": d} for i, (t, d) in enumerate(ROWS)]).encode()


class Run:
    """The real main(), against a faked Lever board, in a throwaway data folder."""

    def __init__(self, test, argv, files=None, cwd=None):
        self.dir = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        (self.dir / "companies.txt").write_text("https://jobs.lever.co/acme | Acme\n")
        (self.dir / "exclude.txt").write_text("")
        for name, text in (files or {}).items():
            (self.dir / name).write_text(text)
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = board()
        out, old = io.StringIO(), os.getcwd()
        if cwd:
            os.chdir(cwd)
        try:
            with mock.patch.object(jr, "HERE", self.dir), mock.patch("urllib.request.urlopen", return_value=response), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.code = jr.main(["--no-settings", "--any-role", "--remote", "worldwide", "--out", str(self.dir / "r.html")] + list(argv))
        finally:
            os.chdir(old)
            jr.use_progress(jr.Progress(enabled=False))
        self.text = out.getvalue()
        self.jobs = json.loads((self.dir / "jobs.json").read_text())
        self.titles = [j["title"] for j in self.jobs]
        self.scored = any(j.get("cv_match", -1) >= 0 for j in self.jobs)


class WithoutACvTests(unittest.TestCase):
    def test_no_cv_at_all_just_works(self):
        r = Run(self, [])
        self.assertEqual(r.code, 0)
        self.assertEqual(sorted(r.titles), ["Backend Engineer", "Java Developer", "Sales Manager"])
        self.assertFalse(r.scored)
        self.assertNotIn("scoring against", r.text)
        self.assertNotIn("CV", r.text.split("matching roles")[-1])  # no complaint about a CV nobody asked for

    def test_match_my_cv_without_a_cv_says_what_it_is_doing(self):
        r = Run(self, ["--roles", "cv"])
        self.assertEqual(r.code, 0)
        self.assertEqual(sorted(r.titles), ["Backend Engineer", "Java Developer"])  # a typical software list; sales is out
        self.assertIn("no CV to read", r.text)
        self.assertIn("typical software and backend list", r.text)

    def test_match_my_cv_with_a_cv_follows_the_cv(self):
        cv = Path(tempfile.mkdtemp()) / "cv.txt"
        cv.write_text(CV_TEXT)
        r = Run(self, ["--roles", "cv", "--cv", str(cv)])
        self.assertEqual(r.titles, ["Backend Engineer"])  # Python and Go, not Java
        self.assertIn("keeping titles that fit the", r.text)
        self.assertNotIn("no CV to read", r.text)
        self.assertTrue(r.scored)

    def test_no_cv_flag_beats_a_cv_sitting_next_to_the_script(self):
        r = Run(self, ["--no-cv"], files={"cv.txt": CV_TEXT})
        self.assertFalse(r.scored)

    def test_a_cv_next_to_the_script_is_used_when_nothing_is_configured(self):
        r = Run(self, [], files={"cv.txt": CV_TEXT})
        self.assertTrue(r.scored)
        self.assertIn("scoring against cv.txt", r.text)


class BadCvTests(unittest.TestCase):
    def test_a_missing_file_never_stops_the_search_and_the_message_is_readable(self):
        r = Run(self, ["--cv", "/definitely/not/here/cv.pdf"])
        self.assertEqual(r.code, 0)
        self.assertEqual(len(r.titles), 3)
        self.assertFalse(r.scored)
        self.assertIn("CV: no file at /definitely/not/here/cv.pdf", r.text)
        self.assertIn("Pick it again in Settings", r.text)
        self.assertNotIn("Errno", r.text)  # not the raw system message

    def test_a_cv_with_no_recognised_skills_explains_itself(self):
        junk = Path(tempfile.mkdtemp()) / "empty.txt"
        junk.write_text("nothing relevant here\n")
        r = Run(self, ["--cv", str(junk)])
        self.assertEqual(r.code, 0)
        self.assertFalse(r.scored)
        self.assertIn("CV empty.txt:", r.text)
        self.assertIn("found no known skills", r.text)

    def test_an_unsupported_file_type_and_a_folder_are_reported_not_fatal(self):
        d = Path(tempfile.mkdtemp())
        (d / "pic.png").write_text("png")
        for target in (d / "pic.png", d):
            r = Run(self, ["--cv", str(target)])
            self.assertEqual(r.code, 0, target)
            self.assertIn("use a .pdf, .docx, .txt or .md file", r.text, target)

    def test_an_unreadable_pdf_reports_what_to_install(self):
        pdf = Path(tempfile.mkdtemp()) / "cv.pdf"
        pdf.write_text("%PDF")
        with mock.patch.object(cvmatch, "load", side_effect=RuntimeError("can't read cv.pdf: install pypdf (pip install pypdf) or save the CV as .docx or .txt")):
            r = Run(self, ["--cv", str(pdf)])
        self.assertEqual(r.code, 0)
        self.assertIn("CV cv.pdf: can't read cv.pdf: install pypdf", r.text)


class PathTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        (self.root / "elsewhere").mkdir()
        (self.root / "with space").mkdir()
        (self.root / "cv.txt").write_text(CV_TEXT)
        (self.root / "with space" / "My CV (1).txt").write_text(CV_TEXT)

    def test_a_relative_path_works_from_the_folder_it_was_written_for(self):
        r = Run(self, ["--cv", "cv.txt"], cwd=self.root)
        self.assertTrue(r.scored)

    def test_a_relative_path_still_works_from_another_folder_when_it_is_in_the_data_folder(self):
        r = Run(self, ["--cv", "cv.txt"], files={"cv.txt": CV_TEXT}, cwd=self.root / "elsewhere")
        self.assertTrue(r.scored)  # not in the folder it was run from, but next to Job Radar's own files

    def test_a_relative_path_that_is_nowhere_says_where_it_looked(self):
        r = Run(self, ["--cv", "nope/cv.txt"], cwd=self.root / "elsewhere")
        self.assertIn(str(self.root / "elsewhere" / "nope" / "cv.txt"), r.text)

    def test_spaces_parentheses_and_quotes(self):
        for typed in (str(self.root / "with space" / "My CV (1).txt"), f'"{self.root / "with space" / "My CV (1).txt"}"'):
            self.assertTrue(Run(self, ["--cv", typed]).scored, typed)

    def test_locate_cv_unit(self):
        with mock.patch("os.getcwd", return_value=str(self.root)), mock.patch.object(Path, "cwd", return_value=self.root):
            self.assertEqual(settings.locate_cv("cv.txt", self.root / "elsewhere"), str(self.root / "cv.txt"))
            self.assertEqual(settings.locate_cv("/abs/path/cv.pdf", self.root), "/abs/path/cv.pdf")
            self.assertEqual(settings.locate_cv("", self.root), "")
            self.assertEqual(settings.locate_cv("  ", self.root), "")
            self.assertEqual(settings.locate_cv("missing.pdf", self.root), str(self.root / "missing.pdf"))
        self.assertEqual(settings.locate_cv("~/cv.pdf", self.root), str(Path.home() / "cv.pdf"))
        if os.name != "nt":
            self.assertEqual(settings.locate_cv(r"C:\Users\Jane Doe\cv.pdf", self.root), "/mnt/c/Users/Jane Doe/cv.pdf")

    def test_a_relative_data_folder_fallback(self):
        data = self.root / "data"
        data.mkdir()
        (data / "mine.txt").write_text(CV_TEXT)
        with mock.patch.object(Path, "cwd", return_value=self.root / "elsewhere"):
            self.assertEqual(settings.locate_cv("mine.txt", data), str(data / "mine.txt"))

    def test_clean_path_makes_a_saved_path_absolute_and_keeps_empty_empty(self):
        with mock.patch.object(Path, "cwd", return_value=self.root):
            self.assertEqual(settings.clean_path("cv.txt"), str(self.root / "cv.txt"))
            self.assertEqual(settings.clean_path("sub/cv.txt"), str(self.root / "sub" / "cv.txt"))
        for blank in ("", "   ", None, '""', "''"):
            self.assertEqual(settings.clean_path(blank), "", blank)
        self.assertEqual(settings.clean_path("/abs/cv.pdf"), "/abs/cv.pdf")


class PickerTests(unittest.TestCase):
    """The settings screen: choosing a CV, or choosing not to have one."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.path = self.root / "settings.json"

    def drive(self, answers, cv_dirs=()):
        lines, queue = [], list(answers)
        cli.main(["settings"], inp=lambda p: queue.pop(0), out=lines.append, root=self.root, runner=lambda a: 0, fancy=False,
                 cv_dirs=list(cv_dirs))
        return "\n".join(lines)

    def row(self, name):
        return str((list(cli.FIELDS) + ["reset", "done"]).index(name) + 1)

    def test_a_typed_relative_path_is_saved_as_an_absolute_one(self):
        (self.root / "cv.txt").write_text(CV_TEXT)
        with mock.patch.object(Path, "cwd", return_value=self.root):
            self.drive([self.row("cv"), "1", "cv.txt", self.row("done")])  # no CVs found: 1 is "type a path"
        self.assertEqual(settings.load(self.path)["cv"], str(self.root / "cv.txt"))

    def test_the_picker_offers_going_without_a_cv_and_it_sticks(self):
        settings.save(self.path, {"cv": "/some/cv.pdf"})
        text = self.drive([self.row("cv"), "2", self.row("done")])  # no CVs found: 1 type a path, 2 don't use a CV
        self.assertIn("Don't use a CV", text)
        self.assertEqual(settings.load(self.path)["cv"], "")
        self.assertIn("not set", self.drive([self.row("done")]))  # the settings list says so

    def test_a_bad_typed_path_is_refused_and_does_not_replace_a_good_one(self):
        (self.root / "cv.txt").write_text(CV_TEXT)
        settings.save(self.path, {"cv": str(self.root / "cv.txt")})
        text = self.drive([self.row("cv"), "1", "/not/a/file.pdf", self.row("done")])
        self.assertIn("can't find a file at /not/a/file.pdf", text)
        self.assertEqual(settings.load(self.path)["cv"], str(self.root / "cv.txt"))

    def test_the_default_is_no_cv(self):
        self.assertEqual(settings.DEFAULTS["cv"], "")
        self.assertEqual(settings.to_args(settings.DEFAULTS), [])  # and no flag is made for it


class MatchMyCvWordingTests(unittest.TestCase):
    def test_the_roles_option_says_what_happens_without_a_cv(self):
        label = next(row[1] for row in settings.OPTIONS["roles"] if row[0] == "cv")
        self.assertIn("typical software list if you have none", label)


if __name__ == "__main__":
    unittest.main()
