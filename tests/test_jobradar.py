"""Tests on sample responses in each board's real API format. Run: python -m unittest -v"""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobradar as jr  # noqa: E402

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
RECENT = "2026-11-05T10:00:00Z"
RECENT_MS = int(datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp() * 1000)

GREENHOUSE = {"jobs": [
    {"id": 1, "title": "AI Engineer", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
     "location": {"name": "Remote - Worldwide"}, "updated_at": RECENT,
     "content": "&lt;p&gt;Build agents with LangGraph and FastAPI. RAG and evals.&lt;/p&gt;"},
    {"id": 2, "title": "Senior ML Engineer", "absolute_url": "https://x/2",
     "location": {"name": "Remote - US"}, "updated_at": RECENT, "content": "python"},
    {"id": 3, "title": "Account Executive", "absolute_url": "https://x/3",
     "location": {"name": "Remote - Worldwide"}, "updated_at": RECENT, "content": ""},
    {"id": 4, "title": "LLM Engineer", "absolute_url": "https://x/4",
     "location": {"name": "Remote"}, "updated_at": "2026-08-01T10:00:00Z", "content": "llm"},
    {"id": 5, "title": "Applied AI Engineer", "absolute_url": "https://x/5",
     "location": {"name": "Remote"}, "updated_at": RECENT,
     "content": "&lt;p&gt;You must be based in the United States.&lt;/p&gt;"},
]}

LEVER = [
    {"id": "a1", "text": "Voice AI Engineer", "hostedUrl": "https://jobs.lever.co/beta/a1",
     "categories": {"location": "Remote - EMEA", "allLocations": ["Remote - EMEA"]},
     "workplaceType": "remote", "createdAt": RECENT_MS,
     "descriptionPlain": "LiveKit, Deepgram, Python.", "salaryRange": {"currency": "USD", "min": 90000, "max": 120000, "interval": "per-year-salary"}},
    {"id": "a2", "text": "Staff AI Engineer", "hostedUrl": "https://jobs.lever.co/beta/a2",
     "categories": {"location": "Anywhere"}, "workplaceType": "remote", "createdAt": RECENT_MS, "descriptionPlain": "agents"},
    {"id": "a3", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/beta/a3",
     "categories": {"location": "San Francisco, CA"}, "workplaceType": "onsite", "createdAt": RECENT_MS, "descriptionPlain": ""},
]

ASHBY = {"jobs": [
    {"id": "z1", "title": "LLM Evaluation Engineer", "jobUrl": "https://jobs.ashbyhq.com/gamma/z1",
     "location": "Remote", "isRemote": True, "publishedAt": RECENT, "descriptionPlain": "evals and retrieval", "isListed": True},
    {"id": "z2", "title": "AI Agents Engineer", "jobUrl": "https://jobs.ashbyhq.com/gamma/z2",
     "location": "Lagos, Nigeria", "isRemote": False, "publishedAt": RECENT, "descriptionPlain": "agents", "isListed": True},
    {"id": "z3", "title": "Agentic Python Engineer", "jobUrl": "https://jobs.ashbyhq.com/gamma/z3",
     "location": "Remote", "secondaryLocations": [{"location": "Worldwide"}], "isRemote": True,
     "publishedAt": RECENT, "descriptionPlain": "python agents", "isListed": True},
]}

COMPANIES = [
    {"provider": "greenhouse", "slug": "acme", "name": "Acme"},
    {"provider": "lever", "slug": "beta", "name": "Beta"},
    {"provider": "ashby", "slug": "gamma", "name": "Evaboot"},
    {"provider": "ashby", "slug": "missing", "name": "Ghost Co"},
    {"provider": "ashby", "slug": "supa", "name": "Supabase"},
]


def fake_fetch(url):
    if "boards-api.greenhouse.io/v1/boards/acme/" in url:
        return GREENHOUSE
    if "api.lever.co/v0/postings/beta" in url:
        return LEVER
    if "posting-api/job-board/gamma" in url:
        return ASHBY
    if "supa" in url:
        raise AssertionError("excluded company was fetched")
    raise LookupError("board not found (check the slug)")


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.jobs, self.problems, self.counts = jr.run(
            COMPANIES, ["Supabase"], ["Evaboot | Agentic Python Engineer"],
            days=30, show_all=False, seen=set(), fetch=fake_fetch, now=NOW)
        self.by_title = {j.title: j for j in self.jobs}

    def test_keeps_worldwide_ai_role(self):
        j = self.by_title["AI Engineer"]
        self.assertEqual(j.region, "worldwide")
        self.assertTrue(j.new)
        self.assertIn("langgraph", " ".join(j.why))

    def test_keeps_emea_with_pay(self):
        j = self.by_title["Voice AI Engineer"]
        self.assertEqual(j.region, "africa-emea")
        self.assertIn("90,000", j.pay)

    def test_lagos_onsite_is_kept(self):
        self.assertEqual(self.by_title["AI Agents Engineer"].region, "africa-emea")

    def test_drops_us_only_and_onsite_elsewhere(self):
        for t in ("Senior ML Engineer", "Backend Engineer", "Applied AI Engineer"):
            self.assertNotIn(t, self.by_title)
        self.assertEqual(self.counts["region"], 3)

    def test_drops_non_engineering_and_old(self):
        self.assertNotIn("Account Executive", self.by_title)
        self.assertNotIn("LLM Engineer", self.by_title)
        self.assertEqual(self.counts["old"], 1)

    def test_applied_and_excluded_are_skipped(self):
        self.assertNotIn("Agentic Python Engineer", self.by_title)
        self.assertEqual(self.counts["applied"], 1)
        self.assertEqual(self.counts["excluded"], 1)

    def test_unclear_remote_is_flagged_not_dropped(self):
        self.assertEqual(self.by_title["LLM Evaluation Engineer"].region, "unclear")

    def test_staff_is_marked_stretch(self):
        self.assertTrue(self.by_title["Staff AI Engineer"].stretch)

    def test_bad_board_is_reported(self):
        self.assertTrue(any("Ghost Co" in p for p in self.problems))

    def test_seen_jobs_are_not_new_next_run(self):
        seen = {j.key for j in self.jobs}
        jobs, _, _ = jr.run(COMPANIES, ["Supabase"], [], 30, False, seen, fetch=fake_fetch, now=NOW)
        self.assertFalse(any(j.new for j in jobs if j.title != "Agentic Python Engineer"))

    def test_show_all_includes_locked(self):
        jobs, _, _ = jr.run(COMPANIES, [], [], 30, True, set(), fetch=fake_fetch, now=NOW)
        regions = {j.title: j.region for j in jobs}
        self.assertEqual(regions["Senior ML Engineer"], "restricted")
        self.assertEqual(regions["Applied AI Engineer"], "restricted")

    def test_url_parsing(self):
        cases = {
            "https://job-boards.greenhouse.io/telnyx54 | Telnyx": ("greenhouse", "telnyx54", "Telnyx"),
            "https://boards.greenhouse.io/acme/jobs/123": ("greenhouse", "acme", "acme"),
            "https://jobs.lever.co/distru/42737937-162f": ("lever", "distru", "distru"),
            "https://jobs.ashbyhq.com/moxie/6cbe045b": ("ashby", "moxie", "moxie"),
            "ashby:posthog | PostHog": ("ashby", "posthog", "PostHog"),
        }
        for line, (p, s, n) in cases.items():
            c = jr.parse_company_line(line)
            self.assertEqual((c["provider"], c["slug"], c["name"]), (p, s, n), line)
        self.assertIsNone(jr.parse_company_line("# comment"))
        with self.assertRaises(ValueError):
            jr.parse_company_line("https://example.com/careers")

    def test_report_renders(self):
        page = jr.render(self.jobs, self.problems, self.counts, 30, NOW)
        self.assertIn("NEW", page)
        self.assertIn("Voice AI Engineer", page)
        self.assertIn("Ghost Co", page)

    def test_report_data_cannot_break_out_of_script(self):
        self.jobs[0].title = "Engineer </script><script>alert(1)</script>"
        page = jr.render(self.jobs, self.problems, self.counts, 30, NOW)
        self.assertNotIn("</script><script>alert", page)
        self.assertIn('id="q"', page)  # search box
        self.assertIn('data-v="table"', page)  # table view toggle

    def test_display_company_prettifies_slugs_only(self):
        self.assertEqual(jr.display_company("polygon-labs"), "Polygon Labs")
        self.assertEqual(jr.display_company("GitLab"), "GitLab")
        self.assertEqual(jr.display_company("qdrant.tech"), "qdrant.tech")


EMEA_BOARD = {"jobs": [
    {"id": 10, "title": "Software Engineer", "absolute_url": "https://x/10", "location": {"name": "Berlin, Germany"}, "updated_at": RECENT},
    {"id": 11, "title": "Office Manager", "absolute_url": "https://x/11", "location": {"name": "London"}, "updated_at": RECENT},
    {"id": 12, "title": "Backend Engineer", "absolute_url": "https://x/12", "location": {"name": "Remote - Worldwide"}, "updated_at": RECENT},
    {"id": 13, "title": "Backend Engineer II", "absolute_url": "https://x/13", "location": {"name": "Austin, TX"}, "updated_at": RECENT},
]}
HYDRATED = {"content": "&lt;p&gt;You must be based in the United States.&lt;/p&gt;"}


def emea_fetch(url):
    if url.endswith("/jobs/12"):
        return HYDRATED
    if "/boards/emeaco/jobs" in url:
        assert "content=true" not in url, "discovered boards should be fetched light"
        return EMEA_BOARD
    raise LookupError("board not found (check the slug)")


class EmeaTests(unittest.TestCase):
    def run_emea(self, **kw):
        companies = [{"provider": "greenhouse", "slug": "emeaco", "name": "EmeaCo"},
                     {"provider": "greenhouse", "slug": "nope", "name": "nope", "discovered": True}]
        dead = set()
        jobs, problems, counts = jr.run(companies, [], [], 30, False, set(), fetch=emea_fetch, now=NOW,
                                        emea=True, light=True, dead_out=dead, **kw)
        return {j.title: j for j in jobs}, problems, counts, dead

    def test_emea_mode_keeps_onsite_europe_and_drops_us(self):
        by, _, _, _ = self.run_emea(any_role=True)
        self.assertEqual(by["Software Engineer"].region, "emea")
        self.assertEqual(by["Office Manager"].region, "emea")
        self.assertNotIn("Backend Engineer II", by)

    def test_any_role_flag_controls_title_filter(self):
        by, _, counts, _ = self.run_emea(any_role=False)
        self.assertNotIn("Office Manager", by)
        self.assertEqual(counts["not_ai"], 1)

    def test_roles_preset_matches_web_titles_only(self):
        for t in ("Frontend Engineer", "Front-End Developer", "Full Stack Engineer", "Backend Engineer", "Software Developer", "Web Developer"):
            self.assertTrue(jr.ROLE_PRESETS["web"].search(t), t)
        for t in ("AI Engineer", "Account Executive", "Data Scientist"):
            self.assertFalse(jr.ROLE_PRESETS["web"].search(t), t)
        by, _, _, _ = self.run_emea(role_rx=jr.ROLE_PRESETS["web"])
        self.assertEqual(sorted(by), ["Software Engineer"])  # Backend Engineer is US-locked after hydration; Office Manager isn't a web role

    def test_cv_preset_matches_cv_stack_and_ignores_seniority(self):
        cv = jr.ROLE_PRESETS["cv"]
        yes = ["Software Engineer", "Senior Backend Engineer", "Staff Software Engineer", "Python Developer", "Java Developer",
               "Golang Engineer", "Go Engineer", "Backend Engineer (Go)", "React Native Developer", "Frontend Engineer, React",
               "Full Stack Engineer", "Node.js Developer", "Angular Developer", "Mobile Engineer", "Software Engineer Intern",
               "Test Automation Engineer", "Spring Boot Developer", "Member of Technical Staff", "SDE II"]
        no = ["Go To Market Manager", "Sales Engineer", "Account Executive", "Data Scientist", "Product Designer",
              "Mechanical Engineer", "Marketing Manager", "Recruiter"]
        for t in yes:
            self.assertTrue(cv.search(t) and not jr.ROLE_SKIP.search(t), t)
        for t in no:
            self.assertFalse(cv.search(t) and not jr.ROLE_SKIP.search(t), t)

    def test_no_senior_drops_senior_titles_only(self):
        for t in ("Senior Software Engineer", "Sr. Backend Developer", "Staff Engineer", "Lead Python Developer",
                  "Engineering Manager", "Principal Engineer", "Software Engineer III"):
            self.assertTrue(jr.SENIOR_TITLE.search(t), t)
        for t in ("Software Engineer", "Backend Developer", "Software Engineer II", "Junior Python Developer", "Software Engineer Intern"):
            self.assertFalse(jr.SENIOR_TITLE.search(t), t)

    def test_emea_mode_drops_bare_remote(self):
        board = {"jobs": [{"id": 20, "title": "Software Engineer", "absolute_url": "https://x/20",
                           "location": {"name": "Remote"}, "updated_at": RECENT, "content": "python"}]}
        c = [{"provider": "greenhouse", "slug": "r", "name": "R"}]
        jobs, _, counts = jr.run(c, [], [], 30, False, set(), fetch=lambda u: board, now=NOW, emea=True)
        self.assertEqual(jobs, [])
        self.assertEqual(counts["region"], 1)
        jobs, _, _ = jr.run(c, [], [], 30, False, set(), fetch=lambda u: board, now=NOW)  # default mode still flags it
        self.assertEqual(jobs[0].region, "unclear")

    def test_discovered_names_are_readable(self):
        self.assertEqual(jr.pretty_name("acme-corp"), "Acme Corp")
        self.assertEqual(jr.pretty_name("SignalFire"), "SignalFire")

    def test_default_mode_does_not_treat_europe_as_open(self):
        jobs, _, _ = jr.run(COMPANIES[:1], [], [], 30, False, set(), fetch=lambda u: EMEA_BOARD, now=NOW)
        self.assertEqual([j.title for j in jobs], ["Backend Engineer"])  # only the worldwide one; Berlin is not "open"

    def test_light_greenhouse_hydrates_description_and_rechecks_region(self):
        by, _, _, _ = self.run_emea(any_role=True)
        self.assertNotIn("Backend Engineer", by)  # worldwide location, but the posting says US only

    def test_dead_discovered_board_is_not_a_problem(self):
        _, problems, counts, dead = self.run_emea(any_role=True)
        self.assertEqual(problems, [])
        self.assertEqual(counts["dead"], 1)
        self.assertEqual(dead, {"greenhouse:nope"})


class AggregatorTests(unittest.TestCase):
    def test_sources_parse_and_failures_are_isolated(self):
        def fetch(url):
            if "remotive" in url:
                return {"jobs": [{"id": 1, "title": "AI Engineer", "company_name": "R", "url": "https://r/1",
                                  "candidate_required_location": "Europe", "publication_date": "2026-11-05T10:00:00", "description": "<p>x</p>"}]}
            if "arbeitnow" in url:
                return {"data": [{"slug": "a", "title": "Dev", "company_name": "A", "url": "https://a", "location": "Berlin",
                                  "remote": False, "created_at": 1793000000, "description": ""}], "links": {"next": None}}
            if "himalayas" in url:
                return {"jobs": [{"title": "ML Engineer", "companyName": "H", "applicationLink": "https://h", "locationRestrictions": [],
                                  "pubDate": 1793000000, "description": "", "guid": "g"}]}
            raise ConnectionError("down")
        jobs, problems = jr.fetch_aggregators(1, fetch)
        by = {j.provider: j for j in jobs}
        self.assertEqual(by["remotive"].location, "Europe")
        self.assertFalse(by["arbeitnow"].remote)
        self.assertEqual(by["himalayas"].location, "Worldwide")
        self.assertEqual(len(problems), 3)  # jobicy, remoteok and hackernews failed; the other three still returned
        got, _, _ = jr.run([], [], [], 30, False, set(), now=datetime(2026, 11, 8, tzinfo=timezone.utc),
                           emea=True, extra_jobs=jobs)
        self.assertEqual(sorted(j.title for j in got), ["AI Engineer", "ML Engineer"])  # naive timestamps compare fine; Berlin on-site Dev has no EMEA-remote problem but is not an AI role

    def test_aggregator_duplicates_of_board_jobs_are_dropped(self):
        dup = jr.Job("Acme", "AI Engineer", "https://r", "Worldwide", True, RECENT, "", "remotive", "9")
        _, _, counts = jr.run(COMPANIES[:1], [], [], 30, False, set(), fetch=fake_fetch, now=NOW, extra_jobs=[dup])
        self.assertEqual(counts["dupe"], 1)


class DiscoveryTests(unittest.TestCase):
    def test_slugs_are_extracted_and_junk_is_dropped(self):
        urls = ["https://boards.greenhouse.io/Acme/jobs/123", "https://boards.greenhouse.io/acme",
                "https://boards.greenhouse.io/embed/job_board?for=x", "https://jobs.ashbyhq.com/$company",
                "https://jobs.ashbyhq.com/8f2b5c1e-0000-4000-8000-123456789abc", "https://jobs.lever.co/beta%20x", "https://jobs.lever.co/"]
        self.assertEqual(jr.slugs_from_urls(urls), ["Acme"])

    def test_cache_is_reused_and_dead_boards_skipped(self):
        import tempfile
        calls = []

        def text(url, timeout=0):
            calls.append(url)
            if "web.archive.org" in url and "jobs.lever.co" in url:
                return "https://jobs.lever.co/zeta/abc\nhttps://jobs.lever.co/dead1"
            return ""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "discovered.json"
            cache, _ = jr.load_discovery(path, text=text, now=NOW)
            self.assertFalse(path.exists())  # greenhouse/ashby came back empty: don't cache a partial scan
            cache["slugs"]["greenhouse"] = cache["slugs"]["ashby"] = ["x"]
            path.write_text(json.dumps(cache))
            self.assertEqual(cache["slugs"]["lever"], ["dead1", "zeta"])
            n = len(calls)
            cache2, _ = jr.load_discovery(path, text=text, now=NOW)
            self.assertEqual(len(calls), n)  # served from cache
            cache2["dead"] = ["lever:dead1"]
            found = jr.discovered_companies(cache2, [{"provider": "lever", "slug": "ZETA", "name": "z"}])
            self.assertEqual([c for c in found if c["provider"] == "lever"], [])
            found = jr.discovered_companies(cache2, [])
            self.assertEqual([c["slug"] for c in found if c["provider"] == "lever"], ["zeta"])


class ReportSummaryTests(unittest.TestCase):
    def test_render_accepts_role_label_and_shows_senior_count(self):
        counts = {"not_ai": 5, "senior": 7, "old": 0, "region": 2, "applied": 0, "excluded": 0, "dupe": 0, "boards": 3, "dead": 1}
        page = jr.render([], [], counts, 30, NOW, skipped_label="outside the role filter")
        self.assertIn("5 outside the role filter", page)
        self.assertIn("7 senior-level titles", page)
        self.assertIn("2 region-locked, on-site or no region stated", page)


class FakeTty(__import__("io").StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def ticking(self):
        t = [0.0]
        return t, (lambda: t[0])

    def test_bar_redraws_in_place_and_shows_count_percent_and_eta(self):
        t, clock = self.ticking()
        out = FakeTty()
        p = jr.Progress(stream=out, clock=clock)
        p.start("Reading job boards", 200)
        t[0] = 30.0
        p.advance(100)
        text = out.getvalue()
        self.assertIn("\r", text)
        self.assertIn("100/200", text)
        self.assertIn("50%", text)
        self.assertIn("ETA 0:30", text)
        self.assertIn("█", text)
        self.assertEqual(text.count("\n"), 0)  # one line, redrawn in place
        p.finish("done")
        self.assertTrue(out.getvalue().endswith("\n"))
        self.assertIn("✓", out.getvalue())

    def test_line_fills_proportionally_and_fits_the_terminal(self):
        p = jr.Progress(stream=FakeTty())
        p.start("x", 10)
        p.n = 5
        for columns in (40, 80, 200):
            left, fill, empty, right = p.line(columns)
            self.assertLessEqual(len(left) + len(fill) + len(empty) + len(right), columns)
            self.assertAlmostEqual(len(fill) / (len(fill) + len(empty)), 0.5, delta=0.1)

    def test_piped_output_is_plain_lines_about_every_ten_percent(self):
        out = __import__("io").StringIO()
        p = jr.Progress(stream=out)
        p.start("Boards", 100)
        for _ in range(100):
            p.advance()
        lines = out.getvalue().splitlines()
        self.assertTrue(5 <= len(lines) <= 12, lines)
        self.assertNotIn("\x1b", out.getvalue())
        self.assertIn("Boards: 100/100", lines[-1])

    def test_legacy_windows_code_page_gets_plain_characters_instead_of_crashing(self):
        import io
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        stream.isatty = lambda: True
        p = jr.Progress(stream=stream)
        p.start("Reading job boards", 10)
        p.advance(5)
        p.finish("done")
        stream.flush()
        text = raw.getvalue().decode("cp1252")
        self.assertIn("#", text)
        self.assertIn("OK", text)
        self.assertNotIn("█", text)

    def test_utf8_terminals_keep_the_block_characters(self):
        p = jr.Progress(stream=FakeTty())
        self.assertEqual((p.FILL, p.CHECK), ("█", "✓"))

    def test_disabled_progress_prints_nothing_and_never_fails(self):
        out = FakeTty()
        p = jr.Progress(enabled=False, stream=out)
        p.advance()
        p.log("hi")
        p.start("x", 0)
        p.advance()
        p.finish()
        self.assertEqual(out.getvalue(), "")

    def test_zero_total_does_not_divide_by_zero(self):
        p = jr.Progress(stream=FakeTty())
        p.start("none", 0)
        p.advance()
        p.finish()

    def test_run_reports_progress_for_each_board(self):
        events = []

        class Rec:
            def start(self, label, total): events.append(("start", label, total))
            def advance(self, n=1): events.append(("advance",))
            def finish(self, note=""): events.append(("finish",))
            def log(self, m): pass
        jr.use_progress(Rec())
        try:
            jr.run(COMPANIES, ["Supabase"], [], 30, False, set(), fetch=fake_fetch, now=NOW)
        finally:
            jr.use_progress(jr.Progress(enabled=False))
        self.assertEqual(events[0], ("start", "Reading job boards", 4))  # 5 companies, 1 excluded
        self.assertEqual(sum(1 for e in events if e == ("advance",)), 4)
        self.assertIn(("finish",), events)


if __name__ == "__main__":
    unittest.main()
