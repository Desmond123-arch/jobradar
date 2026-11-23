"""jobradar update: what is installed, what is new on GitHub, and how each kind of install is updated. No network."""

import http.client
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import selfupdate as su  # noqa: E402

MINE, LATEST = "a" * 40, "b" * 40


def api(latest=LATEST, headline="Newest change", compare=None, fail=None, compare_fail=None, news=("First new thing", "Second new thing"),
        recent_fail=None, known=True):
    """A fake GitHub. `news` are the commits newer than ours (oldest first); `known=False` means ours is not in the recent list."""
    def fetch(url):
        if fail:
            raise fail
        if url.endswith("/commits/main"):
            return {"sha": latest, "commit": {"message": headline + "\n\nlonger text"}}
        if "/commits?" in url:
            if recent_fail:
                raise recent_fail
            newest_first = [{"sha": (LATEST if i == 0 else f"{i:040d}"), "commit": {"message": m + "\n\nbody"}} for i, m in enumerate(reversed(news))]
            return newest_first + ([{"sha": MINE, "commit": {"message": "mine"}}] if known else [{"sha": "e" * 40, "commit": {"message": "older"}}])
        if "/compare/" in url:
            if compare_fail:
                raise compare_fail
            return compare if compare is not None else {"status": "diverged", "ahead_by": 1, "commits": []}
        raise AssertionError(url)
    return fetch


class Runner:
    """Records every command; answers git questions."""

    def __init__(self, sha=MINE, branch="main", status="", code=0, error=None):
        self.calls, self.sha, self.branch, self.status, self.code, self.error = [], sha, branch, status, code, error

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if "rev-parse" in cmd and "--abbrev-ref" in cmd:
            return SimpleNamespace(returncode=0, stdout=self.branch + "\n")
        if "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout=(self.sha or "") + "\n")
        if "status" in cmd:
            return SimpleNamespace(returncode=0, stdout=self.status)
        if self.error:
            raise self.error
        return SimpleNamespace(returncode=self.code, stdout="")

    @property
    def updates(self):
        """Only the commands that change something."""
        return [c for c in self.calls if not any(w in c for w in ("rev-parse", "status"))]


def run(argv=(), kind="checkout", fetch=None, runner=None, answers=("y",), windows=False, which=lambda n: f"/usr/bin/{n}", src=Path("/x")):
    out, asked, queue = [], [], list(answers)
    runner = runner or Runner()

    def ask(prompt):
        asked.append(prompt)
        if not queue:
            raise EOFError
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    with mock.patch.object(su, "extras", return_value=""):
        code = su.main(argv, say=out.append, ask=ask, fetch=fetch or api(), runner=runner, kind=kind, src=src, which=which, windows=windows)
    return code, "\n".join(out), runner, asked


class DetectTests(unittest.TestCase):
    def test_a_git_folder_is_a_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            self.assertEqual(su.how_installed(Path(d)), "checkout")

    def test_site_packages_is_never_a_checkout_even_with_a_git_folder_above_it(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "lib" / "python3.12" / "site-packages"
            sp.mkdir(parents=True)
            (sp / ".git").mkdir()
            self.assertEqual(su.how_installed(sp, prefix="/usr"), "pip")

    def test_pipx_uv_and_plain_pip_by_where_python_lives(self):
        plain = Path("/nowhere/site-packages")
        self.assertEqual(su.how_installed(plain, prefix="/home/me/.local/share/pipx/venvs/jobradar"), "pipx")
        self.assertEqual(su.how_installed(plain, prefix="/home/me/.local/share/uv/tools/jobradar"), "uv")
        self.assertEqual(su.how_installed(plain, prefix="/home/me/project/.venv"), "pip")
        self.assertEqual(su.how_installed(plain, prefix="/usr"), "pip")


class InstalledCommitTests(unittest.TestCase):
    def dist(self, text=None, error=None):
        d = mock.Mock()
        if error:
            d.read_text.side_effect = error
        else:
            d.read_text.return_value = text
        return d

    def test_an_installed_copy_records_its_commit(self):
        text = '{"url": "https://github.com/x/y.git", "vcs_info": {"commit_id": "%s", "vcs": "git"}}' % MINE
        self.assertEqual(su.installed_commit("pip", dist=self.dist(text)), MINE)

    def test_installed_from_a_local_folder_or_download_has_no_commit(self):
        self.assertIsNone(su.installed_commit("pip", dist=self.dist('{"url": "file:///x", "dir_info": {}}')))
        self.assertIsNone(su.installed_commit("pip", dist=self.dist(None)))
        self.assertIsNone(su.installed_commit("pip", dist=self.dist("not json")))
        self.assertIsNone(su.installed_commit("pip", dist=self.dist(error=OSError())))

    def test_a_checkout_asks_git(self):
        self.assertEqual(su.installed_commit("checkout", runner=Runner(sha=MINE)), MINE)
        self.assertIsNone(su.installed_commit("checkout", runner=Runner(sha="")))
        self.assertIsNone(su.installed_commit("checkout", runner=mock.Mock(side_effect=OSError("no git"))))
        self.assertIsNone(su.installed_commit("checkout", runner=lambda *a, **k: SimpleNamespace(returncode=128, stdout="")))


class SpecTests(unittest.TestCase):
    def spec(self, pdf=False, windows=False):
        def find(name):
            return object() if (name == "pypdf" and pdf) else None

        def version(name):
            if name == "windows-curses" and windows:
                return "2.3"
            raise su.importlib.metadata.PackageNotFoundError(name)
        with mock.patch.object(su.importlib.util, "find_spec", find), mock.patch.object(su.importlib.metadata, "version", version):
            return su.spec()

    def test_the_extras_you_already_use_are_kept(self):
        self.assertEqual(self.spec(), f"jobradar @ {su.GIT_URL}")
        self.assertEqual(self.spec(pdf=True), f"jobradar[pdf] @ {su.GIT_URL}")
        self.assertEqual(self.spec(windows=True), f"jobradar[windows] @ {su.GIT_URL}")
        self.assertEqual(self.spec(pdf=True, windows=True), f"jobradar[all] @ {su.GIT_URL}")


class CommandTests(unittest.TestCase):
    def cmd(self, kind, which=()):
        with mock.patch.object(su, "extras", return_value="pdf"):
            return su.command(kind, which=lambda n: f"/bin/{n}" if n in which else None, python="/venv/bin/python", src=Path("/clone"))

    def test_each_kind_of_install(self):
        s = f"jobradar[pdf] @ {su.GIT_URL}"
        self.assertEqual(self.cmd("checkout"), ["git", "-C", "/clone", "pull", "--ff-only"])
        self.assertEqual(self.cmd("pipx", ["pipx"]), ["pipx", "install", "--force", s])
        self.assertEqual(self.cmd("uv", ["uv"]), ["uv", "tool", "install", "--force", s])
        self.assertEqual(self.cmd("pip"), ["/venv/bin/python", "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", s])

    def test_a_missing_pipx_or_uv_falls_back_to_pip_inside_the_same_environment(self):
        for kind in ("pipx", "uv"):
            self.assertEqual(self.cmd(kind)[:3], ["/venv/bin/python", "-m", "pip"], kind)

    def test_upgrade_alone_is_never_used_because_the_version_does_not_change(self):
        for kind in ("pipx", "uv", "pip"):
            cmd = self.cmd(kind, ["pipx", "uv"])
            self.assertTrue("--force" in cmd or "--force-reinstall" in cmd, kind)
            self.assertNotIn("upgrade", cmd)

    def test_printable_quotes_what_needs_it(self):
        self.assertEqual(su.printable(["pip", "install", "jobradar[pdf] @ git+https://x"]), 'pip install "jobradar[pdf] @ git+https://x"')
        self.assertEqual(su.printable(["git", "-C", "/a b", "pull"]), 'git -C "/a b" pull')


class WhatsNewTests(unittest.TestCase):
    def test_behind_lists_the_new_changes_oldest_first(self):
        news = su.whats_new(MINE, LATEST, api())
        self.assertEqual((news["status"], news["count"], news["subjects"]), ("behind", 2, ["First new thing", "Second new thing"]))

    def test_the_small_commit_list_is_used_not_the_heavy_compare(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return api()(url)
        su.whats_new(MINE, LATEST, fetch)
        self.assertEqual(len(urls), 1)
        self.assertIn("/commits?", urls[0])
        self.assertNotIn("compare", urls[0])

    def test_nothing_new(self):
        news = su.whats_new(MINE, LATEST, api(news=()))
        self.assertEqual((news["status"], news["count"], news["subjects"]), ("identical", 0, []))

    def test_ours_not_in_the_recent_list_falls_back_to_compare(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return api(known=False, compare={"status": "behind", "ahead_by": 0, "behind_by": 3, "commits": []})(url)
        news = su.whats_new(MINE, LATEST, fetch)
        self.assertEqual((news["status"], news["count"]), ("ahead", 3))   # seen from our side
        self.assertTrue(any("/compare/" in u and "per_page=1" in u for u in urls))  # asking for the smallest answer it can

    def test_far_behind_is_reported_from_compare(self):
        news = su.whats_new(MINE, LATEST, api(known=False, compare={"status": "ahead", "ahead_by": 120, "commits": [{"commit": {"message": "x"}}]}))
        self.assertEqual((news["status"], news["count"]), ("behind", 120))

    def test_only_the_newest_are_listed_and_the_rest_counted(self):
        news = su.whats_new(MINE, LATEST, api(news=[f"change {i}" for i in range(20)]), limit=5)
        self.assertEqual(news["subjects"], [f"change {i}" for i in range(15, 20)])
        self.assertEqual(news["count"], 20)

    def test_short(self):
        self.assertEqual((su.short(MINE), su.short(None), su.short("abc")), ("aaaaaaa", "?", "abc"))


class OutcomeTests(unittest.TestCase):
    def test_up_to_date_does_nothing(self):
        code, text, runner, asked = run(runner=Runner(sha=LATEST))
        self.assertEqual(code, 0)
        self.assertIn("You are up to date", text)
        self.assertIn("Newest change", text)
        self.assertEqual((runner.updates, asked), ([], []))

    def test_new_changes_are_listed_and_the_update_runs_after_a_yes(self):
        code, text, runner, asked = run(answers=["y"])
        self.assertEqual(code, 0)
        self.assertIn("2 new changes on GitHub", text)
        self.assertIn("  - First new thing", text)
        self.assertIn("  - Second new thing", text)
        self.assertEqual(runner.updates, [["git", "-C", "/x", "pull", "--ff-only"]])
        self.assertIn("Updated.", text)
        self.assertIn("untouched", text)  # settings and results are kept
        self.assertEqual(len(asked), 1)

    def test_enter_alone_means_yes_and_n_means_no(self):
        self.assertEqual(len(run(answers=[""])[2].updates), 1)
        code, text, runner, _ = run(answers=["n"])
        self.assertEqual((code, runner.updates), (0, []))
        self.assertIn("Nothing was changed", text)
        self.assertEqual(run(answers=["No"])[2].updates, [])

    def test_ctrl_c_or_ctrl_d_at_the_question_changes_nothing(self):
        for error in (EOFError(), KeyboardInterrupt()):
            code, text, runner, _ = run(answers=[error])
            self.assertEqual(code, 1)
            self.assertEqual(runner.updates, [])
            self.assertIn("Nothing was changed", text)

    def test_check_only_never_asks_and_never_changes_anything(self):
        code, text, runner, asked = run(["--check"])
        self.assertEqual((code, runner.updates, asked), (0, [], []))
        self.assertIn("python cli.py update", text)
        code, text, _, _ = run(["--check"], kind="pipx")
        self.assertIn("jobradar update", text)

    def test_yes_skips_the_question(self):
        for flag in ("--yes", "-y"):
            code, _, runner, asked = run([flag], answers=[])
            self.assertEqual((code, len(runner.updates), asked), (0, 1, []), flag)

    def test_being_ahead_of_github_is_not_an_update(self):
        fetch = api(known=False, compare={"status": "behind", "ahead_by": 0, "behind_by": 2, "commits": []})
        code, text, runner, asked = run(fetch=fetch)
        self.assertEqual((code, runner.updates, asked), (0, [], []))
        self.assertIn("2 changes ahead of GitHub", text)
        self.assertIn("Nothing to update", text)

    def test_one_change_is_singular(self):
        self.assertIn("1 new change on GitHub", run(fetch=api(news=["Only change"]))[1])

    def test_diverged_still_offers_the_update(self):
        code, text, runner, _ = run(fetch=api(known=False, compare={"status": "diverged", "ahead_by": 1, "commits": []}))
        self.assertIn("GitHub has a different version", text)
        self.assertEqual(len(runner.updates), 1)

    def test_an_unknown_current_version_still_updates_and_says_what_is_latest(self):
        with mock.patch.object(su, "installed_commit", return_value=None):
            code, text, runner, _ = run(kind="pip")
        self.assertIn("Which version, I can't tell", text)
        self.assertIn(f"The latest on GitHub is {su.short(LATEST)}: Newest change", text)
        self.assertEqual(len(runner.updates), 1)

    def test_a_failed_whats_new_lookup_still_lets_you_update(self):
        code, text, runner, _ = run(fetch=api(recent_fail=urllib.error.URLError("rate limited")))
        self.assertIn("GitHub has a different version", text)
        self.assertEqual(len(runner.updates), 1)


class FailureTests(unittest.TestCase):
    def test_a_dropped_connection_mid_answer_is_a_message_not_a_traceback(self):
        """What the real run hit: GitHub's answer was cut off part way (http.client.IncompleteRead)."""
        for error in (http.client.IncompleteRead(b"abc", 100), http.client.RemoteDisconnected("closed"), ConnectionResetError(), TimeoutError()):
            code, text, runner, _ = run(fetch=api(fail=error))
            self.assertEqual(code, 1, error)
            self.assertIn("Couldn't reach GitHub", text)
            self.assertEqual(runner.updates, [])

    def test_the_whats_new_list_failing_the_same_way_still_lets_you_update(self):
        code, text, runner, _ = run(fetch=api(recent_fail=http.client.IncompleteRead(b"abc", 100)))
        self.assertIn("GitHub has a different version", text)
        self.assertEqual(len(runner.updates), 1)

    def test_fetch_json_retries_once_on_a_dropped_connection(self):
        attempts = []

        class Resp:
            def __enter__(self_):
                attempts.append(1)
                if len(attempts) == 1:
                    raise http.client.IncompleteRead(b"x", 10)
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b'{"ok": true}'
        with mock.patch("urllib.request.urlopen", return_value=Resp()):
            self.assertEqual(su.fetch_json("https://api.github.com/x"), {"ok": True})
        self.assertEqual(len(attempts), 2)
        attempts.clear()
        with mock.patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"x", 10)):
            with self.assertRaises(http.client.IncompleteRead):
                su.fetch_json("https://api.github.com/x")   # twice failing gives up, and main() turns that into a message

    def test_offline_is_a_clear_message_not_a_traceback(self):
        for error in (urllib.error.URLError("no route"), OSError("timed out")):
            code, text, runner, _ = run(fetch=api(fail=error))
            self.assertEqual(code, 1)
            self.assertIn("Couldn't reach GitHub", text)
            self.assertEqual(runner.updates, [])

    def test_garbage_from_github(self):
        def fetch(url):
            return {"unexpected": True}
        code, text, _, _ = run(fetch=fetch)
        self.assertEqual(code, 1)
        self.assertIn("Couldn't reach GitHub", text)

    def test_the_update_command_failing_shows_how_to_run_it_yourself(self):
        code, text, _, _ = run(runner=Runner(code=3))
        self.assertEqual(code, 3)
        self.assertIn("did not finish (exit code 3)", text)
        self.assertIn("git -C /x pull --ff-only", text)
        self.assertNotIn("Updated.", text)

    def test_the_command_not_being_found(self):
        code, text, _, _ = run(kind="pip", runner=Runner(error=FileNotFoundError("python")))
        self.assertEqual(code, 1)
        self.assertIn("Run it yourself", text)

    def test_a_timeout_or_subprocess_error(self):
        code, text, _, _ = run(runner=Runner(error=subprocess.TimeoutExpired("git", 1)))
        self.assertEqual(code, 1)


class CheckoutSafetyTests(unittest.TestCase):
    def test_changes_of_your_own_are_never_overwritten(self):
        code, text, runner, asked = run(runner=Runner(status=" M cli.py\n"))
        self.assertEqual(code, 1)
        self.assertEqual((runner.updates, asked), ([], []))
        self.assertIn("changes of your own", text)
        self.assertIn("Nothing was changed", text)

    def test_untracked_files_do_not_block_it(self):
        self.assertEqual(len(run(runner=Runner(status=""))[2].updates), 1)

    def test_another_branch_is_left_alone(self):
        code, text, runner, _ = run(runner=Runner(branch="my-feature"))
        self.assertEqual((code, runner.updates), (1, []))
        self.assertIn("'my-feature', not main", text)

    def test_git_missing(self):
        runner = mock.Mock(side_effect=lambda cmd, **kw: (_ for _ in ()).throw(OSError("git")) if "status" in cmd or "--abbrev-ref" in cmd else SimpleNamespace(returncode=0, stdout=MINE))
        code, text, _, _ = run(runner=runner)
        self.assertEqual(code, 1)
        self.assertIn("Couldn't run git", text)

    def test_the_safety_checks_apply_only_to_a_checkout(self):
        code, _, runner, _ = run(kind="pip", runner=Runner(status=" M x\n", branch="odd"))
        self.assertEqual(code, 0)
        self.assertEqual(len(runner.updates), 1)


class WindowsTests(unittest.TestCase):
    def test_an_installed_copy_cannot_replace_itself_so_the_command_is_shown(self):
        for kind in ("pipx", "uv", "pip"):
            code, text, runner, asked = run(kind=kind, windows=True, runner=Runner(sha=MINE))
            self.assertEqual(code, 0, kind)
            self.assertEqual((runner.updates, asked), ([], []), kind)
            self.assertIn("can't replace itself while it is running", text)
            self.assertIn("jobradar", text.split("run:")[-1])
            self.assertIn("kept", text)

    def test_a_windows_checkout_is_updated_normally_because_git_pull_does_not_replace_the_program(self):
        code, _, runner, _ = run(kind="checkout", windows=True)
        self.assertEqual((code, len(runner.updates)), (0, 1))


class CliRoutingTests(unittest.TestCase):
    def test_update_goes_to_the_updater_with_its_flags_and_returns_its_code(self):
        seen = {}

        def fake(argv, say=print):
            seen["argv"], seen["say"] = list(argv), say
            return 7
        with mock.patch.object(su, "main", fake):
            lines = []
            self.assertEqual(cli.main(["update", "--check", "--yes"], out=lines.append), 7)
        self.assertEqual(seen["argv"], ["--check", "--yes"])
        seen["say"]("hello")
        self.assertEqual(lines, ["hello"])  # its messages go through the same output

    def test_a_flag_before_the_word_still_counts(self):
        with mock.patch.object(su, "main", lambda argv, say=print: 0):
            self.assertEqual(cli.main(["--plain", "update"], out=lambda t: None), 0)

    def test_it_never_opens_the_full_screen_interface(self):
        with mock.patch.object(su, "main", lambda argv, say=print: 0), mock.patch.object(cli, "FancyUI", side_effect=AssertionError("no UI")):
            cli.main(["update"], out=lambda t: None)

    def test_other_words_are_unaffected(self):
        lines = []
        self.assertEqual(cli.main(["reset", "nothing-here", "--yes"], inp=lambda p: "", out=lines.append, root=Path(tempfile.mkdtemp()),
                                  runner=lambda a: 0, fancy=False), 2)


if __name__ == "__main__":
    unittest.main()
