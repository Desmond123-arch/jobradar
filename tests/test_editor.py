"""Opening companies.txt, exclude.txt and applied.txt in a text editor."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli  # noqa: E402
import editor  # noqa: E402
import jobradar as jr  # noqa: E402


def have(*names):
    return lambda n: f"/usr/bin/{n}" if n in names else None


class ResolveTests(unittest.TestCase):
    def test_environment_variables_come_first_in_order(self):
        env = {"EDITOR": "vi", "VISUAL": "code --wait", "JOBRADAR_EDITOR": "micro"}
        self.assertEqual(editor.resolve(env, "linux", which=have("nano"))[0], ["micro"])
        del env["JOBRADAR_EDITOR"]
        self.assertEqual(editor.resolve(env, "linux", which=have())[0], ["code", "--wait"])
        del env["VISUAL"]
        self.assertEqual(editor.resolve(env, "linux", which=have())[0], ["vi"])

    def test_blank_variables_are_ignored(self):
        self.assertEqual(editor.resolve({"EDITOR": "  "}, "linux", which=have("nano"))[0], ["nano"])

    def test_a_windows_command_with_spaces_in_its_path(self):
        env = {"EDITOR": '"C:\\Program Files\\Notepad++\\notepad++.exe" -multiInst'}
        self.assertEqual(editor.resolve(env, "win32")[0], ["C:\\Program Files\\Notepad++\\notepad++.exe", "-multiInst"])

    def test_each_system_without_any_variable(self):
        self.assertEqual(editor.resolve({}, "win32")[0], ["notepad"])
        self.assertEqual(editor.resolve({}, "darwin")[0], ["open", "-W", "-t"])
        self.assertEqual(editor.resolve({}, "linux", which=have("vim", "nano"))[0], ["nano"])
        self.assertEqual(editor.resolve({}, "linux", which=have("vi"))[0], ["vi"])

    def test_wsl_prefers_a_terminal_editor_then_windows_notepad(self):
        self.assertEqual(editor.resolve({}, "linux", wsl=True, which=have("nano", "notepad.exe"))[0], ["nano"])
        self.assertEqual(editor.resolve({}, "linux", wsl=True, which=have("notepad.exe"))[0], ["notepad.exe"])
        self.assertEqual(editor.resolve({}, "linux", wsl=False, which=have("notepad.exe"))[0], None)

    def test_a_desktop_without_a_terminal_editor_and_nothing_at_all(self):
        self.assertEqual(editor.resolve({}, "linux", which=have("xdg-open"))[0], ["xdg-open"])
        self.assertEqual(editor.resolve({}, "linux", which=have()), (None, ""))


class CommandTests(unittest.TestCase):
    def test_notepad_on_wsl_gets_a_windows_path(self):
        runner = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="C:\\x\\companies.txt\n")  # noqa: E731
        self.assertEqual(editor.command_for(Path("/home/a/companies.txt"), ["notepad.exe"], True, runner), ["notepad.exe", "C:\\x\\companies.txt"])

    def test_others_get_the_plain_path_and_a_failed_conversion_falls_back(self):
        self.assertEqual(editor.command_for(Path("/a/b.txt"), ["nano"], True), ["nano", "/a/b.txt"])
        boom = lambda *a, **k: (_ for _ in ()).throw(OSError())  # noqa: E731
        self.assertEqual(editor.command_for(Path("/a/b.txt"), ["notepad.exe"], True, boom), ["notepad.exe", "/a/b.txt"])


class EditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "companies.txt"
        self.said = []

    def go(self, writes=None, name="companies", env=None, which=have("nano"), runner=None, **kw):
        calls = []

        def fake(cmd, **k):
            calls.append(cmd)
            if writes is not None:
                self.path.write_text(writes, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="")
        code = editor.edit(name, self.path, jr.parse_company_line, say=self.said.append, runner=runner or fake,
                           env=env or {}, platform="linux", which=which, **kw)
        return code, calls, "\n".join(self.said)

    def test_it_opens_the_file_and_reports_a_change_keeping_a_backup(self):
        self.path.write_text("# mine\n")
        code, calls, text = self.go(writes="# mine\nhttps://boards.greenhouse.io/stripe\n")
        self.assertEqual((code, calls), (0, [["nano", str(self.path)]]))
        self.assertIn("Saved companies.txt", text)
        self.assertIn("companies.txt.bak", text)
        self.assertEqual((self.path.parent / "companies.txt.bak").read_text(), "# mine\n")

    def test_no_change_is_said_plainly(self):
        self.path.write_text("x\n")
        self.assertIn("No changes", self.go(writes="x\n")[2])

    def test_a_missing_file_is_created_first_from_the_starter(self):
        starter = self.path.parent / "starter.txt"
        starter.write_text("# starter\n")
        self.go(writes=None, starter=starter)
        self.assertEqual(self.path.read_text(), "# starter\n")

    def test_a_missing_file_without_a_starter_is_empty(self):
        self.go(writes=None)
        self.assertEqual(self.path.read_text(), "")

    def test_unreadable_company_lines_are_pointed_out(self):
        self.path.write_text("")
        text = self.go(writes="https://boards.greenhouse.io/stripe\nhttps://example.com/not-a-board\n")[2]
        self.assertIn("line 2 won't be read", text)
        self.assertNotIn("line 1", text)

    def test_other_lists_are_not_checked_as_companies(self):
        self.assertEqual(editor.problems("exclude", "anything at all\n", jr.parse_company_line), [])

    def test_no_editor_prints_the_path_and_how_to_name_one(self):
        code, calls, text = self.go(which=have())
        self.assertEqual((code, calls), (1, []))
        self.assertIn(str(self.path), text)
        self.assertIn("JOBRADAR_EDITOR", text)

    def test_an_editor_that_cannot_start_is_a_message_not_a_crash(self):
        def boom(cmd, **k):
            raise FileNotFoundError("nano")
        code, _, text = self.go(runner=boom)
        self.assertEqual(code, 1)
        self.assertIn("Open this file yourself", text)

    def test_the_screen_is_handed_back_before_the_editor_starts(self):
        order = []
        self.go(writes="", runner=lambda cmd, **k: order.append("editor") or SimpleNamespace(returncode=0), suspend=lambda: order.append("suspend"))
        self.assertEqual(order, ["suspend", "editor"])


class CliTests(unittest.TestCase):
    def run_cli(self, argv, answers=()):
        root = Path(tempfile.mkdtemp())
        lines, queue, calls = [], list(answers), []
        orig = cli.UI.__init__

        def init(self, *a, **k):
            orig(self, *a, **k)
            self.editor_runner = lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0)
        cli.UI.__init__ = init
        try:
            code = cli.main(argv, inp=lambda p: queue.pop(0), out=lines.append, root=root, fancy=False)
        finally:
            cli.UI.__init__ = orig
        return code, calls, "\n".join(lines), root

    def test_edit_with_a_name_opens_that_list(self):
        import editor as e
        orig = e.resolve
        e.resolve = lambda *a, **k: (["myeditor"], "myeditor")
        try:
            code, calls, text, root = self.run_cli(["edit", "exclude"])
        finally:
            e.resolve = orig
        self.assertEqual(calls, [["myeditor", str(root / "exclude.txt")]])
        self.assertTrue((root / "exclude.txt").exists())

    def test_an_unknown_list_name(self):
        code, calls, text, _ = self.run_cli(["edit", "nonsense"])
        self.assertEqual((code, calls), (1, []))
        self.assertIn("companies, exclude, applied", text)

    def test_the_menu_offers_it(self):
        lines = []
        cli.main([], inp=lambda p: "quit", out=lines.append, root=Path(tempfile.mkdtemp()), fancy=False)
        self.assertIn("Edit my lists", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
