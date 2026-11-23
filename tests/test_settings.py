"""Settings: loading, saving, turning settings into flags, finding CVs, and the CV-tailored title filter."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobradar as jr  # noqa: E402
import settings  # noqa: E402


class FileCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "settings.json"


class LoadSaveTests(FileCase):
    def test_missing_file_gives_defaults(self):
        self.assertEqual(settings.load(self.path), settings.DEFAULTS)

    def test_bad_json_and_non_objects_give_defaults(self):
        for text in ("not json", "[1, 2]", '"x"', ""):
            self.path.write_text(text)
            self.assertEqual(settings.load(self.path), settings.DEFAULTS, text)

    def test_invalid_values_fall_back_one_at_a_time(self):
        self.path.write_text(json.dumps({"roles": "nonsense", "remote": ["africa"], "days": 0, "no_senior": "yes",
                                         "show_all": True, "cv": 5, "theme": "nord", "sources": ["all"]}))
        got = settings.load(self.path)
        self.assertEqual((got["roles"], got["remote"], got["days"], got["no_senior"], got["show_all"], got["cv"], got["theme"], got["sources"]),
                         ("ai", ["africa"], 30, False, True, "", "nord", "list"))
        self.path.write_text(json.dumps({"remote": ["mars"]}))
        self.assertEqual(settings.load(self.path)["remote"], list(settings.DEFAULTS["remote"]))  # a bad list is not half-used

    def test_days_must_be_a_real_integer_in_range(self):
        for bad in (True, 2.5, 366, -3, "7"):
            self.path.write_text(json.dumps({"days": bad}))
            self.assertEqual(settings.load(self.path)["days"], 30, bad)
        self.path.write_text(json.dumps({"days": 365}))
        self.assertEqual(settings.load(self.path)["days"], 365)

    def test_save_merges_keeps_unknown_keys_and_leaves_no_temp_file(self):
        self.path.write_text(json.dumps({"note": "mine", "roles": "web"}))
        settings.save(self.path, {"theme": "nord"})
        self.assertEqual(json.loads(self.path.read_text()), {"note": "mine", "roles": "web", "theme": "nord"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["settings.json"])

    def test_save_rejects_bad_values_without_touching_the_file(self):
        self.path.write_text(json.dumps({"roles": "web"}))
        for key, bad in (("roles", "x"), ("days", 0), ("no_senior", "yes")):
            with self.assertRaises(ValueError):
                settings.save(self.path, {key: bad})
        self.assertEqual(json.loads(self.path.read_text()), {"roles": "web"})

    def test_whitespace_is_trimmed_and_unicode_survives(self):
        settings.save(self.path, {"cv": "  /home/me/résumé.pdf  "})
        self.assertIn("résumé", self.path.read_text(encoding="utf-8"))
        self.assertEqual(settings.load(self.path)["cv"], "/home/me/résumé.pdf")


class ExampleFileTests(unittest.TestCase):
    def test_the_example_settings_file_lists_exactly_the_real_settings_with_valid_values(self):
        example = json.loads((Path(__file__).resolve().parent.parent / "settings.example.json").read_text())
        self.assertEqual(set(example), set(settings.DEFAULTS))  # a new setting must be added here too
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            path.write_text(json.dumps(example))
            self.assertEqual(settings.load(path), {**example, "cv": example["cv"]})  # nothing was rejected as invalid


class ToArgsTests(unittest.TestCase):
    def args(self, **kw):
        return settings.to_args(dict(settings.DEFAULTS, **kw))

    def test_defaults_mean_no_flags(self):
        self.assertEqual(self.args(), [])

    def test_open_report_maps_to_the_open_flag_and_defaults_off(self):
        self.assertFalse(settings.DEFAULTS["open_report"])
        self.assertEqual(self.args(open_report=True), ["--open"])

    def test_each_setting_maps_to_its_flag(self):
        self.assertEqual(self.args(roles="cv"), ["--roles", "cv"])
        self.assertEqual(self.args(roles="web"), ["--roles", "web"])
        self.assertEqual(self.args(roles="any"), ["--any-role"])
        self.assertEqual(self.args(remote=["africa", "my-timezone"]), ["--remote", "africa,my-timezone"])
        self.assertEqual(self.args(remote=[]), ["--remote", ""])  # nothing ticked is a real choice, not "use the default"
        self.assertEqual(self.args(sources="agg"), ["--aggregators"])
        self.assertEqual(self.args(sources="all"), ["--aggregators", "--discover"])
        self.assertEqual(self.args(no_senior=True, show_all=True, days=7), ["--no-senior", "--all", "--days", "7"])
        self.assertEqual(self.args(cv="/a b/cv.pdf"), ["--cv", "/a b/cv.pdf"])


class ParseArgsTests(FileCase):
    def test_settings_supply_defaults_and_flags_win(self):
        settings.save(self.path, {"roles": "web", "remote": ["emea"], "days": 14, "sources": "agg"})
        args = jr.parse_args([], self.path)
        self.assertEqual((args.roles, args.remote, args.days, args.aggregators, args.discover), ("web", "emea", 14, True, False))
        args = jr.parse_args(["--days", "3", "--roles", "cv"], self.path)
        self.assertEqual((args.days, args.roles), (3, "cv"))

    def test_open_report_setting_turns_on_the_open_flag(self):
        self.assertFalse(jr.parse_args([], self.path).open)
        settings.save(self.path, {"open_report": True})
        self.assertTrue(jr.parse_args([], self.path).open)
        self.assertFalse(jr.parse_args(["--no-settings"], self.path).open)

    def test_no_settings_ignores_the_file(self):
        settings.save(self.path, {"roles": "web", "remote": ["emea"]})
        args = jr.parse_args(["--no-settings"], self.path)
        self.assertEqual((args.roles, args.remote), (None, "worldwide,africa,emea,my-timezone,unclear"))

    def test_a_broken_file_does_not_stop_a_run(self):
        self.path.write_text("{{{")
        self.assertEqual(jr.parse_args([], self.path).roles, None)


class CleanPathTests(unittest.TestCase):
    def test_windows_paths_become_wsl_paths(self):
        if os.name == "nt":
            self.skipTest("on Windows the path is already right")
        self.assertEqual(settings.clean_path(r'"C:\Users\Jane Doe\Downloads\My_CV_General (1).pdf"'),
                         "/mnt/c/Users/Jane Doe/Downloads/My_CV_General (1).pdf")
        self.assertEqual(settings.clean_path("D:/docs/cv.docx"), "/mnt/d/docs/cv.docx")

    def test_quotes_spaces_tilde_and_empty(self):
        self.assertEqual(settings.clean_path("  '/tmp/a b.pdf'  "), "/tmp/a b.pdf")
        self.assertEqual(settings.clean_path("~/cv.pdf"), str(Path.home() / "cv.pdf"))
        self.assertEqual(settings.clean_path("   "), "")
        self.assertEqual(settings.clean_path(None), "")


class FindCvTests(FileCase):
    def touch(self, name, age=0):
        p = self.dir / name
        p.write_text("x")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_finds_cv_like_files_newest_first_and_ignores_the_rest(self):
        self.touch("old resume.docx", 500)
        new = self.touch("My_CV_General (1).pdf", 10)
        self.touch("notes.txt")
        self.touch("photo.png")
        self.touch("cv.exe")
        (self.dir / "cv-folder.pdf").mkdir()
        names = [p.name for p in settings.find_cv_files([self.dir])]
        self.assertEqual(names, [new.name, "old resume.docx"])

    def test_limit_missing_dirs_and_duplicates(self):
        for i in range(12):
            self.touch(f"cv{i}.pdf", i)
        self.assertEqual(len(settings.find_cv_files([self.dir, self.dir, self.dir / "missing"], limit=5)), 5)
        self.assertEqual(len(settings.find_cv_files([self.dir, self.dir], limit=50)), 12)


class CvRolesTests(unittest.TestCase):
    def test_titles_follow_the_skills_on_the_cv(self):
        rx = jr.roles_regex_for_cv({"Python", "React", "Docker"})
        for t in ("Python Developer", "Senior React Engineer", "Backend Engineer", "Software Engineer", "DevOps Engineer",
                  "Software Engineer (Python)"):
            self.assertTrue(rx.search(t), t)
        for t in ("Java Developer", "Kotlin Engineer", "Account Executive", "Data Scientist", "Rust Engineer"):
            self.assertFalse(rx.search(t), t)

    def test_a_java_cv_keeps_java_but_a_python_cv_does_not(self):
        self.assertTrue(jr.roles_regex_for_cv({"Java", "Spring Boot"}).search("Java Developer"))
        self.assertFalse(jr.roles_regex_for_cv({"Python"}).search("Java Developer"))
        self.assertFalse(jr.roles_regex_for_cv({"Java"}).search("JavaScript Developer"))

    def test_no_mapped_skills_falls_back_to_the_typical_stack(self):
        self.assertIs(jr.roles_regex_for_cv({"Git", "SQL"}), jr.ROLE_PRESETS["cv"])
        self.assertIs(jr.roles_regex_for_cv(set()), jr.ROLE_PRESETS["cv"])

    def test_every_skill_mapping_names_a_real_cv_skill(self):
        import cvmatch
        self.assertEqual(set(jr.SKILL_TITLE_TOKENS) - set(cvmatch.SKILLS), set())


if __name__ == "__main__":
    unittest.main()
