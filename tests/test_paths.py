import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paths


class DataDirTest(unittest.TestCase):
    def test_checkout_uses_source_folder(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JOBRADAR_HOME", None)
            self.assertEqual(paths.data_dir(), paths.SRC)

    def test_env_folder_is_created_and_seeded(self):
        with tempfile.TemporaryDirectory() as d:
            starters = Path(d) / "share" / "jobradar"
            starters.mkdir(parents=True)
            (starters / "companies.txt").write_text("https://jobs.ashbyhq.com/acme\n")
            home = Path(d) / "home"
            with mock.patch.dict(os.environ, {"JOBRADAR_HOME": str(home)}), \
                    mock.patch.object(paths, "_starter_dirs", lambda: [starters]):
                self.assertEqual(paths.data_dir(), home)
            self.assertEqual((home / "companies.txt").read_text(), "https://jobs.ashbyhq.com/acme\n")

    def test_seed_never_overwrites_your_list(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "companies.txt").write_text("mine\n")
            paths.seed(Path(d))
            self.assertEqual((Path(d) / "companies.txt").read_text(), "mine\n")


if __name__ == "__main__":
    unittest.main()
