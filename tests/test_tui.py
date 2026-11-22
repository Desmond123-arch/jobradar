"""Tests for the full-screen browser's logic and screen layout, and for choosing a browser. No terminal needed."""

import curses
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import openurl  # noqa: E402
import tui  # noqa: E402


def job(i, title="Backend Engineer", company="Acme", region="emea", new=False, score=5, posted="2026-11-05", **kw):
    return dict(title=f"{title} {i}", company=company, location="Berlin", region=region, region_reason="location in EMEA",
                new=new, score=score, posted=posted, url=f"https://x/{i}", why=["engineering role"],
                description="Python and Go services.", pay="", **kw)


JOBS = [job(0, new=True, score=9), job(1, "Frontend Engineer", "Beta", "worldwide", score=3, posted="2026-11-08"),
        job(2, "日本語のエンジニア", "Gamma", "unclear", score=7, posted="2026-11-01"), job(3, "Sales Engineer", "Acme")]


def press(state, *keys):
    events = []
    for k in keys:
        events.append(state.press(k))
    return events


class StateTests(unittest.TestCase):
    def test_move_is_clamped_and_enter_opens_the_selected_role(self):
        st = tui.State(JOBS)
        press(st, "k", "k", curses.KEY_DOWN, "j")
        self.assertEqual(st.cursor, 2)
        press(st, "G", "j")
        self.assertEqual(st.cursor, 3)
        press(st, "g")
        self.assertEqual(st.cursor, 0)
        for enter in (10, 13, curses.KEY_ENTER, "\n"):
            event = st.press(enter)
            self.assertEqual(event[0], "open")
            self.assertEqual(event[1]["url"], "https://x/0")

    def test_filter_words_are_anded_quotes_and_minus_work(self):
        st = tui.State(JOBS)
        press(st, "/", *"frontend", 10)
        self.assertEqual([j["company"] for j in st.shown], ["Beta"])
        press(st, "c", "/", *"engineer -sales", 10)
        self.assertEqual(sorted(j["_i"] for j in st.shown), [0, 1, 2])  # all but "Sales Engineer"
        press(st, "c", "/", *'"backend engineer 0"', 10)
        self.assertEqual([j["_i"] for j in st.shown], [0])

    def test_filter_mode_edit_cancel_and_unicode(self):
        st = tui.State(JOBS)
        press(st, "/", "a", "b", curses.KEY_BACKSPACE)
        self.assertEqual(st.query, "a")
        press(st, 27)
        self.assertEqual((st.query, st.mode), ("", "list"))
        press(st, "/", "日", "本", 10)
        self.assertEqual([j["_i"] for j in st.shown], [2])

    def test_region_new_sort_clear(self):
        st = tui.State(JOBS)
        self.assertEqual(st.regions, ["", "emea", "worldwide", "unclear"])
        st.press("r")
        self.assertEqual({j["region"] for j in st.shown}, {"emea"})
        st.press("n")
        self.assertEqual([j["_i"] for j in st.shown], [0])
        st.press("c")
        self.assertEqual(len(st.shown), 4)
        st.press("s")  # fit score
        self.assertEqual([j["_i"] for j in st.shown][:2], [0, 2])
        st.press("s")  # newest first
        self.assertEqual(st.shown[0]["_i"], 1)
        st.press("s")  # company A-Z
        self.assertEqual(st.shown[0]["company"], "Acme")

    def test_escape_clears_filters_then_quits(self):
        st = tui.State(JOBS)
        st.press("n")
        self.assertEqual(st.press(27), None)
        self.assertFalse(st.filters_active)
        self.assertEqual(st.press(27), ("quit", None))
        self.assertEqual(st.press("q"), ("quit", None))

    def test_applied_and_exclude_events_and_removal(self):
        st = tui.State(JOBS)
        kind, j = st.press("a")
        self.assertEqual((kind, j["_i"]), ("applied", 0))
        st.remove_where(lambda x: x["_i"] == 0)
        self.assertEqual(len(st.all), 3)
        kind, j = st.press("x")
        st.remove_where(lambda x: x["company"] == j["company"])
        self.assertEqual(j["company"], "Beta")  # role 0 was removed, so the cursor sits on the next one
        self.assertEqual({x["company"] for x in st.all}, {"Gamma", "Acme"})

    def test_empty_state_does_not_crash(self):
        st = tui.State([])
        self.assertIsNone(st.job)
        for k in ("j", "k", 10, "a", "x", "o", "G", "?", "x"):
            st.press(k)
        rows = tui.plain(tui.frame(st, 24, 100))
        self.assertIn("No results yet", "\n".join(rows))


class FrameTests(unittest.TestCase):
    def sizes(self):
        return [(24, 80), (32, 120), (40, 200), (12, 50), (8, 30), (60, 100)]

    def test_every_row_fits_the_terminal_in_every_mode(self):
        many = [job(i, title="Very long role title " * 6, company="Company " * 5) for i in range(60)] + JOBS
        for jobs in (JOBS, many, []):
            for mode in ("list", "filter", "help"):
                for h, w in self.sizes():
                    st = tui.State(jobs, "Chrome")
                    st.mode = mode
                    rows = tui.frame(st, h, w)
                    self.assertEqual(len(rows), h)
                    for r in rows:
                        self.assertLessEqual(tui.spans_width(r), w, (mode, h, w, tui.plain([r])))

    def test_wide_terminal_shows_details_with_link_and_browser_name(self):
        text = "\n".join(tui.plain(tui.frame(tui.State(JOBS, "Chrome"), 32, 120)))
        self.assertIn("details", text)
        self.assertIn("https://x/0", text)
        self.assertIn("open in Chrome", text)
        self.assertIn("┃ Backend Engineer 0", text)  # the selection bar

    def test_narrow_terminal_is_list_only(self):
        text = "\n".join(tui.plain(tui.frame(tui.State(JOBS), 24, 80)))
        self.assertNotIn("details", text)
        self.assertIn("enter open", text)

    def test_scrolling_keeps_cursor_visible_and_shows_page_marker(self):
        st = tui.State([job(i) for i in range(100)])
        for _ in range(40):
            st.press("j")
        text = "\n".join(tui.plain(tui.frame(st, 24, 80)))
        self.assertIn("Backend Engineer 40", text)
        self.assertIn("41/100", text)

    def test_cells_and_clip_handle_wide_characters(self):
        self.assertEqual(tui.cells("日本"), 4)
        self.assertEqual(tui.cells("é"), 1)
        self.assertLessEqual(tui.cells(tui.clip("日本語のエンジニア", 7)), 7)
        self.assertEqual(tui.clip("short", 10), "short")

    def test_select_frame_marks_the_current_option(self):
        text = "\n".join(tui.plain(tui.select_frame("Job Radar", "What now?", [("a", "One"), ("b", "Two")], 1, 20, 60)))
        self.assertIn("> 2. Two", text)
        self.assertIn("  1. One", text)


class OpenUrlTests(unittest.TestCase):
    CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"

    def r(self, **kw):
        defaults = dict(env={}, wsl=True, platform="linux", exists=lambda p: p == self.CHROME, which=lambda n: None, globber=lambda p: [])
        defaults.update(kw)
        return openurl.resolve(**defaults)

    def test_wsl_finds_windows_chrome_automatically(self):
        self.assertEqual(self.r(), ([self.CHROME, "{url}"], "Chrome"))

    def test_environment_beats_settings_beats_auto(self):
        self.assertEqual(self.r(setting="/opt/ff {url} --new-tab", env={})[0], ["/opt/ff", "{url}", "--new-tab"])
        argv, label = self.r(setting="firefox", env={"JOBRADAR_BROWSER": "brave --incognito"})
        self.assertEqual((argv, label), (["brave", "--incognito", "{url}"], "brave"))

    def test_default_keyword_and_missing_chrome_fall_back_to_the_system_browser(self):
        self.assertEqual(self.r(setting="default")[0], None)
        self.assertEqual(self.r(exists=lambda p: False, setting="chrome")[1], "your default browser (Chrome not found)")
        self.assertEqual(self.r(exists=lambda p: False, which=lambda n: "/usr/bin/wslview" if n == "wslview" else None)[0], ["wslview", "{url}"])

    def test_per_user_chrome_install_is_found(self):
        user = "/mnt/c/Users/me/AppData/Local/Google/Chrome/Application/chrome.exe"
        argv, _ = self.r(exists=lambda p: p == user, globber=lambda p: [user])
        self.assertEqual(argv[0], user)

    def test_open_url_launches_detached_and_never_raises(self):
        calls = []
        ok, label = openurl.open_url("https://x/1", setting="", runner=lambda argv, **kw: calls.append((argv, kw)),
                                     env={}, wsl=True, exists=lambda p: p == self.CHROME, which=lambda n: None, globber=lambda p: [])
        self.assertEqual((ok, label), (True, "Chrome"))
        self.assertEqual(calls[0][0], [self.CHROME, "https://x/1"])
        self.assertTrue(calls[0][1]["start_new_session"])

        def boom(argv, **kw):
            raise OSError("no such file")
        ok, _ = openurl.open_url("https://x/1", setting="", runner=boom, env={}, wsl=True,
                                 exists=lambda p: p == self.CHROME, which=lambda n: None, globber=lambda p: [])
        self.assertFalse(ok)
        self.assertEqual(openurl.open_url("")[0], False)

    def test_native_windows_finds_chrome_from_the_standard_folders(self):
        env = {"ProgramFiles": r"C:\Program Files", "LOCALAPPDATA": r"C:\Users\me\AppData\Local"}
        per_user = str(Path(env["LOCALAPPDATA"]) / "Google" / "Chrome" / "Application" / "chrome.exe")
        argv, label = openurl.resolve(env=env, wsl=False, platform="win32", exists=lambda p: p == per_user, which=lambda n: None, globber=lambda p: [])
        self.assertEqual((argv, label), ([per_user, "{url}"], "Chrome"))
        machine_wide = str(Path(env["ProgramFiles"]) / "Google" / "Chrome" / "Application" / "chrome.exe")
        argv, _ = openurl.resolve(env=env, wsl=False, platform="win32", exists=lambda p: p == machine_wide, which=lambda n: None, globber=lambda p: [])
        self.assertEqual(argv[0], machine_wide)

    def test_native_windows_without_chrome_uses_the_default_browser(self):
        argv, label = openurl.resolve(env={"ProgramFiles": r"C:\Program Files"}, wsl=False, platform="win32",
                                      exists=lambda p: False, which=lambda n: None, globber=lambda p: [])
        self.assertEqual((argv, label), (None, "your default browser"))

    def test_detached_launch_options_differ_by_platform(self):
        self.assertEqual(openurl.detach_kwargs("posix"), {"start_new_session": True})
        self.assertEqual(openurl.detach_kwargs("nt"), {"creationflags": 0x208})

    def test_file_target_uses_windows_path_in_wsl_only(self):
        ran = subprocess.CompletedProcess([], 0, stdout="\\\\wsl.localhost\\Ubuntu\\tmp\\r.html\n")
        self.assertEqual(openurl.file_target("/tmp/r.html", wsl=True, which=lambda n: "/bin/wslpath", runner=lambda *a, **k: ran),
                         "\\\\wsl.localhost\\Ubuntu\\tmp\\r.html")
        self.assertEqual(openurl.file_target("/tmp/r.html", wsl=False), "file:///tmp/r.html")


class ThemeTests(unittest.TestCase):
    def test_every_theme_defines_every_color_role(self):
        roles = set(tui.THEMES["glow"])
        for name, t in tui.THEMES.items():
            self.assertEqual(set(t), roles, name)
            for k, v in t.items():
                if k == "label" or (k == "bg" and v is None):  # None = keep the terminal's own background
                    continue
                self.assertRegex(v, r"^#[0-9A-Fa-f]{6}$", f"{name}.{k}")

    def test_glow_theme_uses_the_colors_from_glows_source(self):
        t = tui.THEMES["glow"]
        self.assertEqual((t["accent"], t["badge_fg"], t["ok"], t["bg"]), ("#EE6FF8", "#ECFD65", "#04B575", None))

    def test_every_style_the_screen_uses_is_themed(self):
        used = set()
        many = [job(i, new=True) for i in range(30)] + JOBS
        for mode in ("list", "filter", "help"):
            st = tui.State(many)
            st.mode = mode
            st.query = "x" if mode == "list" else ""
            for r in tui.frame(st, 32, 120):
                used |= {style for _, style in r}
        themed = set(tui.styles_for(tui.THEMES["nord"]))
        self.assertLessEqual(used, themed | {"normal"}, used - themed)

    def test_to256_maps_known_colors(self):
        self.assertEqual(tui.to256("#000000"), 16)
        self.assertEqual(tui.to256("#FFFFFF"), 231)
        self.assertEqual(tui.to256("#FF0000"), 196)
        self.assertEqual(tui.to256("#808080"), 244)  # a gray lands on the gray ramp
        self.assertEqual(tui.to256("#EE6FF8"), 207)  # Glow's fuchsia

    def test_t_cycles_themes_and_flashes_the_name(self):
        st = tui.State(JOBS)
        names = list(tui.THEMES)
        st.press("t")
        self.assertEqual((st.theme, st.theme_changed), (names[1], True))
        self.assertIn(tui.THEMES[names[1]]["label"], st.flash)
        for _ in range(len(names) - 1):
            st.press("t")
        self.assertEqual(st.theme, names[0])

    def test_theme_choice_is_saved_and_read_back(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_text(json.dumps({"browser": "chrome"}))
            orig, env = tui.settings_file, os.environ.pop("JOBRADAR_THEME", None)
            tui.settings_file = lambda: path
            try:
                tui.save_theme("nord")
                self.assertEqual(json.loads(path.read_text()), {"browser": "chrome", "theme": "nord"})  # keeps other settings
                self.assertEqual(tui.load_theme(), "nord")
                os.environ["JOBRADAR_THEME"] = "dracula"
                self.assertEqual(tui.load_theme(), "dracula")  # environment wins
                os.environ["JOBRADAR_THEME"] = "nope"
                self.assertEqual(tui.load_theme(), "nord")  # unknown names are ignored
                path.write_text("not json")
                os.environ.pop("JOBRADAR_THEME")
                self.assertEqual(tui.load_theme(), "glow")
            finally:
                tui.settings_file = orig
                os.environ.pop("JOBRADAR_THEME", None)
                if env is not None:
                    os.environ["JOBRADAR_THEME"] = env

    def test_theme_preview_page_shows_every_theme_with_escaped_text(self):
        page = tui.theme_html([job(0, title="<b>Backend</b> & Co", new=True)])
        for t in tui.THEMES.values():
            self.assertIn(t["label"], page)
        self.assertIn("&lt;b&gt;Backend", page)
        self.assertNotIn("<b>Backend</b>", page)
        self.assertIn("#EE6FF8", page)


if __name__ == "__main__":
    unittest.main()
