"""Kinds of remote role you can tick: worldwide, Africa, EMEA, Europe, Middle East, Americas, Asia-Pacific,
my time zone, and roles that don't say where. A role shows if it matches any box ticked."""

import contextlib
import curses
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import jobradar as jr  # noqa: E402
import scopes  # noqa: E402
import settings  # noqa: E402
import timezones as tz  # noqa: E402
import tui  # noqa: E402

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
SEQ = [0]
ZONE = tz.Zone(0, 3)


def job(location, remote=True, description=""):
    SEQ[0] += 1
    return jr.Job(f"Co{SEQ[0]}", "Backend Engineer", f"https://x/{SEQ[0]}", location, remote, "2026-11-20T10:00:00Z", description,
                  "greenhouse", str(SEQ[0]))


def shown(location, ticked, remote=True, description="", zone=None, **kw):
    """The label a role is shown under with these boxes ticked, or None if it is hidden."""
    j = job(location, remote, description)
    region, _ = jr.assess(j, tz=zone, remote=ticked, **kw)
    return None if jr.blocked(region, False, False) else region


class CategoryTests(unittest.TestCase):
    CASES = {
        "Remote - Worldwide": {"worldwide"}, "Remote, anywhere": {"worldwide"}, "Fully remote": {"worldwide"},
        "Remote, Africa": {"africa"}, "Remote, Nigeria": {"africa"}, "Remote - Accra, Ghana": {"africa"}, "Remote, South Africa": {"africa"},
        "Remote - EMEA": {"emea", "africa", "europe", "middle-east"},
        "Remote, Europe, Middle East and Africa": {"emea", "africa", "europe", "middle-east"},
        "Remote, Germany": {"europe"}, "Remote, UK": {"europe"}, "Remote - Utrecht": {"europe"},
        "Remote, Dubai": {"middle-east"}, "Remote - Saudi Arabia": {"middle-east"}, "Remote, Turkey": {"middle-east"},
        "Remote - US only": {"americas"}, "Remote, Canada": {"americas"}, "Remote - LATAM": {"americas"}, "Smalltown, TX": {"americas"},
        "Remote, India": {"asia-pacific"}, "APAC": {"asia-pacific"}, "Remote, Singapore or Sydney": {"asia-pacific"},
        "Remote": {"unclear"}, "Remote (Georgia)": {"unclear"}, "": {"unclear"},
        "Remote - US or Europe": {"americas", "europe"},
        "Fully Remote (US-only)": {"americas"}, "Remote-first, UK": {"europe"},
    }

    def test_each_posting_belongs_to_the_categories_its_words_support(self):
        for loc, expected in self.CASES.items():
            self.assertEqual(scopes.of(loc)[0], expected, loc)

    def test_a_state_code_only_counts_when_no_other_place_was_named(self):
        self.assertEqual(scopes.of("Berlin, DE")[0], {"europe"})   # DE is Germany here, not Delaware
        self.assertEqual(scopes.of("Pune, IN")[0], {"asia-pacific"})
        self.assertEqual(scopes.of("Tel Aviv, IL")[0], {"middle-east"})
        self.assertEqual(scopes.of("Wilmington, DE")[0], {"americas"})

    def test_a_description_can_lock_a_worldwide_role(self):
        found, why = scopes.of("Remote - Worldwide", "You must be based in the United States.")
        self.assertEqual(found, set())
        self.assertIn("description says", why)
        self.assertEqual(scopes.of("Remote", "Must be authorized to work in the US.")[0], set())

    def test_a_reason_always_comes_back(self):
        for loc in self.CASES:
            self.assertTrue(scopes.of(loc)[1], loc)

    def test_the_table_is_consistent(self):
        self.assertEqual(len(scopes.KEYS), len(set(scopes.KEYS)))
        self.assertTrue(set(scopes.DEFAULT) <= set(scopes.KEYS))
        self.assertTrue(set(scopes.EMEA_WIDE) <= set(scopes.KEYS))
        self.assertEqual(set(scopes.PRIORITY), set(scopes.KEYS) - {"my-timezone"} | {"my-timezone"})
        self.assertTrue(scopes.valid(scopes.DEFAULT) and scopes.valid(scopes.EMEA_WIDE))

    def test_parse_and_valid(self):
        self.assertEqual(scopes.parse("unclear, Worldwide ; africa"), ["worldwide", "africa", "unclear"])  # standard order
        self.assertEqual(scopes.parse(["africa"]), ["africa"])
        self.assertEqual(scopes.parse(""), [])
        with self.assertRaises(ValueError) as cm:
            scopes.parse("africa, mars")
        self.assertIn("mars", str(cm.exception))
        self.assertFalse(scopes.valid(["africa", "worldwide"]))  # not in the standard order
        self.assertFalse(scopes.valid(["africa", "africa"]))
        self.assertFalse(scopes.valid("africa"))
        self.assertTrue(scopes.valid([]))

    def test_short(self):
        self.assertEqual(scopes.short([]), "nothing ticked")
        self.assertEqual(scopes.short(["worldwide", "africa"]), "Worldwide, Africa")
        self.assertEqual(scopes.short(scopes.DEFAULT), "Worldwide, Africa, EMEA and 2 more")

    def test_a_role_in_several_categories_shows_under_the_most_specific_ticked_one(self):
        both = {"worldwide", "africa"}
        self.assertEqual(scopes.label_for(both, {"worldwide", "africa"}), "africa")
        self.assertEqual(scopes.label_for(both, {"worldwide"}), "worldwide")
        self.assertIsNone(scopes.label_for(both, {"europe"}))
        self.assertIsNone(scopes.label_for(set(), set(scopes.KEYS)))


class TickingTests(unittest.TestCase):
    """The point of the redesign: each box stands on its own, and worldwide is only one of them."""

    def test_africa_alone_shows_africa_roles_and_hides_worldwide_ones(self):
        self.assertEqual(shown("Remote, Nigeria", ["africa"]), "africa")
        self.assertIsNone(shown("Remote - Worldwide", ["africa"]))

    def test_worldwide_alone_shows_only_worldwide(self):
        self.assertEqual(shown("Remote - Worldwide", ["worldwide"]), "worldwide")
        self.assertIsNone(shown("Remote, Africa", ["worldwide"]))
        self.assertIsNone(shown("Remote", ["worldwide"]))

    def test_every_kind_can_be_ticked_alone(self):
        pairs = {"europe": "Remote, Germany", "middle-east": "Remote, Dubai", "americas": "Remote - US only",
                 "asia-pacific": "Remote, India", "emea": "Remote - EMEA", "unclear": "Remote"}
        for key, loc in pairs.items():
            self.assertEqual(shown(loc, [key]), key, loc)
            if key != "emea":  # an EMEA role is also Africa, Europe and the Middle East, by definition
                others = [k for k in scopes.KEYS if k not in (key, "my-timezone")]
                self.assertIsNone(shown(loc, others), f"{loc} must not show with everything ticked except {key}")

    def test_an_emea_role_is_open_to_africa_europe_and_the_middle_east_too(self):
        for box in ("emea", "africa", "europe", "middle-east"):
            self.assertIsNotNone(shown("Remote - EMEA", [box]), box)
        self.assertIsNone(shown("Remote - EMEA", ["americas", "asia-pacific", "worldwide"]))

    def test_ticking_two_kinds_shows_either(self):
        self.assertEqual(shown("Remote, Nigeria", ["africa", "europe"]), "africa")
        self.assertEqual(shown("Remote, Germany", ["africa", "europe"]), "europe")
        self.assertIsNone(shown("Remote, India", ["africa", "europe"]))
        self.assertEqual(shown("Remote - US or Europe", ["europe"]), "europe")  # one of the places listed is yours
        self.assertIsNone(shown("Remote - US or Europe", ["africa"]))

    def test_nothing_ticked_shows_no_remote_roles(self):
        for loc in ("Remote - Worldwide", "Remote, Africa", "Remote", "Remote, Germany"):
            self.assertIsNone(shown(loc, []), loc)

    def test_everything_ticked_hides_only_what_a_lock_forbids(self):
        everything = list(scopes.KEYS)
        for loc in ("Remote - Worldwide", "Remote, Africa", "Remote", "Remote, Germany", "Remote - US only"):
            self.assertIsNotNone(shown(loc, everything), loc)
        self.assertIsNone(shown("Remote - Worldwide", everything, description="You must be based in the United States."))

    def test_roles_that_dont_say_where_follow_their_own_box(self):
        self.assertEqual(shown("Remote", ["unclear"]), "unclear")
        self.assertIsNone(shown("Remote", ["worldwide", "africa", "emea"]))
        self.assertEqual(shown("Remote - Worldwide", ["worldwide"]), "worldwide")

    def test_the_reason_says_what_matched_or_why_it_is_hidden(self):
        j = job("Remote, Germany")
        region, reason = jr.assess(j, remote=["africa"])
        self.assertEqual(region, "restricted")
        self.assertIn("Europe", reason)
        self.assertIn("not ticked", reason)
        j = job("Remote - US only")
        self.assertIn("Americas", jr.assess(j, remote=["worldwide"])[1])
        j = job("Remote, Nigeria")
        self.assertEqual(jr.assess(j, remote=["africa"])[1], "location: Remote, Nigeria")

    def test_scopes_are_recorded_on_the_job(self):
        j = job("Remote - US or Europe")
        jr.assess(j, remote=["europe"])
        self.assertEqual(j.scopes, ["americas", "europe"])

    def test_the_older_rules_apply_when_no_list_is_given(self):
        j = job("Lagos, Nigeria", remote=False)
        self.assertEqual(jr.assess(j)[0], "africa-emea")  # code that has not chosen keeps the original behaviour


class TimeZoneBoxTests(unittest.TestCase):
    def test_a_role_tied_to_a_nearby_zone_shows_under_my_time_zone(self):
        self.assertEqual(shown("Remote (CET)", ["my-timezone"], zone=ZONE), "my-timezone")
        self.assertEqual(shown("Remote", ["my-timezone"], description="Overlap with CET required.", zone=ZONE), "my-timezone")

    def test_a_far_zone_is_hidden_whatever_else_is_ticked(self):
        everything = list(scopes.KEYS)
        self.assertIsNone(shown("Remote - Worldwide (PST hours)", everything, zone=ZONE))
        self.assertIsNone(shown("Remote, Nigeria (EST)", everything, zone=ZONE))

    def test_the_box_is_independent_of_the_others(self):
        self.assertIsNone(shown("Remote (CET)", ["worldwide", "africa"], zone=ZONE))      # only a zone: not ticked
        self.assertEqual(shown("Remote (CET)", ["my-timezone"], zone=ZONE), "my-timezone")
        self.assertEqual(shown("Remote, Germany (CET)", ["europe"], zone=ZONE), "europe")  # also Europe: shown as that
        self.assertEqual(shown("Remote, Germany (CET)", ["my-timezone"], zone=ZONE), "my-timezone")

    def test_a_role_that_names_a_zone_is_not_also_one_that_says_nothing(self):
        j = job("Remote (CET)")
        jr.assess(j, tz=ZONE, remote=["unclear", "my-timezone"])
        self.assertNotIn("unclear", j.scopes)
        self.assertIn("my-timezone", j.scopes)

    def test_the_note_reaches_the_why_line(self):
        j = job("Remote (SAST)")
        jr.assess(j, tz=ZONE, remote=["my-timezone"])
        jr.score_job(j)
        self.assertTrue(any("time zone: SAST" in w for w in j.why))

    def test_with_no_range_set_any_zone_named_counts(self):
        self.assertEqual(shown("Remote (PST)", ["my-timezone"], zone=None), "my-timezone")
        self.assertIsNone(shown("Remote", ["my-timezone"], zone=None))


class OnSiteBoxTests(unittest.TestCase):
    def test_remote_boxes_never_let_an_on_site_role_in(self):
        for loc in ("Lagos, Nigeria", "Berlin, Germany", "Dubai, UAE"):
            self.assertIsNone(shown(loc, list(scopes.KEYS), remote=False), loc)

    def test_the_on_site_switch_does(self):
        self.assertEqual(shown("Lagos, Nigeria", [], remote=False, onsite=True), "nearby")
        self.assertEqual(shown("Lagos, Nigeria", [], remote=False, onsite=True, places=("lagos",)), "nearby")
        self.assertIsNone(shown("Berlin, Germany", [], remote=False, onsite=True, places=("lagos",)))


class RunTests(unittest.TestCase):
    def run_jobs(self, jobs, ticked, **kw):
        out, _, counts = jr.run([], [], [], 30, kw.pop("show_all", False), set(), now=NOW, extra_jobs=jobs, any_role=True,
                                remote=ticked, **kw)
        return out, counts

    def test_a_search_applies_the_boxes_and_counts_what_it_hid(self):
        jobs = [job("Remote - Worldwide"), job("Remote, Nigeria"), job("Remote, Germany"), job("Remote")]
        out, counts = self.run_jobs(jobs, ["africa"])
        self.assertEqual([j.region for j in out], ["africa"])
        self.assertEqual(counts["region"], 3)

    def test_unticking_every_remote_kind_and_allowing_on_site_shows_only_on_site_jobs_everywhere(self):
        jobs = [job("Remote - Worldwide"), job("Remote, Nigeria"), job("Remote"), job("San Francisco, CA", remote=False),
                job("Lagos, Nigeria", remote=False), job("Berlin, Germany", remote=False)]
        out, _ = self.run_jobs(jobs, [], onsite=True)  # nothing ticked, on-site on, no places named: anywhere
        self.assertEqual(sorted(j.location for j in out), ["Berlin, Germany", "Lagos, Nigeria", "San Francisco, CA"])
        self.assertEqual({j.region for j in out}, {"nearby"})
        out, _ = self.run_jobs(jobs, [], onsite=True, places=("lagos",))
        self.assertEqual([j.location for j in out], ["Lagos, Nigeria"])  # name a place and only that place

    def test_show_locked_roles_reveals_the_hidden_ones_labelled(self):
        out, _ = self.run_jobs([job("Remote, Germany")], ["africa"], show_all=True)
        self.assertEqual([j.region for j in out], ["restricted"])

    def test_results_rank_by_how_specific_the_match_is(self):
        jobs = [job("Remote"), job("Remote - Worldwide"), job("Remote, Germany"), job("Remote, Nigeria")]
        out, _ = self.run_jobs(jobs, list(scopes.KEYS))
        self.assertEqual([j.region for j in out], ["africa", "worldwide", "europe", "unclear"])

    def test_a_greenhouse_description_arriving_late_can_lock_a_role(self):
        board = {"jobs": [{"id": 1, "title": "Backend Engineer", "absolute_url": "https://x/1", "location": {"name": "Remote - Worldwide"},
                           "updated_at": "2026-11-20T10:00:00Z"}]}

        def fetch(url):
            return {"content": "&lt;p&gt;You must be based in the United States.&lt;/p&gt;"} if url.endswith("/jobs/1") else board
        companies = [{"provider": "greenhouse", "slug": "acme", "name": "Acme"}]
        out, _, counts = jr.run(companies, [], [], 30, False, set(), fetch=fetch, now=NOW, any_role=True, light=True, remote=["worldwide"])
        self.assertEqual(out, [])
        self.assertEqual(counts["region"], 1)


class SettingsAndFlagsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(settings.DEFAULTS["remote"], scopes.DEFAULT)
        self.assertEqual(settings.to_args(settings.DEFAULTS), [])

    def test_loading_hands_out_a_copy_so_editing_it_cannot_change_the_defaults(self):
        got = settings.load(Path("/nonexistent/settings.json"))
        got["remote"].append("americas")
        self.assertEqual(settings.DEFAULTS["remote"], scopes.DEFAULT)
        self.assertEqual(settings.load(Path("/nonexistent/settings.json"))["remote"], scopes.DEFAULT)

    def test_save_validates_the_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            for bad in (["mars"], ["africa", "worldwide"], "africa", ["africa", "africa"], [1]):
                with self.assertRaises(ValueError, msg=bad):
                    settings.save(path, {"remote": bad})
            settings.save(path, {"remote": ["africa", "my-timezone"]})
            self.assertEqual(settings.load(path)["remote"], ["africa", "my-timezone"])
            settings.save(path, {"remote": []})
            self.assertEqual(settings.load(path)["remote"], [])  # nothing ticked is kept as chosen

    def test_older_settings_files_are_read_as_the_list_they_meant(self):
        def load(raw):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "s.json"
                path.write_text(json.dumps(raw))
                return settings.load(path)["remote"]
        self.assertEqual(load({"region": "lagos"}), ["worldwide", "africa", "emea", "my-timezone", "unclear"])
        self.assertEqual(load({"region": "africa", "show_unclear": False}), ["worldwide", "africa", "emea", "my-timezone"])
        self.assertEqual(load({"region": "worldwide"}), ["worldwide", "my-timezone", "unclear"])
        self.assertEqual(load({"region": "emea"}), scopes.EMEA_WIDE)
        self.assertEqual(load({"region": "emea", "show_unclear": False}), [k for k in scopes.EMEA_WIDE if k != "unclear"])
        self.assertEqual(load({"region": "emea", "remote": ["africa"]}), ["africa"])  # the new setting wins
        self.assertEqual(load({"region": "mars"}), scopes.DEFAULT)

    def parse(self, *argv):
        return jr.parse_args(list(argv) + ["--no-settings"])

    def test_the_flag(self):
        self.assertEqual(self.parse().remote, ",".join(scopes.DEFAULT))
        self.assertEqual(self.parse("--remote", "Africa, worldwide").remote, "worldwide,africa")
        self.assertEqual(self.parse("--remote", "").remote, "")
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            self.parse("--remote", "africa,mars")
        self.assertIn("mars", err.getvalue())

    def test_the_older_emea_flag_is_a_shortcut(self):
        self.assertEqual(self.parse("--emea").remote, ",".join(scopes.EMEA_WIDE))

    def test_settings_become_the_flag_and_the_flag_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            settings.save(path, {"remote": ["europe", "my-timezone"]})
            self.assertEqual(jr.parse_args([], path).remote, "europe,my-timezone")
            self.assertEqual(jr.parse_args(["--remote", "africa"], path).remote, "africa")


class ChecklistScreenTests(unittest.TestCase):
    OPTIONS = [("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")]

    class Win:
        def __init__(self, keys):
            self.keys = list(keys)

        def getmaxyx(self):
            return 24, 90

        def erase(self): pass
        def addstr(self, *a): pass
        def refresh(self): pass

        def get_wch(self):
            return self.keys.pop(0)

    class Screen:
        theme, attrs = "glow", {}

        def __init__(self, keys):
            self.win, self.closed = ChecklistScreenTests.Win(keys), 0

        def open(self):
            return self.win

        def set_theme(self, name): pass

        def close(self):
            self.closed += 1

    def tick(self, keys, selected=()):
        screen = self.Screen(keys)
        return tui.run_checklist("Job Radar", "Pick", self.OPTIONS, selected, screen=screen), screen

    def test_space_ticks_and_d_finishes(self):
        self.assertEqual(self.tick([" ", "d"])[0], ["a"])
        self.assertEqual(self.tick([" ", curses.KEY_DOWN, curses.KEY_DOWN, " ", "d"])[0], ["a", "c"])  # kept in the options' order

    def test_ticking_again_unticks(self):
        self.assertEqual(self.tick([" ", " ", "d"])[0], [])
        self.assertEqual(self.tick([" ", "d"], selected=["a"])[0], [])

    def test_enter_ticks_on_an_option_and_finishes_on_done(self):
        self.assertEqual(self.tick([10, curses.KEY_DOWN, 10, curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_DOWN, 10])[0], ["a", "b"])

    def test_digits_tick_directly(self):
        self.assertEqual(self.tick(["3", "1", "d"])[0], ["a", "c"])
        self.assertEqual(self.tick(["9", "d"])[0], [])  # no such option

    def test_a_toggles_everything_on_then_off(self):
        self.assertEqual(self.tick(["a", "d"])[0], ["a", "b", "c"])
        self.assertEqual(self.tick(["a", "a", "d"])[0], [])
        self.assertEqual(self.tick(["a", "d"], selected=["b"])[0], ["a", "b", "c"])

    def test_escape_cancels_and_changes_nothing(self):
        self.assertIsNone(self.tick([" ", 27])[0])
        self.assertIsNone(self.tick([" ", "q"])[0])

    def test_navigation_is_clamped_and_the_session_stays_open(self):
        got, screen = self.tick([curses.KEY_UP, "k", " ", "d"])
        self.assertEqual(got, ["a"])
        self.assertEqual(screen.closed, 0)
        self.assertEqual(self.tick([curses.KEY_DOWN] * 20 + [" "])[0], [])  # parked on Done, space finishes with nothing ticked

    def test_the_mouse_ticks_and_the_wheel_moves(self):
        rows_start = 4  # badge, blank, prompt, blank, then the options
        with mock.patch.object(curses, "getmouse", side_effect=[(0, 5, rows_start + 1, 0, curses.BUTTON1_CLICKED),
                                                                (0, 5, rows_start + 2, 0, curses.BUTTON1_CLICKED),
                                                                (0, 5, rows_start + 3, 0, curses.BUTTON1_CLICKED)]):
            got, _ = self.tick([curses.KEY_MOUSE] * 3)  # tick Beta, tick Gamma, click Done
        self.assertEqual(got, ["b", "c"])
        with mock.patch.object(curses, "getmouse", return_value=(0, 1, 1, 0, curses.BUTTON5_PRESSED)):
            got, _ = self.tick([curses.KEY_MOUSE, " ", "d"])
        self.assertEqual(got, ["b"])

    def test_the_frame_shows_boxes_and_no_box_on_done(self):
        rows = tui.plain(tui.select_frame("Job Radar", "Pick", self.OPTIONS + [("__done", "Done")], 0, 20, 70, checks={"b"}))
        text = "\n".join(rows)
        self.assertIn("[ ] Alpha", text)
        self.assertIn("[x] Beta", text)
        self.assertIn("[ ] Gamma", text)
        done = next(r for r in rows if "Done" in r)
        self.assertNotIn("[", done)

    def test_a_plain_select_has_no_boxes(self):
        self.assertNotIn("[", "\n".join(tui.plain(tui.select_frame("Job Radar", "Pick", self.OPTIONS, 0, 20, 70))))

    def test_the_real_list_fits_and_scrolls_on_a_short_terminal(self):
        options = cli.remote_options() + [("__done", "Done")]
        for h in (10, 14, 24):
            rows = tui.plain(tui.select_frame("Job Radar", "Pick", options, 0, h, 100, checks=set(scopes.DEFAULT)))
            self.assertLessEqual(len(rows), h)
            self.assertIn("esc cancel", rows[-1])


class PlainChecklistTests(unittest.TestCase):
    def ui(self, answers):
        queue, lines = list(answers), []
        ui = cli.UI(Path("."), inp=lambda p: queue.pop(0), out=lines.append)
        return ui, lines

    def test_numbers_toggle_and_blank_finishes(self):
        ui, lines = self.ui(["2", "1 3", ""])
        self.assertEqual(ui.checklist("Pick", [("a", "A"), ("b", "B"), ("c", "C")], ["a"]), ["b", "c"])
        text = "\n".join(lines)
        self.assertIn("1. [x] A", text)   # the first listing shows what was already ticked
        self.assertIn("2. [x] B", text)   # and the next shows the change

    def test_junk_is_ignored(self):
        ui, _ = self.ui(["0, 9, x, 2", ""])
        self.assertEqual(ui.checklist("Pick", [("a", "A"), ("b", "B")], []), ["b"])

    def test_it_keeps_the_order_of_the_options(self):
        ui, _ = self.ui(["3 1", ""])
        self.assertEqual(ui.checklist("Pick", [("a", "A"), ("b", "B"), ("c", "C")], []), ["a", "c"])

    def test_the_full_screen_version_goes_back_on_escape(self):
        ui = cli.FancyUI(Path("."), inp=lambda p: "", out=lambda t: None)
        ui.screen = mock.Mock()
        with mock.patch.object(tui, "run_checklist", return_value=None), self.assertRaises(cli.Back):
            ui.checklist("Pick", [("a", "A")], [])
        with mock.patch.object(tui, "run_checklist", return_value=["a"]):
            self.assertEqual(ui.checklist("Pick", [("a", "A")], []), ["a"])


class WordingTests(unittest.TestCase):
    def test_the_question_options_say_what_each_one_means(self):
        text = "\n".join(label for _, label in cli.remote_options())
        for phrase in ("Worldwide: roles open to anyone, anywhere", "Africa: roles that name Africa", "EMEA: roles that say EMEA",
                       "Europe: roles that name Europe", "Middle East: roles that name", "Americas: roles that name",
                       "Asia-Pacific: roles that name", "My time zone: roles that tie their hours to a time zone near yours",
                       "Don't say where: roles that just say"):
            self.assertIn(phrase, text)
        self.assertNotIn("roles that open", text)  # each meaning is a complete phrase, not a fragment glued on
        self.assertEqual([k for k, _ in cli.remote_options()], scopes.KEYS)

    def test_the_settings_list_shows_what_is_ticked(self):
        self.assertEqual(cli.describe("remote", ["worldwide", "africa"]), "Worldwide, Africa")
        self.assertEqual(cli.describe("remote", []), "nothing ticked")

    def test_every_screen_and_the_report_know_every_label(self):
        keys = {k for k, *_ in jr.REGION_META}
        for key in scopes.KEYS:
            self.assertIn(key, keys, key)
            self.assertIn(key, tui.REGION_STYLE, key)
            self.assertIn(key, tui.REGION_NAME, key)
            self.assertIn(key, tui.REGION_ORDER, key)
            self.assertIn(key, cli.REGION_TAG, key)
        for key in keys:
            self.assertIn(key, tui.REGION_STYLE, key)
            self.assertIn(key, tui.REGION_NAME, key)
            self.assertIn(key, cli.REGION_TAG, key)

    def test_the_browser_shows_the_new_labels(self):
        for key, name in (("africa", "AFRICA"), ("europe", "EUROPE"), ("americas", "AMERICAS"), ("my-timezone", "MY TIME ZONE")):
            d = dict(title="T", company="C", location="Remote", region=key, region_reason="r", new=False, score=1, posted="2026-11-20",
                     url="https://x", why=[], description="", pay="")
            self.assertIn(name, "\n".join(tui.plain(tui.frame(tui.State([d]), 30, 120))))

    def test_the_report_gets_its_buttons_and_badges_from_one_table(self):
        j = job("Remote, Nigeria")
        j.region, j.region_reason = "africa", "location"
        page = jr.render([j], [], {"boards": 1, "dead": 0}, 30, NOW)
        meta = json.loads(page.split('id="region-meta">')[1].split("</script>")[0])
        self.assertEqual([m["key"] for m in meta], [k for k, *_ in jr.REGION_META])
        self.assertIn("META.map", page)
        self.assertNotIn("OPTIONAL_REGIONS", page)

    def test_on_site_included_ranks_just_after_africa(self):
        order = [k for k, *_ in jr.REGION_META]
        self.assertLess(order.index("africa"), order.index("nearby"))
        self.assertLess(order.index("nearby"), order.index("emea"))


if __name__ == "__main__":
    unittest.main()
