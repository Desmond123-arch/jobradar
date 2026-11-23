"""Which browser opens a link on each system, and what happens when Chrome, or any browser, is missing."""

import sys
import unittest
import webbrowser
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import openurl  # noqa: E402

EXPLORER = "/mnt/c/Windows/explorer.exe"
CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
NONE = lambda name: None  # noqa: E731


def resolve(setting="", wsl=False, platform="linux", have=(), which=(), env=None):
    return openurl.resolve(setting=setting, env=env or {}, wsl=wsl, platform=platform, exists=lambda p: p in have,
                           which=lambda n: f"/usr/bin/{n}" if n in which else None, globber=lambda p: [])


class WithoutChromeTests(unittest.TestCase):
    def test_native_windows_macos_and_linux_use_the_system_default_browser(self):
        for platform in ("win32", "darwin", "linux"):
            self.assertEqual(resolve(platform=platform), (None, "your default browser"), platform)

    def test_wsl_uses_the_windows_default_browser_through_explorer(self):
        argv, label = resolve(wsl=True, have=[EXPLORER])
        self.assertEqual((argv, label), ([EXPLORER, "{url}"], "your Windows default browser"))

    def test_the_order_on_wsl_is_chrome_then_wslview_then_explorer(self):
        self.assertEqual(resolve(wsl=True, have=[EXPLORER, CHROME])[1], "Chrome")
        self.assertEqual(resolve(wsl=True, have=[EXPLORER], which=["wslview"])[0][0], "wslview")
        self.assertEqual(resolve(wsl=True, have=[EXPLORER])[0][0], EXPLORER)

    def test_explorer_is_only_used_on_wsl(self):
        self.assertEqual(resolve(wsl=False, have=[EXPLORER]), (None, "your default browser"))

    def test_wsl_with_nothing_at_all_falls_back_to_the_generic_launcher(self):
        self.assertEqual(resolve(wsl=True), (None, "your default browser"))

    def test_chrome_is_preferred_when_there_but_never_required(self):
        self.assertEqual(resolve(platform="linux", which=["chromium"]), (["chromium", "{url}"], "Chrome"))
        self.assertEqual(resolve(setting="chrome", platform="linux"), (None, "your default browser (Chrome not found)"))

    def test_the_default_keyword_ignores_chrome_even_if_it_is_installed(self):
        self.assertEqual(resolve(setting="default", wsl=True, have=[CHROME, EXPLORER]), (None, "your default browser"))

    def test_the_environment_beats_the_setting(self):
        argv, label = resolve(setting="chrome", env={"JOBRADAR_BROWSER": "firefox"})
        self.assertEqual((argv, label), (["firefox", "{url}"], "firefox"))


class ChoosingAnotherBrowserTests(unittest.TestCase):
    def test_a_command_with_spaces_in_its_path_on_windows(self):
        argv, label = resolve(setting='"C:\\Program Files\\Mozilla Firefox\\firefox.exe" -new-tab {url}', platform="win32")
        self.assertEqual(argv, ["C:\\Program Files\\Mozilla Firefox\\firefox.exe", "-new-tab", "{url}"])  # no quote characters left
        self.assertEqual(label, "firefox")

    def test_edge_on_windows_and_from_wsl(self):
        win = '"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" {url}'
        self.assertEqual(resolve(setting=win, platform="win32")[1], "msedge")
        wsl = '"/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" {url}'
        argv, label = resolve(setting=wsl, wsl=True)
        self.assertEqual((argv[0], label), ("/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "msedge"))

    def test_macos_open_a(self):
        argv, label = resolve(setting='open -a "Google Chrome" {url}', platform="darwin")
        self.assertEqual(argv, ["open", "-a", "Google Chrome", "{url}"])
        self.assertEqual(label, "open")

    def test_the_link_goes_where_url_is_or_at_the_end(self):
        self.assertEqual(resolve(setting="brave-browser --incognito")[0], ["brave-browser", "--incognito", "{url}"])
        self.assertEqual(resolve(setting="firefox --new-window {url} --private")[0], ["firefox", "--new-window", "{url}", "--private"])


class LaunchingTests(unittest.TestCase):
    def open(self, setting="", fallback=None, runner=None, **kw):
        return openurl.open_url("https://x/1", setting=setting, runner=runner or mock.Mock(), fallback=fallback or (lambda u: True),
                                env={}, **{"wsl": False, "platform": "linux", "exists": lambda p: False, "which": NONE,
                                           "globber": lambda p: [], **kw})

    def test_a_configured_command_that_does_not_exist_is_reported_not_raised(self):
        self.assertEqual(self.open(setting="no-such-browser", runner=mock.Mock(side_effect=FileNotFoundError)), (False, "no-such-browser"))

    def test_when_no_browser_can_start_it_says_so(self):
        self.assertEqual(self.open(fallback=lambda u: False), (False, "your default browser"))

    def test_the_link_is_passed_to_the_command(self):
        runner = mock.Mock()
        self.assertEqual(self.open(setting="firefox", runner=runner), (True, "firefox"))
        self.assertEqual(runner.call_args[0][0], ["firefox", "https://x/1"])

    def test_wsl_without_chrome_launches_explorer(self):
        runner = mock.Mock()
        self.assertEqual(self.open(wsl=True, exists=lambda p: p == EXPLORER, runner=runner), (True, "your Windows default browser"))
        self.assertEqual(runner.call_args[0][0], [EXPLORER, "https://x/1"])

    def test_an_empty_link_is_refused(self):
        self.assertEqual(openurl.open_url("", setting="firefox"), (False, "no link"))


class TextBrowserGuardTests(unittest.TestCase):
    """A browser that runs inside the terminal would wreck the full-screen display, so it is never started."""

    def get(self, name):
        browser = mock.Mock()
        browser.name = name
        browser.open.return_value = True
        return browser

    def test_text_browsers_are_refused_and_never_launched(self):
        for name in ("lynx", "links", "w3m", "elinks", "www-browser", "/usr/bin/lynx", "Links2"):
            browser = self.get(name)
            with mock.patch.object(webbrowser, "get", return_value=browser):
                self.assertFalse(openurl.default_browser_open("https://x"), name)
            browser.open.assert_not_called()

    def test_graphical_browsers_are_used(self):
        for name in ("firefox", "google-chrome", "xdg-open", "safari", "/usr/bin/chromium"):
            browser = self.get(name)
            with mock.patch.object(webbrowser, "get", return_value=browser):
                self.assertTrue(openurl.default_browser_open("https://x"), name)
            browser.open.assert_called_once_with("https://x")

    def test_no_registered_browser_at_all(self):
        with mock.patch.object(webbrowser, "get", side_effect=webbrowser.Error("could not locate runnable browser")):
            self.assertFalse(openurl.default_browser_open("https://x"))

    def test_it_is_the_default_fallback(self):
        import inspect
        self.assertIs(inspect.signature(openurl.open_url).parameters["fallback"].default, openurl.default_browser_open)


class OpenReportMessageTests(unittest.TestCase):
    def run_open_report(self, opener):
        import tempfile
        lines = []
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "report.html").write_text("<html></html>")
            ui = cli.UI(Path(d), inp=lambda p: "", out=lines.append, opener=opener)
            cli.open_report(ui)
        return "\n".join(lines)

    def test_it_says_so_when_no_browser_could_be_started(self):
        text = self.run_open_report(lambda url: False)
        self.assertIn("report.html", text)
        self.assertIn("Couldn't start a browser", text)
        self.assertIn("Open that file yourself", text)

    def test_it_is_quiet_when_it_worked(self):
        self.assertNotIn("Couldn't", self.run_open_report(lambda url: True))

    def test_a_test_opener_that_returns_nothing_is_not_treated_as_a_failure(self):
        self.assertNotIn("Couldn't", self.run_open_report(lambda url: None))

    def test_an_opener_that_raises_does_not_crash_the_menu(self):
        def boom(url):
            raise OSError("no")
        self.run_open_report(boom)


if __name__ == "__main__":
    unittest.main()
