"""Your settings: one settings.json next to the scripts. Everything here can also be changed from the menu
(python cli.py settings), so you never have to edit it by hand.

  cv         path to your CV (.pdf .docx .txt .md). Jobs are scored against the skills in it, and
             --roles cv keeps the titles that fit them. Empty: look for cv.* next to jobradar.py
  roles      ai | cv | web | any       which job titles to keep
  region     lagos | emea              Africa and worldwide-remote roles, or anywhere in Europe/Middle East/Africa
  sources    list | agg | all          companies.txt only, plus aggregators, or plus every discoverable board
  no_senior  true | false              drop senior, staff, lead and manager titles
  show_all   true | false              also show region-locked and on-site roles
  days       1-365                     how far back to look
  open_report true | false             open report.html in your browser when a run finishes
  browser    chrome | default | a command   see openurl.py. Empty: automatic
  theme      glow | catppuccin | tokyonight | dracula | rosepine | gruvbox | nord

Command-line flags always win over this file, and --no-settings ignores it. A broken or hand-edited value
falls back to its default instead of stopping anything.
"""

import glob
import json
import os
import re
from pathlib import Path

import paths

HERE = paths.data_dir()  # your files; see paths.py
FILE = HERE / "settings.json"

DEFAULTS = {"cv": "", "roles": "ai", "region": "lagos", "sources": "list", "no_senior": False,
            "show_all": False, "days": 30, "browser": "", "theme": "glow", "open_report": False}

# value -> (menu label, short label)
OPTIONS = {
    "roles": [("ai", "AI and engineering titles", "AI and engineering"),
              ("cv", "Titles that fit the skills on your CV", "match my CV"),
              ("web", "Backend, frontend, full-stack, software engineer", "web and software"),
              ("any", "Every role", "every role")],
    "region": [("lagos", "Africa-worded locations and worldwide remote", "Africa and worldwide remote"),
               ("emea", "Anywhere in Europe, the Middle East or Africa, plus worldwide remote", "all of EMEA")],
    "sources": [("list", "Only the companies in companies.txt (fast)", "companies.txt only"),
                ("agg", "companies.txt plus the job sites (Remotive, Hacker News and more)", "plus job sites"),
                ("all", "Every discoverable board plus aggregators (first run is slow)", "every board")],
}
CHOICES = {k: tuple(v[0] for v in vs) for k, vs in OPTIONS.items()}


def short(key, value):
    return next((s for v, _, s in OPTIONS[key] if v == value), str(value))


def _valid(key, value):
    if key in CHOICES:
        return value in CHOICES[key]
    if key in ("no_senior", "show_all", "open_report"):
        return isinstance(value, bool)
    if key == "days":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 365
    return isinstance(value, str)  # cv, browser, theme


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load(path=None):
    """All settings, with a default for anything missing or invalid."""
    raw = _read(path or FILE)
    out = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if key in raw and _valid(key, raw[key]):
            out[key] = raw[key].strip() if isinstance(raw[key], str) else raw[key]
    return out


def save(path, updates):
    """Merge updates into the file, keeping anything else already in it. Raises ValueError on a bad value."""
    path = Path(path or FILE)
    for key, value in updates.items():
        if key in DEFAULTS and not _valid(key, value):
            raise ValueError(f"{value!r} is not a valid {key}")
    data = _read(path)
    data.update(updates)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def to_args(s):
    """The jobradar.py flags that mean the same as these settings."""
    argv = []
    if s["roles"] in ("cv", "web"):
        argv += ["--roles", s["roles"]]
    elif s["roles"] == "any":
        argv.append("--any-role")
    if s["region"] == "emea":
        argv.append("--emea")
    if s["sources"] in ("agg", "all"):
        argv.append("--aggregators")
    if s["sources"] == "all":
        argv.append("--discover")
    if s["no_senior"]:
        argv.append("--no-senior")
    if s["show_all"]:
        argv.append("--all")
    if s["days"] != DEFAULTS["days"]:
        argv += ["--days", str(s["days"])]
    if s["open_report"]:
        argv.append("--open")
    if s["cv"]:
        argv += ["--cv", s["cv"]]
    return argv


# ---------------------------------------------------------------- CV files

CV_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
_CV_NAME = re.compile(r"cv|resume|résumé", re.I)


def clean_path(text):
    """A path as typed or pasted: quotes and spaces trimmed, ~ expanded, and a Windows path
    (C:\\Users\\me\\cv.pdf) turned into its WSL form (/mnt/c/Users/me/cv.pdf) when not on Windows."""
    text = (text or "").strip().strip("\"'").strip()
    if not text:
        return ""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if m and os.name != "nt":
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return str(Path(text).expanduser())


def cv_search_dirs():
    home = Path.home()
    dirs = [HERE, home, home / "Downloads", home / "Documents", home / "Desktop"]
    for base in glob.glob("/mnt/c/Users/*"):  # the Windows side, from WSL
        dirs += [Path(base), Path(base) / "Downloads", Path(base) / "Documents", Path(base) / "Desktop"]
    return dirs


def find_cv_files(dirs=None, limit=8):
    """CV-looking files (cv/resume in the name, a readable type), newest first."""
    seen, found = set(), []
    for d in dirs if dirs is not None else cv_search_dirs():
        try:
            entries = list(Path(d).iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if p.suffix.lower() in CV_EXTENSIONS and _CV_NAME.search(p.stem) and p.is_file():
                    real = p.resolve()
                    if real not in seen:
                        seen.add(real)
                        found.append((p.stat().st_mtime, p))
            except OSError:
                continue
    return [p for _, p in sorted(found, key=lambda t: -t[0])[:limit]]


if __name__ == "__main__":
    for k, v in load().items():
        print(f"{k:10} {v!r}")
