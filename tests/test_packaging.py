"""The package list in pyproject.toml has to name every module, or an installed copy is missing it."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def listed():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"py-modules\s*=\s*\[(.*?)\]", text, re.S).group(1)
    return set(re.findall(r'"([^"]+)"', block))


def shipped():
    return {p.stem for p in ROOT.glob("*.py")}


class PackagingTests(unittest.TestCase):
    def test_every_top_level_module_is_in_the_package_list(self):
        self.assertEqual(shipped() - listed(), set(), "add these to [tool.setuptools] py-modules in pyproject.toml")

    def test_nothing_listed_is_missing(self):
        self.assertEqual(listed() - shipped(), set())

    def test_every_import_between_shipped_modules_resolves(self):
        std = set(sys.stdlib_module_names)
        optional = {"pypdf", "curses"}
        for module in sorted(listed()):
            for line in (ROOT / f"{module}.py").read_text(encoding="utf-8").splitlines():
                found = re.match(r"\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
                if found:
                    name = found.group(1)
                    self.assertTrue(name in std or name in listed() or name in optional, f"{module}.py imports {name}")

    def test_the_commands_point_at_real_functions(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        import entry
        for target in re.findall(r'=\s*"entry:(\w+)"', text):
            self.assertTrue(callable(getattr(entry, target)), target)


if __name__ == "__main__":
    unittest.main()
