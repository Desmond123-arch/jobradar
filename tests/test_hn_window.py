"""Hacker News "Who is hiring?" is monthly, so it gets a longer look-back than --days."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobradar as jr  # noqa: E402

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


def posting(provider, days_ago, title):
    posted = (NOW - timedelta(days=days_ago)).isoformat()
    return jr.Job(f"Co {title}", title, f"https://example.com/{title}", "REMOTE (worldwide)", True, posted,
                  "python", provider, title)


def kept(jobs, days):
    out, _, counts = jr.run([], [], [], days, False, set(), now=NOW, extra_jobs=jobs, any_role=True)
    return {j.title for j in out}, counts


class HackerNewsWindowTests(unittest.TestCase):
    def test_a_three_week_old_thread_post_survives_a_two_week_window(self):
        titles, _ = kept([posting("hackernews", 21, "hn-21")], days=15)
        self.assertEqual(titles, {"hn-21"})

    def test_other_sources_still_follow_days_exactly(self):
        titles, counts = kept([posting("greenhouse", 21, "gh-21"), posting("lever", 14, "lv-14")], days=15)
        self.assertEqual(titles, {"lv-14"})
        self.assertEqual(counts["old"], 1)

    def test_hn_posts_from_a_previous_thread_are_still_dropped(self):
        titles, _ = kept([posting("hackernews", 40, "hn-40"), posting("hackernews", 34, "hn-34")], days=15)
        self.assertEqual(titles, {"hn-34"})

    def test_a_longer_days_setting_is_never_shortened(self):
        titles, _ = kept([posting("hackernews", 50, "hn-50"), posting("hackernews", 70, "hn-70")], days=60)
        self.assertEqual(titles, {"hn-50"})

    def test_the_minimum_is_35_days(self):
        self.assertEqual(jr.HN_MIN_DAYS, 35)


class SoftWorldwideWordsTests(unittest.TestCase):
    """"Fully remote" and "global" only mean no office, so they must not hide a named restriction."""

    def region(self, loc, description=""):
        job = jr.Job("C", "Engineer", "https://x", loc, True, "2026-11-20", description, "x", "1")
        return jr.classify_region(job)[0]

    def test_a_soft_word_beside_a_restriction_is_restricted(self):
        for loc in ("Fully Remote (US-only)", "Remote-first, UK", "Fully Remote, Canada", "Global team, remote in Germany"):
            self.assertEqual(self.region(loc), "restricted", loc)

    def test_a_soft_word_on_its_own_is_still_worldwide(self):
        for loc in ("Fully remote", "100% Remote (Global)", "Global (Remote)", "Remote-first"):
            self.assertEqual(self.region(loc), "worldwide", loc)

    def test_a_clear_statement_beats_the_places_it_lists(self):
        for loc in ("Remote - Worldwide", "REMOTE (worldwide)", "REMOTE (Worldwide: US / Canada / Europe)", "Anywhere in the world"):
            self.assertEqual(self.region(loc), "worldwide", loc)

    def test_the_description_can_still_override(self):
        self.assertEqual(self.region("Fully remote", "You must be based in the United States."), "restricted")

    def test_both_kinds_still_count_as_remote_signals(self):
        for loc in ("Anywhere", "Fully remote", "Global"):
            self.assertTrue(jr.WORLDWIDE.search(loc), loc)


if __name__ == "__main__":
    unittest.main()
