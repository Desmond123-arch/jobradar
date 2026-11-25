"""Gem job boards (jobs.gem.com): reading a posting, company lines, discovery, and the cached-scan backfill."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobradar as jr  # noqa: E402
import sources  # noqa: E402

POST = {
    "id": "abc123", "title": "Backend Engineer", "absolute_url": "https://jobs.gem.com/acme/abc123",
    "location": {"name": "Lagos, Nigeria"}, "location_type": "remote",
    "offices": [{"location": {"name": "Lagos, Nigeria"}}, {"location": {"name": "Nairobi, Kenya"}}],
    "first_published_at": "2026-11-01T10:00:00.000Z", "created_at": "2026-10-20T10:00:00.000Z",
    "content": "<p>Build <b>APIs</b></p>", "content_plain": "Build APIs in Python",
}


class ReaderTests(unittest.TestCase):
    def test_a_posting_becomes_a_job(self):
        (job,) = jr.from_gem("Acme", [POST])
        self.assertEqual((job.company, job.title, job.url, job.provider, job.job_id),
                         ("Acme", "Backend Engineer", "https://jobs.gem.com/acme/abc123", "gem", "abc123"))
        self.assertEqual(job.location, "Lagos, Nigeria, Nairobi, Kenya")  # each place once
        self.assertTrue(job.remote)
        self.assertEqual(job.posted, "2026-11-01T10:00:00.000Z")
        self.assertEqual(job.description, "Build APIs in Python")

    def test_on_site_and_hybrid_are_not_remote_unless_the_place_says_so(self):
        for kind in ("hybrid", "on_site", ""):
            (job,) = jr.from_gem("A", [dict(POST, location_type=kind, location={"name": "Berlin, Germany"}, offices=[])])
            self.assertFalse(job.remote, kind)
        (job,) = jr.from_gem("A", [dict(POST, location_type="hybrid", location={"name": "Remote - EMEA"}, offices=[])])
        self.assertTrue(job.remote)

    def test_missing_fields_do_not_crash(self):
        (job,) = jr.from_gem("A", [{"id": 7, "title": "X"}])
        self.assertEqual((job.url, job.location, job.posted, job.description, job.job_id), ("", "", "", "", "7"))
        (job,) = jr.from_gem("A", [{"title": "X", "location": None, "offices": None, "content": "<p>hi</p>"}])
        self.assertEqual(job.description.strip(), "hi")  # falls back to the HTML

    def test_the_date_falls_back_to_created_at(self):
        (job,) = jr.from_gem("A", [dict(POST, first_published_at=None)])
        self.assertEqual(job.posted, "2026-10-20T10:00:00.000Z")

    def test_an_empty_board_or_an_odd_answer(self):
        self.assertEqual(jr.from_gem("A", []), [])
        self.assertEqual(jr.from_gem("A", {"error": "x"}), [])

    def test_fetch_company_calls_the_gem_api(self):
        urls = []
        jobs = jr.fetch_company({"provider": "gem", "slug": "acme", "name": "Acme"}, fetch=lambda u: urls.append(u) or [POST])
        self.assertEqual(urls, ["https://api.gem.com/job_board/v0/acme/job_posts/"])
        self.assertEqual(len(jobs), 1)


class CompanyLineTests(unittest.TestCase):
    def test_a_gem_url_or_provider_slug(self):
        for line in ("https://jobs.gem.com/acme", "https://jobs.gem.com/acme/abc123XYZ", "gem:acme"):
            self.assertEqual(jr.parse_company_line(line), {"provider": "gem", "slug": "acme", "name": "acme"}, line)
        self.assertEqual(jr.parse_company_line("https://jobs.gem.com/acme | Acme Inc")["name"], "Acme Inc")

    def test_the_other_boards_still_parse_and_the_error_names_gem(self):
        self.assertEqual(jr.parse_company_line("lever:zeta")["provider"], "lever")
        with self.assertRaises(ValueError) as cm:
            jr.parse_company_line("https://example.com/x")
        self.assertIn("Gem", str(cm.exception))


class DiscoveryTests(unittest.TestCase):
    def test_gem_is_scanned_and_its_slugs_are_read_from_urls(self):
        self.assertEqual(jr.DISCOVERY_HOSTS["gem"], ["jobs.gem.com"])
        urls = ["https://jobs.gem.com/acme", "https://jobs.gem.com/acme/am9icG9zdDox", "https://jobs.gem.com/Beta-Co/xyz"]
        self.assertEqual(sorted(jr.slugs_from_urls(urls)), ["Beta-Co", "acme"])

    def test_it_appears_as_a_named_source_after_ashby(self):
        self.assertEqual(sources.name("gem"), "Gem")
        self.assertEqual(sources.ordered(["gem", "ashby", "lever"]), ["lever", "ashby", "gem"])

    def test_discovered_gem_boards_become_companies(self):
        out = jr.discovered_companies({"slugs": {"gem": ["acme-corp"]}}, known=[])
        self.assertEqual(out, [{"provider": "gem", "slug": "acme-corp", "name": "Acme Corp", "discovered": True}])


class CachedScanBackfillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "discovered.json"
        self.now = datetime(2026, 11, 25, tzinfo=timezone.utc)
        self.asked = []

    def text(self, url, timeout=150):
        self.asked.append(url)
        if "web.archive.org" in url:
            host = url.split("url=")[1].split("/*")[0]
            return f"https://{host}/acme\nhttps://{host}/beta\n"
        raise OSError("not used")

    def old_cache(self, at="2026-11-20T00:00:00+00:00"):
        self.path.write_text(json.dumps({"at": at, "slugs": {"greenhouse": ["g"], "lever": ["l"], "ashby": ["a"]}, "dead": ["lever:x"]}))

    def test_a_scan_from_before_gem_gets_only_gem_added(self):
        self.old_cache()
        cache, problems = jr.load_discovery(self.path, text=self.text, now=self.now)
        self.assertEqual(cache["slugs"]["gem"], ["acme", "beta"])
        self.assertEqual(cache["slugs"]["greenhouse"], ["g"])  # the rest is kept, not rescanned
        self.assertEqual(cache["dead"], ["lever:x"])
        self.assertTrue(all("jobs.gem.com" in u for u in self.asked if "web.archive.org" in u))
        self.assertEqual(json.loads(self.path.read_text())["slugs"]["gem"], ["acme", "beta"])  # and saved
        self.assertEqual(cache["at"], "2026-11-20T00:00:00+00:00")  # the 30 days still count from the original scan

    def test_a_complete_cache_is_not_touched(self):
        self.old_cache()
        jr.load_discovery(self.path, text=self.text, now=self.now)
        self.asked.clear()
        jr.load_discovery(self.path, text=self.text, now=self.now)
        self.assertEqual(self.asked, [])

    def test_if_the_archive_is_down_nothing_half_saved(self):
        self.old_cache()
        before = self.path.read_text()

        def down(url, timeout=150):
            raise OSError("down")
        cache, problems = jr.load_discovery(self.path, text=down, now=self.now)
        self.assertEqual(self.path.read_text(), before)
        self.assertTrue(problems)
        self.assertNotIn("gem", cache["slugs"])


if __name__ == "__main__":
    unittest.main()
