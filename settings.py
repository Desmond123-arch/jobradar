"""Your settings: one settings.json next to the scripts. Everything here can also be changed from the menu
(python cli.py settings), so you never have to edit it by hand.

  cv         path to your CV (.pdf .docx .txt .md). Jobs are scored against the skills in it, and
             --roles cv keeps the titles that fit them. Empty: look for cv.* next to jobradar.py
  roles      ai | cv | web | any       which job titles to keep
  remote     a list of kinds         which remote roles you want. Tick any of: worldwide, africa, emea, europe,
                                       middle-east, americas, asia-pacific, my-timezone, unclear. A role shows if it
                                       matches any you ticked. (Older files with "region" are read as the matching list.)
  sources    list | agg | all          companies.txt only, plus aggregators, or plus every discoverable board
  no_senior  true | false              drop senior, staff, lead and manager titles
  show_all   true | false              also show region-locked and on-site roles
  days       1-365                     how far back to look
  timezone   auto | UTC+1 | UTC-8 ...  where you are. auto uses this computer's time zone
  tz_range   0-24                      hours from your time zone you are happy to work. 24 = don't filter
  onsite     true | false             also keep on-site and hybrid roles (not only remote ones)
  places     text                      where you would work on-site, e.g. "Accra, Ghana". Blank = anywhere
  open_report true | false             open report.html in your browser when a run finishes
  mouse      true | false             click and scroll in the full-screen screens (off lets you select text)
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
import scopes
import timezones

HERE = paths.data_dir()  # your files; see paths.py
FILE = HERE / "settings.json"

DEFAULTS = {"cv": "", "roles": "ai", "remote": list(scopes.DEFAULT), "sources": "list", "no_senior": False,
            "show_all": False, "days": 30, "browser": "", "theme": "glow", "open_report": False, "mouse": True,
            "timezone": "auto", "tz_range": 3, "onsite": False, "places": ""}

# value -> (menu label, short label)
OPTIONS = {
    "roles": [("ai", "AI and engineering titles", "AI and engineering"),
              ("cv", "Titles that fit the skills on your CV (a typical software list if you have none)", "match my CV"),
              ("web", "Backend, frontend, full-stack, software engineer", "web and software"),
              ("any", "Every role", "every role")],
    "sources": [("list", "Only the companies in companies.txt (fast)", "companies.txt only"),
                ("agg", "companies.txt plus the job sites (Remotive, Hacker News and more)", "plus job sites"),
                ("all", "Every discoverable board plus aggregators (first run is slow)", "every board")],
}
CHOICES = {k: tuple(v[0] for v in vs) for k, vs in OPTIONS.items()}
# Older settings files had a single "region" (and "show_unclear"). These are the lists they meant.
LEGACY_REMOTE = {"lagos": ["worldwide", "africa", "emea", "my-timezone"], "africa": ["worldwide", "africa", "emea", "my-timezone"],
                 "worldwide": ["worldwide", "my-timezone"], "emea": [k for k in scopes.EMEA_WIDE if k != "unclear"]}


def short(key, value):
    return next((s for v, _, s in OPTIONS[key] if v == value), str(value))


def _valid(key, value):
    if key == "remote":
        return scopes.valid(value)
    if key in CHOICES:
        return value in CHOICES[key]
    if key in ("no_senior", "show_all", "open_report", "mouse", "onsite"):
        return isinstance(value, bool)
    if key == "timezone":
        return timezones.valid(value)
    if key == "tz_range":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= timezones.ANY_RANGE
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
    if "remote" not in raw or not scopes.valid(raw.get("remote")):
        legacy = LEGACY_REMOTE.get(raw.get("region"))
        if legacy is not None:
            out["remote"] = list(legacy) + (["unclear"] if raw.get("show_unclear", True) is not False else [])
    out["remote"] = list(out["remote"])
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
    if s["remote"] != DEFAULTS["remote"]:
        argv += ["--remote", ",".join(s["remote"])]
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
    if s["timezone"] != DEFAULTS["timezone"]:
        argv += ["--timezone", s["timezone"]]
    if s["tz_range"] != DEFAULTS["tz_range"]:
        argv += ["--tz-range", str(s["tz_range"])]
    if s["onsite"]:
        argv.append("--onsite")
    if s["places"]:
        argv += ["--places", s["places"]]
    if s["cv"]:
        argv += ["--cv", s["cv"]]
    return argv


# ---------------------------------------------------------------- CV files

CV_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")
_CV_NAME = re.compile(r"cv|resume|résumé", re.I)


def _convert(text):
    """Quotes and spaces trimmed, ~ expanded, and a Windows path (C:\\Users\\me\\cv.pdf) turned into its WSL form
    (/mnt/c/Users/me/cv.pdf) when not on Windows. A relative path stays relative."""
    text = (text or "").strip().strip("\"'").strip()
    if not text:
        return ""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if m and os.name != "nt":
        return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
    return str(Path(text).expanduser())


def clean_path(text):
    """A path as typed or pasted, ready to save: tidied as in _convert, and made absolute from where you are now, so
    it still points at the same file when Job Radar is run from another folder."""
    path = _convert(text)
    if not path:
        return ""  # no CV stays no CV; Path("") would be "."
    return str(Path(path) if Path(path).is_absolute() else Path.cwd() / path)


def locate_cv(text, base):
    """The file a CV setting or --cv points at. A relative path is looked for in the folder you ran from, then in
    Job Radar's own folder, so a hand-written "cv.pdf" works wherever it is run from."""
    path = _convert(text)
    if not path or Path(path).is_absolute():
        return path
    for root in (Path.cwd(), Path(base)):
        if (root / path).exists():
            return str(root / path)
    return str(Path.cwd() / path)  # not found: a full path makes the error message say where it looked


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
