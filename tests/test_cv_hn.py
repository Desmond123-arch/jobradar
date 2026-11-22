import tempfile
import unittest
import zipfile
from pathlib import Path

import cvmatch
import jobradar as jr

CV_TEXT = "Java, Spring Boot, Go (golang), Python, TypeScript, React, React Native, Node.js, Flask, Playwright, Docker"


class CVMatchTest(unittest.TestCase):
    def setUp(self):
        self.cv = cvmatch.CV("cv.txt", CV_TEXT, cvmatch.skills_in(CV_TEXT))

    def test_skills_found_without_false_friends(self):
        self.assertIn("Spring Boot", self.cv.skills)
        self.assertIn("Go", self.cv.skills)
        self.assertNotIn("JavaScript", cvmatch.skills_in("Java developer"))
        self.assertEqual(cvmatch.skills_in("please express interest; the rest of the team, go home"), set())

    def test_match_reports_missing_skills(self):
        pct, have, missing = cvmatch.match(self.cv, "Backend Engineer", "Python, PostgreSQL, Kubernetes and React")
        self.assertEqual(pct, 50)
        self.assertEqual(have, ["Python", "React"])
        self.assertEqual(missing, ["Kubernetes", "PostgreSQL"])

    def test_too_few_skills_is_not_scored(self):
        self.assertIsNone(cvmatch.match(self.cv, "Engineer", "We use Python.")[0])

    def test_apply_adds_to_score(self):
        job = jr.Job("Acme", "Backend Engineer", "u", "Remote", True, "", "Python, React, Flask, Docker", "x", "1")
        jr.score_job(job)
        before = job.score
        cvmatch.apply(job, self.cv)
        self.assertEqual(job.cv_match, 100)
        self.assertEqual(job.score, before + 10)

    def test_reads_docx(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cv.docx"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", "<w:document><w:p><w:t>Python and React</w:t></w:p></w:document>")
            self.assertEqual(cvmatch.load(path).skills, {"Python", "React"})
            self.assertEqual(cvmatch.find_cv(d), path)


class HackerNewsTest(unittest.TestCase):
    def test_parses_standard_header(self):
        job = jr.parse_hn_post({"id": 7, "created_at": "2026-11-02T16:00:00Z",
                                "text": "Acme | Senior Backend Engineer | REMOTE (EMEA) | Full-time | https://acme.io<p>We build things."})
        self.assertEqual((job.company, job.title, job.location), ("Acme", "Senior Backend Engineer", "REMOTE (EMEA)"))
        self.assertEqual(job.url, "https://news.ycombinator.com/item?id=7")

    def test_skips_posts_without_header(self):
        self.assertIsNone(jr.parse_hn_post({"id": 1, "text": "Hi all, we are hiring engineers, email me."}))

    def test_finds_latest_thread_without_hardcoded_month(self):
        def fetch(url):
            if "search_by_date" in url:
                return {"hits": [{"objectID": "9", "title": "Ask HN: Who wants to be hired? (March 2031)", "num_comments": 900},
                                 {"objectID": "8", "title": "Ask HN: Who is hiring? (March 2031)", "num_comments": 400}]}
            self.assertTrue(url.endswith("/items/8"))
            return {"children": [{"id": 1, "created_at": "2031-03-01T00:00:00Z", "text": "Co | Software Engineer | Remote"}]}
        self.assertEqual([j.company for j in jr.agg_hackernews(fetch, 5)], ["Co"])


class ReportCVTest(unittest.TestCase):
    def test_cv_column_only_when_scored(self):
        job = jr.Job("Acme", "Engineer", "https://x", "Remote", True, "2026-11-20", "", "x", "1", region="worldwide")
        page = jr.render([job], [], {}, 30, jr.datetime.now(jr.timezone.utc))
        self.assertIn('"cv":-1', page)


if __name__ == "__main__":
    unittest.main()
