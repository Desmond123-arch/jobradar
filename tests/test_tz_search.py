"""Time zones and on-site roles in the search, the settings screens, and the flags."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import jobradar as jr  # noqa: E402
import settings  # noqa: E402
import timezones as tz  # noqa: E402
import tui  # noqa: E402

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
RECENT = "2026-11-20T10:00:00Z"
ZONE = tz.Zone(0, 3)  # Accra


def job(location, remote=True, description="", title="Backend Engineer", n=[0]):
    n[0] += 1
    return jr.Job(f"Co{n[0]}", title, f"https://x/{n[0]}", location, remote, RECENT, description, "greenhouse", str(n[0]))


def run(jobs, **kw):
    out, _, counts = jr.run([], [], [], 30, kw.pop("show_all", False), set(), now=NOW, extra_jobs=jobs, any_role=True, **kw)
    return {j.location + "|" + j.description[:20]: j for j in out}, counts


class AssessTests(unittest.TestCase):
    def test_a_far_time_zone_becomes_its_own_blocked_region(self):
        j = job("Remote - Worldwide (PST hours)")
        self.assertEqual(jr.assess(j, tz=ZONE)[0], "timezone")
        self.assertTrue(jr.blocked("timezone", False, False))
        self.assertFalse(jr.blocked("timezone", True, False))  # show-all reveals it

    def test_a_near_zone_stays_and_leaves_a_note_that_reaches_the_why_line(self):
        j = job("Remote - Worldwide (SAST)")
        region, _ = jr.assess(j, tz=ZONE)
        self.assertEqual(region, "worldwide")
        self.assertIn("SAST", j.tz_note)
        jr.score_job(j)
        self.assertTrue(any("time zone: SAST" in w for w in j.why))

    def test_the_check_covers_every_remote_region_but_not_locked_ones(self):
        for loc, expected in (("Remote EMEA (EST hours)", "timezone"), ("Remote (PST)", "timezone"), ("Worldwide (PST)", "timezone")):
            self.assertEqual(jr.assess(job(loc), tz=ZONE)[0], expected, loc)
        self.assertEqual(jr.assess(job("Remote - US only (PST)"), tz=ZONE)[0], "restricted")  # already locked

    def test_without_a_zone_nothing_is_judged(self):
        self.assertEqual(jr.assess(job("Remote - Worldwide (PST hours)"), tz=None)[0], "worldwide")

    def test_the_description_is_read_too(self):
        j = job("Remote - Worldwide", description="You must work US Pacific time zone hours.")
        self.assertEqual(jr.assess(j, tz=ZONE)[0], "timezone")

    def test_the_note_is_cleared_between_assessments(self):
        j = job("Remote - Worldwide (SAST)")
        jr.assess(j, tz=ZONE)
        self.assertTrue(j.tz_note)
        j.location = "Remote - Worldwide"
        jr.assess(j, tz=ZONE)
        self.assertEqual(j.tz_note, "")


class OnsiteTests(unittest.TestCase):
    def test_on_site_roles_are_dropped_unless_you_allow_them(self):
        j = job("Accra, Ghana", remote=False)
        self.assertEqual(jr.assess(j)[0], "onsite")
        self.assertEqual(jr.assess(j, onsite=True)[0], "nearby")

    def test_listed_places_limit_which_on_site_roles_are_kept(self):
        places = ("accra", "ghana")
        self.assertEqual(jr.assess(job("Accra, Ghana", remote=False), onsite=True, places=places)[0], "nearby")
        self.assertEqual(jr.assess(job("Kumasi, GHANA (hybrid)", remote=False), onsite=True, places=places)[0], "nearby")
        self.assertEqual(jr.assess(job("Austin, TX", remote=False), onsite=True, places=places)[0], "onsite")

    def test_places_match_whole_words_not_pieces_of_words(self):
        self.assertTrue(jr.place_ok("Lagos, Nigeria", ("nigeria",)))
        self.assertFalse(jr.place_ok("Kenya", ("ken",)))
        self.assertFalse(jr.place_ok("Washington, DC", ("ash",)))
        self.assertTrue(jr.place_ok("Anywhere", ()))
        self.assertTrue(jr.place_ok("", ()))
        self.assertFalse(jr.place_ok("", ("accra",)))

    def test_the_time_zone_does_not_apply_to_on_site_roles(self):
        j = job("Accra, Ghana (PST hours)", remote=False)
        self.assertEqual(jr.assess(j, tz=ZONE, onsite=True)[0], "nearby")

    def test_the_reason_names_the_place(self):
        _, why = jr.assess(job("Accra, Ghana", remote=False), onsite=True, places=("accra",))
        self.assertIn("Accra, Ghana", why)
        _, why = jr.assess(job("Accra, Ghana", remote=False), onsite=True)
        self.assertIn("on-site", why)

    def test_on_site_roles_rank_just_below_explicit_africa_and_above_plain_worldwide(self):
        jobs = [job("Remote - Worldwide"), job("Accra, Ghana", remote=False), job("Remote EMEA")]
        out, _, _ = jr.run([], [], [], 30, False, set(), now=NOW, extra_jobs=jobs, any_role=True, onsite=True)
        self.assertEqual([j.region for j in out], ["africa-emea", "nearby", "worldwide"])


class RunTests(unittest.TestCase):
    def test_a_search_drops_far_zones_and_keeps_near_and_unmentioned_ones(self):
        out, counts = run([job("Remote - Worldwide (PST)"), job("Remote - Worldwide (SAST)"), job("Remote - Worldwide")], tz=ZONE)
        self.assertEqual(sorted(k.split("|")[0] for k in out), ["Remote - Worldwide", "Remote - Worldwide (SAST)"])
        self.assertEqual(counts["region"], 1)

    def test_show_all_keeps_the_far_ones_labelled(self):
        out, _ = run([job("Remote - Worldwide (PST)")], tz=ZONE, show_all=True)
        self.assertEqual([j.region for j in out.values()], ["timezone"])
        self.assertIn("PST", next(iter(out.values())).region_reason)

    def test_on_site_roles_come_through_a_search_when_allowed(self):
        jobs = [job("Accra, Ghana", remote=False), job("Austin, TX", remote=False)]
        out, _ = run(jobs, onsite=True, places=("accra",))
        self.assertEqual([j.location for j in out.values()], ["Accra, Ghana"])
        out, _ = run(jobs)
        self.assertEqual(out, {})

    def test_a_greenhouse_description_arriving_late_is_checked_too(self):
        board = {"jobs": [{"id": 1, "title": "Backend Engineer", "absolute_url": "https://x/1", "location": {"name": "Remote - Worldwide"},
                           "updated_at": RECENT}]}

        def fetch(url):
            if url.endswith("/jobs/1"):
                return {"content": "&lt;p&gt;Must be available for EST hours.&lt;/p&gt;"}
            return board
        companies = [{"provider": "greenhouse", "slug": "acme", "name": "Acme"}]
        out, _, counts = jr.run(companies, [], [], 30, False, set(), fetch=fetch, now=NOW, any_role=True, light=True, tz=ZONE)
        self.assertEqual(out, [])  # worldwide on its face, dropped once the description is read
        self.assertEqual(counts["region"], 1)
        out, _, _ = jr.run(companies, [], [], 30, False, set(), fetch=fetch, now=NOW, any_role=True, light=True, tz=None)
        self.assertEqual(len(out), 1)


class FlagTests(unittest.TestCase):
    def parse(self, *argv):
        return jr.parse_args(list(argv) + ["--no-settings"])

    def test_defaults(self):
        a = self.parse()
        self.assertEqual((a.timezone, a.tz_range, a.onsite, a.places), ("auto", 3.0, False, ""))

    def test_zone_flag_accepts_what_people_type(self):
        for typed, stored in (("UTC+1", "UTC+1"), ("utc-8", "UTC-8"), ("+1", "UTC+1"), ("5.5", "UTC+5.5"), ("AUTO", "auto")):
            self.assertEqual(self.parse("--timezone", typed).timezone, stored, typed)

    def test_a_bad_zone_is_a_clear_error(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.parse("--timezone", "Paris")
        self.assertIn("is not a time zone", err.getvalue())

    def test_other_flags(self):
        a = self.parse("--tz-range", "5", "--onsite", "--places", "Accra, Ghana")
        self.assertEqual((a.tz_range, a.onsite, a.places), (5.0, True, "Accra, Ghana"))

    def test_settings_become_flags_and_flags_win(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            settings.save(path, {"timezone": "UTC+1", "tz_range": 6, "onsite": True, "places": "Lagos"})
            a = jr.parse_args([], path)
            self.assertEqual((a.timezone, a.tz_range, a.onsite, a.places), ("UTC+1", 6.0, True, "Lagos"))
            a = jr.parse_args(["--tz-range", "2", "--timezone", "UTC-8"], path)
            self.assertEqual((a.timezone, a.tz_range), ("UTC-8", 2.0))


class SettingsValueTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        d = settings.DEFAULTS
        self.assertEqual((d["timezone"], d["tz_range"], d["onsite"], d["places"]), ("auto", 3, False, ""))
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.json"
            for key, bad in (("timezone", "Paris"), ("timezone", "UTC+20"), ("tz_range", 25), ("tz_range", -1), ("tz_range", True),
                             ("tz_range", 2.5), ("onsite", "yes"), ("places", 3)):
                with self.assertRaises(ValueError, msg=(key, bad)):
                    settings.save(path, {key: bad})
            settings.save(path, {"timezone": "UTC+5.5", "tz_range": 24, "onsite": True, "places": "Accra"})
            self.assertEqual(settings.load(path)["timezone"], "UTC+5.5")

    def test_default_values_make_no_flags_and_each_change_makes_one(self):
        base = dict(settings.DEFAULTS)
        self.assertEqual(settings.to_args(base), [])
        self.assertEqual(settings.to_args(dict(base, timezone="UTC-8")), ["--timezone", "UTC-8"])
        self.assertEqual(settings.to_args(dict(base, tz_range=24)), ["--tz-range", "24"])
        self.assertEqual(settings.to_args(dict(base, onsite=True, places="Accra")), ["--onsite", "--places", "Accra"])


class SettingsScreenTests(unittest.TestCase):
    """The new rows, driven the way the other settings tests are."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def drive(self, answers):
        lines, queue = [], list(answers)
        cli.main(["settings"], inp=lambda p: queue.pop(0), out=lines.append, root=self.root, runner=lambda a: 0, fancy=False)
        return "\n".join(lines), settings.load(self.root / "settings.json")

    def number(self, key, options):
        return str(options.index(key) + 1)

    def test_the_time_zone_dropdown_lists_auto_then_every_zone(self):
        keys = [tz.AUTO] + [tz.key_for(o) for o, _ in tz.ZONES]
        text, got = self.drive([cli_row("timezone"), self.number("UTC+1", keys), cli_row("done")])
        self.assertEqual(got["timezone"], "UTC+1")
        self.assertIn("Detect from this computer", text)
        self.assertIn("Lagos", text)
        self.assertIn("UTC-8", text)

    def test_the_range_dropdown(self):
        keys = [str(h) for h, _ in tz.RANGES]
        text, got = self.drive([cli_row("tz_range"), self.number("6", keys), cli_row("done")])
        self.assertEqual(got["tz_range"], 6)
        self.assertIn("Within 3 hours", text)
        self.assertIn("Any: don't filter by time zone", text)
        _, got = self.drive([cli_row("tz_range"), self.number("24", keys), cli_row("done")])
        self.assertEqual(got["tz_range"], 24)

    def test_on_site_and_places(self):
        _, got = self.drive([cli_row("onsite"), "y", cli_row("places"), "Accra, Ghana", cli_row("done")])
        self.assertEqual((got["onsite"], got["places"]), (True, "Accra, Ghana"))
        _, got = self.drive([cli_row("places"), "", cli_row("done")])  # blank means anywhere
        self.assertEqual(got["places"], "")

    def test_the_list_shows_the_current_values_in_words(self):
        settings.save(self.root / "settings.json", {"timezone": "UTC+1", "tz_range": 5, "onsite": True, "places": "Accra"})
        text, _ = self.drive([cli_row("done")])
        for expected in ("Time zone", "UTC+1", "Time zone range", "within 5h", "On-site roles", "On-site places", "Accra"):
            self.assertIn(expected, text)

    def test_describe(self):
        self.assertTrue(cli.describe("timezone", "auto").startswith("this computer"))
        self.assertEqual(cli.describe("tz_range", 24), "off")
        self.assertEqual(cli.describe("places", ""), "anywhere")
        self.assertEqual(cli.describe("onsite", True), "yes")


def cli_row(name):
    return str((list(cli.FIELDS) + ["reset", "done"]).index(name) + 1)


class DisplayTests(unittest.TestCase):
    def test_the_report_and_screen_know_the_new_regions(self):
        j = job("Accra, Ghana", remote=False)
        j.region, j.region_reason = "nearby", "on-site"
        page = jr.render([j], [], {"boards": 1, "dead": 0}, 30, NOW)
        self.assertIn("On-site, included", page)
        self.assertIn("Other time zone", page)
        self.assertIn('id="region-meta"', page)  # the report's filter buttons and badges come from this table
        for key, label, _style, _button in jr.REGION_META:
            self.assertIn(label, page)
        self.assertEqual(jr.REGION_LABEL["timezone"][1], "bad")
        self.assertEqual(tui.REGION_NAME["timezone"], "OTHER TIME ZONE")
        self.assertIn("nearby", tui.REGION_ORDER)

    def test_the_browser_shows_the_other_time_zone_chip(self):
        d = dict(title="T", company="C", location="Remote (PST)", region="timezone", region_reason="time zone: PST is 8h from you",
                 new=False, score=1, posted="2026-11-20", url="https://x", why=[], description="", pay="")
        text = "\n".join(tui.plain(tui.frame(tui.State([d]), 30, 120)))
        self.assertIn("OTHER TIME ZONE", text)
        self.assertIn("PST is 8h from you", text)


if __name__ == "__main__":
    unittest.main()
