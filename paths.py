"""Where jobradar keeps your files: companies.txt, exclude.txt, settings.json, seen.json, the report.

  - $JOBRADAR_HOME, when set.
  - The source folder, when you run from a git checkout (companies.txt sits next to the code).
  - ~/.jobradar otherwise, i.e. after `pipx install` / `uv tool install`. The first run copies the
    starter companies.txt and exclude.txt there; after that the files are yours to edit.
"""

import os
import shutil
import site
import sys
import sysconfig
from pathlib import Path

SRC = Path(__file__).resolve().parent
STARTERS = ("companies.txt", "exclude.txt", "settings.example.json")


def _starter_dirs():
    """Where the installer put the starter files ([tool.setuptools.data-files] -> <prefix>/share/jobradar)."""
    prefixes = [sys.prefix, sysconfig.get_path("data"), getattr(site, "USER_BASE", None)]
    return [Path(p) / "share" / "jobradar" for p in prefixes if p]


def seed(folder):
    for name in STARTERS:
        target = folder / name
        if target.exists():
            continue
        for d in _starter_dirs():
            if (d / name).exists():
                shutil.copyfile(d / name, target)
                break
        else:
            if name == "companies.txt":
                target.write_text("# One careers URL per line, e.g. https://jobs.ashbyhq.com/<company>\n", encoding="utf-8")


def atomic_write(path, text, encoding="utf-8"):
    """Save a file so that Ctrl+C, a crash or a closed window can never leave half of it: write a temporary
    copy next to it, then swap it in. Readers see the old file or the new one, never a cut-off one."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def starter_file(name):
    """The starter copy of a file that the installer shipped, or None when running from a git checkout."""
    for d in _starter_dirs():
        if (d / name).exists():
            return d / name
    return None


def data_dir():
    env = os.environ.get("JOBRADAR_HOME")
    if env:
        folder = Path(env).expanduser()
    elif (SRC / "companies.txt").exists():
        return SRC
    else:
        folder = Path.home() / ".jobradar"
    folder.mkdir(parents=True, exist_ok=True)
    seed(folder)
    return folder


if __name__ == "__main__":
    print(data_dir())
