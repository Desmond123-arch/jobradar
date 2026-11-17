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


if __name__ == "__main__":
    unittest.main()
