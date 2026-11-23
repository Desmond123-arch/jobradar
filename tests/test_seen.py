"""seen.json follows the boards instead of growing for ever: roles no board has listed for 90 days are forgotten."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobradar as jr  # noqa: E402

TODAY = "2026-11-23"


def days_ago(n, today=TODAY):
    return (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


class Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "seen.json"


class LoadTests(Folder):
    def test_a_missing_file_is_empty(self):
        self.assertEqual(jr.load_seen_state(self.path, TODAY), ({}, None))

    def test_an_older_plain_list_is_read_as_dated_today(self):
        self.path.write_text('["a:b:1", "a:b:2"]')
        state, note = jr.load_seen_state(self.path, TODAY)
        self.assertEqual(state, {"a:b:1": TODAY, "a:b:2": TODAY})  # a full 90 days from now, not forgotten at once
        self.assertIsNone(note)

    def test_the_new_format_keeps_its_dates(self):
        self.path.write_text(json.dumps({"a:b:1": "2026-01-05", "a:b:2": TODAY}))
        self.assertEqual(jr.load_seen_state(self.path, TODAY)[0], {"a:b:1": "2026-01-05", "a:b:2": TODAY})

    def test_a_bad_date_or_value_is_read_as_today_not_as_a_crash(self):
        self.path.write_text(json.dumps({"a": "not a date", "b": 5, "c": None, "d": "2026-13-45", "e": "2026-02-03"}))
        state, _ = jr.load_seen_state(self.path, TODAY)
        self.assertEqual(state, {"a": TODAY, "b": TODAY, "c": TODAY, "d": TODAY, "e": "2026-02-03"})

    def test_keys_are_text(self):
        self.path.write_text("[1, 2]")
        self.assertEqual(set(jr.load_seen_state(self.path, TODAY)[0]), {"1", "2"})

    def test_damaged_files_are_set_aside_in_either_format(self):
        for text in ('["a", "b', '{"a": "2026-01-01"', '"just text"', "42", "null", ""):
            self.path.write_text(text)
            state, note = jr.load_seen_state(self.path, TODAY)
            self.assertEqual(state, {}, text)
            self.assertIn("damaged", note, text)
            self.assertFalse(self.path.exists(), text)
            (self.dir / "seen.json.damaged").unlink()

    def test_load_seen_still_returns_the_keys_as_a_set(self):
        self.path.write_text(json.dumps({"a": TODAY, "b": TODAY}))
        self.assertEqual(jr.load_seen(self.path), ({"a", "b"}, None))
        self.path.write_text('["x"]')
        self.assertEqual(jr.load_seen(self.path), ({"x"}, None))


class UpdateTests(unittest.TestCase):
    def test_everything_listed_or_shown_is_dated_today(self):
        state, forgot = jr.update_seen({"old": days_ago(10)}, ["listed"], ["shown"], TODAY)
        self.assertEqual(state, {"old": days_ago(10), "listed": TODAY, "shown": TODAY})
        self.assertEqual(forgot, 0)

    def test_a_role_that_is_still_listed_never_ages_out(self):
        state = {"open": days_ago(400)}
        state, forgot = jr.update_seen(state, ["open"], [], TODAY)
        self.assertEqual(state, {"open": TODAY})
        self.assertEqual(forgot, 0)

    def test_a_role_no_board_has_listed_for_90_days_is_forgotten(self):
        state, forgot = jr.update_seen({"gone": days_ago(91), "recent": days_ago(30)}, [], [], TODAY)
        self.assertEqual(state, {"recent": days_ago(30)})
        self.assertEqual(forgot, 1)

    def test_the_boundary(self):
        state, forgot = jr.update_seen({"exactly90": days_ago(90), "91": days_ago(91), "89": days_ago(89)}, [], [], TODAY)
        self.assertEqual(sorted(state), ["89", "exactly90"])
        self.assertEqual(forgot, 1)

    def test_the_window_can_be_changed(self):
        state, _ = jr.update_seen({"a": days_ago(20), "b": days_ago(5)}, [], [], TODAY, keep_days=10)
        self.assertEqual(sorted(state), ["b"])

    def test_the_input_is_not_changed(self):
        before = {"gone": days_ago(200)}
        jr.update_seen(before, ["new"], [], TODAY)
        self.assertEqual(before, {"gone": days_ago(200)})

    def test_a_role_that_comes_back_after_being_forgotten_counts_as_new_again(self):
        state, _ = jr.update_seen({"x": days_ago(100)}, [], [], TODAY)
        self.assertNotIn("x", state)  # forgotten; if it is listed again it is not in `seen`, so it shows as NEW

    def test_the_default_is_90_days(self):
        self.assertEqual(jr.SEEN_KEEP_DAYS, 90)

    def test_a_year_of_daily_runs_stays_small(self):
        """Fifty new roles open every day and each stays listed for 20 days. The old behaviour kept all of them."""
        state, everything = {}, set()
        day0 = datetime(2026, 1, 1)
        for d in range(365):
            today = (day0 + timedelta(days=d)).strftime("%Y-%m-%d")
            listed = {f"r{n}" for n in range(d * 50, d * 50 + 50)} | {f"r{n}" for n in range(max(0, (d - 20)) * 50, d * 50)}
            everything |= listed
            state, _ = jr.update_seen(state, listed, [], today)
        self.assertEqual(len(everything), 365 * 50)        # what the old, add-only list would hold: 18,250 entries
        self.assertLess(len(state), (20 + 90) * 50 + 100)  # now: only what was listed within the last 90 days


class RunReportsWhatIsListedTests(unittest.TestCase):
    NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)

    def test_listed_out_holds_every_fetched_role_even_ones_filtered_out(self):
        def job(i, title, posted="2026-11-20"):
            return jr.Job(f"Co{i}", title, f"https://x/{i}", "Remote - Worldwide", True, posted, "d", "greenhouse", str(i))
        jobs = [job(1, "Backend Engineer"), job(2, "Account Executive"), job(3, "Backend Engineer", posted="2025-01-01")]
        listed = set()
        kept, _, counts = jr.run([], [], [], 30, False, set(), now=self.NOW, extra_jobs=jobs, remote=["worldwide"], listed_out=listed)
        self.assertEqual(len(kept), 1)
        self.assertEqual(listed, {j.key for j in jobs})  # the hidden and the old ones are still open on their boards
        self.assertEqual(counts["not_ai"] + counts["old"], 2)

    def test_it_is_optional(self):
        jr.run([], [], [], 30, False, set(), now=self.NOW)


class EndToEndTests(Folder):
    """The real main(), against a faked Lever board, writing the real seen.json."""

    def board(self, ids):
        return json.dumps([{"id": i, "text": f"Backend Engineer {i}", "hostedUrl": f"https://jobs.lever.co/acme/{i}",
                            "categories": {"location": "Remote - Worldwide", "allLocations": ["Remote - Worldwide"]},
                            "workplaceType": "remote", "createdAt": int(datetime.now(timezone.utc).timestamp() * 1000),
                            "descriptionPlain": "python"} for i in ids]).encode()

    def run_main(self, ids):
        (self.dir / "companies.txt").write_text("https://jobs.lever.co/acme | Acme\n")
        (self.dir / "exclude.txt").write_text("")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = self.board(ids)
        out = io.StringIO()
        self.addCleanup(jr.use_progress, jr.Progress(enabled=False))  # main() turns the progress bar on for the whole process
        with mock.patch.object(jr, "HERE", self.dir), mock.patch("urllib.request.urlopen", return_value=response), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = jr.main(["--no-settings", "--any-role", "--remote", "worldwide", "--out", str(self.dir / "r.html")])
        return code, out.getvalue()

    def test_the_file_is_dated_and_stale_entries_are_dropped(self):
        old = days_ago(200, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.path.write_text(json.dumps({"lever:Acme:closed-long-ago": old, "lever:Acme:2": old}))  # 2 is still open below
        code, out = self.run_main(["1", "2"])
        self.assertEqual(code, 0)
        state = json.loads(self.path.read_text())
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertNotIn("lever:Acme:closed-long-ago", state)      # nobody lists it: forgotten
        self.assertEqual(state["lever:Acme:1"], today)             # new today
        self.assertEqual(state["lever:Acme:2"], today)             # still open: its date was refreshed, not dropped
        self.assertIn("forgot 1 old roles", out)

    def test_an_older_list_file_is_migrated_on_the_first_run(self):
        self.path.write_text('["lever:Acme:1", "lever:Acme:9"]')
        self.run_main(["1"])
        state = json.loads(self.path.read_text())
        self.assertIsInstance(state, dict)
        self.assertEqual(set(state), {"lever:Acme:1", "lever:Acme:9"})  # 9 is gone from the board but has 90 days of grace

    def test_roles_seen_before_are_not_new_and_the_file_does_not_balloon_over_repeat_runs(self):
        self.run_main(["1", "2", "3"])
        first = self.path.read_text()
        code, out = self.run_main(["1", "2", "3"])
        self.assertIn("0 new", out)
        self.assertEqual(json.loads(self.path.read_text()), json.loads(first))  # same roles, same file


if __name__ == "__main__":
    unittest.main()
