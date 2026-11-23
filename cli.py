"""Interactive menu for jobradar. Run:  python cli.py

  1  Search        answer a few questions, jobradar runs with the matching flags
  2  Browse        full-screen list of the last results: filter, sort, press Enter to open a posting
  3  Add company   paste a careers URL into companies.txt
  4  Open report   open report.html in your browser
  5  Settings      your CV, roles, which remote roles, time zone, on-site, companies, days, browser, theme

  python cli.py browse    go straight to the full-screen browser
  python cli.py settings  go straight to settings
  python cli.py reset     put things back to a fresh start (asks first; keeps a .bak of your lists)
  python cli.py themes    preview every color theme in your browser
  python cli.py --plain   plain prompts instead of the full-screen interface

In a terminal it uses arrow-key menus and a full-screen browser (see tui.py). Piped or without curses it falls
back to numbered prompts. Links open in Windows Chrome from WSL (see openurl.py). No third-party packages.
"""

import json
import math
import re
import shlex
import sys
from pathlib import Path

import cvmatch
import jobradar as jr
import openurl
import paths
import reset
import scopes
import settings
import sources
import timezones

PAGE = 10
REGION_TAG = {"africa-emea": "AFRICA/EMEA", "emea": "EMEA", "worldwide": "WORLDWIDE", "unclear": "CHECK LOC",
              "restricted": "LOCKED", "onsite": "ON-SITE", "africa": "AFRICA", "europe": "EUROPE", "middle-east": "MIDDLE EAST",
              "americas": "AMERICAS", "asia-pacific": "ASIA-PACIFIC", "my-timezone": "MY TIME ZONE", "nearby": "ON-SITE OK",
              "timezone": "OTHER TIME ZONE"}


class Quit(Exception):
    pass


class Back(Exception):
    """Esc in a full-screen menu: leave this screen."""


class UI:
    fancy = False
    cv_dirs = None  # where to look for CV files; None means the usual places

    def real_console(self):
        """True when a person is typing at a terminal (not piped, not a test)."""
        return self.inp is input and sys.stdin.isatty() and sys.stdout.isatty()

    def prefers_web(self):
        """A real console without curses (native Windows PowerShell or cmd): the HTML report is the better browser."""
        import tui
        return tui.curses is None and self.real_console()

    def __init__(self, root=jr.HERE, inp=input, out=print, runner=jr.main, opener=None):
        self.root, self.inp, self.out, self.runner = Path(root), inp, out, runner
        self.opener = opener or (lambda url: openurl.open_url(url)[0])

    def say(self, text=""):
        self.out(text)

    def ask(self, prompt):
        try:
            return self.inp(f"{prompt} ").strip()
        except (EOFError, KeyboardInterrupt):
            raise Quit

    def close(self):
        pass

    def choose(self, prompt, options, default, preview=False):
        """options: [(key, label)]. Enter picks the default; a number or the key itself works.
        preview is only used by the full-screen version, to show a color theme as you move over it."""
        self.say(f"\n{prompt}")
        for i, (key, label) in enumerate(options, 1):
            self.say(f"  {i}. {label}{'  (default)' if key == default else ''}")
        keys = [k for k, _ in options]
        while True:
            raw = self.ask(">").lower()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(keys):
                return keys[int(raw) - 1]
            if raw in keys:
                return raw
            self.say(f"  pick 1-{len(keys)}")

    def checklist(self, prompt, options, selected):
        """Tick any number of options. Plain version: type the numbers to switch on or off, blank when done."""
        ticked = set(selected)
        while True:
            self.say(f"\n{prompt}")
            for i, (key, label) in enumerate(options, 1):
                self.say(f"  {i}. [{'x' if key in ticked else ' '}] {label}")
            raw = self.ask("Numbers to tick or untick (blank when done):")
            if not raw:
                return [k for k, _ in options if k in ticked]
            for token in re.split(r"[,\s]+", raw):
                if token.isdigit() and 1 <= int(token) <= len(options):
                    ticked ^= {options[int(token) - 1][0]}

    def yes(self, prompt, default=False):
        raw = self.ask(f"{prompt} [{'Y/n' if default else 'y/N'}]").lower()
        return default if not raw else raw.startswith("y")

    def number(self, prompt, default):
        while True:
            raw = self.ask(f"{prompt} [{default}]")
            if not raw:
                return default
            if raw.isdigit() and int(raw) > 0:
                return int(raw)
            self.say("  enter a whole number")

    def path(self, name):
        return self.root / name


class FancyUI(UI):
    """Same questions, as arrow-key menus (gum style) on one full-screen session. Messages that would have been
    printed are held and shown under the next menu's prompt, so they are seen instead of flashing past."""
    fancy = True

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        import atexit
        import tui
        self.screen, self.notes = tui.Screen(), []
        self._real_runner, self.runner = self.runner, self._run
        atexit.register(self.close)

    def say(self, text=""):
        if str(text).strip():
            self.notes.append(text)

    def close(self):
        """Back to the normal terminal, printing anything still held."""
        self.screen.close()
        held, self.notes = self.notes, []
        for note in held:
            self.out(note)

    def _run(self, argv):
        """A search prints as it goes, so it gets the normal terminal, cleared and headed, and the screen waits for
        Enter afterwards: otherwise the next full-screen menu would cover the result before it could be read."""
        self.screen.close()
        held, self.notes = self.notes, []
        self.out("\x1b[2J\x1b[HJob Radar \u00b7 searching\n")
        for note in held:
            self.out(note)
        code = self._real_runner(argv)
        try:
            self.inp("\nPress Enter to go back to the menu ")
        except (EOFError, KeyboardInterrupt):
            pass
        return code

    def ask(self, prompt, placeholder=""):
        """A line of text, typed inside the full-screen session (no dropping out to the plain terminal)."""
        import tui
        notes, self.notes = self.notes, []
        text = tui.run_input("Job Radar", prompt.strip().rstrip(":").strip(), screen=self.screen, placeholder=placeholder, notes=notes)
        if text is None:
            raise Back
        return text.strip()

    def number(self, prompt, default):
        while True:
            text = self.ask(prompt, placeholder=f"{default}  (press Enter to keep it)")
            if not text:
                return default
            if text.isdigit() and int(text) > 0:
                return int(text)
            self.say("  Enter a whole number, like 14.")

    def choose(self, prompt, options, default, preview=False):
        import tui
        notes, self.notes = self.notes, []
        picked = tui.run_select("Job Radar", prompt, options, default, screen=self.screen, notes=notes, preview=preview)
        if picked is None:
            raise Back
        return picked

    def checklist(self, prompt, options, selected):
        import tui
        notes, self.notes = self.notes, []
        picked = tui.run_checklist("Job Radar", prompt, options, selected, screen=self.screen, notes=notes)
        if picked is None:
            raise Back
        return picked

    def yes(self, prompt, default=False):
        return self.choose(prompt, [("y", "Yes"), ("n", "No")], "y" if default else "n") == "y"


# ---------------------------------------------------------------- search

REMOTE_PROMPT = "Which remote roles do you want?"
REMOTE_HINT = "Tick every kind you want. A role shows if it matches any of them."


def remote_options():
    return [(key, f"{label}: {meaning}") for key, label, meaning, _ in scopes.CATEGORIES]


def pick_remote(ui, current):
    ui.say("  " + REMOTE_HINT)
    return ui.checklist(REMOTE_PROMPT, remote_options(), current)


def build_args(ui, s):
    """Ask the search questions, starting from the saved settings. Returns (jobradar flags, the chosen values)."""
    def opts(key, which=1):
        return [(v, row[which]) for row in settings.OPTIONS[key] for v in [row[0]]]
    role = ui.choose("Which roles?", opts("roles"), s["roles"])
    remote = pick_remote(ui, s["remote"])
    sources = ui.choose("Which companies?", opts("sources"), s["sources"])
    no_senior = ui.yes("Drop senior, staff, lead and manager titles?", s["no_senior"])
    show_all = ui.yes("Also show region-locked and on-site roles?", s["show_all"])
    days = ui.number("Posted within how many days?", s["days"])
    chosen = dict(s, roles=role, remote=remote, sources=sources, no_senior=no_senior, show_all=show_all, days=days)
    return settings.to_args(chosen), chosen


SEARCH_SUMMARY = ["roles", "remote", "timezone", "tz_range", "onsite", "sources", "no_senior", "days"]


def search(ui):
    """Search with the saved settings, by default straight away. Changing things for one search, or editing the
    saved settings first, are choices, not a questionnaire you have to answer every time."""
    path = ui.path("settings.json")
    while True:
        saved = settings.load(path)
        for key in SEARCH_SUMMARY:
            ui.say(f"  {NAMES[key]:<22}{describe(key, saved[key])}")
        choice = ui.choose("Search with your saved settings?", [
            ("run", "Search now"),
            ("change", "Change a few answers for this search only"),
            ("settings", "Edit my saved settings first")], "run")
        if choice == "settings":
            try:
                edit_settings(ui)
            except Back:
                pass
            continue  # and back to this question, now with the new settings
        break
    if choice == "run":
        argv = settings.to_args(saved)
    else:
        argv, chosen = build_args(ui, saved)
        ui.say("\nThis is the same as:  python jobradar.py " + shlex.join(argv))
        if not ui.yes("Run it now?", True):
            return
        if chosen != saved and ui.yes("Save these answers as your saved settings?", False):
            settings.save(path, chosen)
    if "--discover" in argv and not ui.path("discovered.json").exists():
        ui.say("First discovery scan: expect a few extra minutes while it collects company boards.")
    ui.runner(["--no-settings"] + argv)  # the answers above are the whole story, not settings.json on top
    if "--open" in argv and not ui.fancy:
        return  # the report is already open in your browser
    if ui.yes("\nBrowse the results?", True):
        browse(ui)


# ---------------------------------------------------------------- settings

FIELDS = ["cv", "roles", "remote", "timezone", "tz_range", "onsite", "places", "sources", "no_senior", "show_all", "days",
          "browser", "theme", "open_report", "mouse"]
NAMES = {"cv": "CV file", "roles": "Roles", "remote": "Remote roles", "sources": "Companies", "no_senior": "Drop senior titles",
         "show_all": "Show locked roles", "days": "Look back (days)", "browser": "Browser", "theme": "Color theme",
         "open_report": "Open report after run", "mouse": "Mouse", "timezone": "Time zone", "tz_range": "Time zone range",
         "onsite": "On-site roles", "places": "On-site places"}


def describe(key, value):
    import tui
    if key == "cv":
        return Path(value).name if value else "not set (looks for cv.* next to jobradar.py)"
    if key in settings.OPTIONS:
        return settings.short(key, value)
    if key == "mouse":
        return "on" if value else "off"
    if key == "remote":
        return scopes.short(value)
    if key == "timezone":
        return timezones.describe(value)
    if key == "tz_range":
        return timezones.range_label(value)
    if key == "places":
        return value or "anywhere"
    if key in ("no_senior", "show_all", "open_report", "onsite"):
        return "yes" if value else "no"
    if key == "browser":
        return value or "automatic"
    if key == "theme":
        return tui.THEMES.get(value, tui.THEMES[tui.DEFAULT_THEME])["label"]
    return str(value)


def pick_cv(ui, path):
    found = settings.find_cv_files(ui.cv_dirs)
    current = settings.load(path)["cv"]
    options = [(str(p), f"{p.name}   ({p.parent})") for p in found] + [("__type", "Type or paste a path"), ("__clear", "Don't use a CV")]
    keys = [k for k, _ in options]
    default = current if current in keys else (keys[0] if found else "__type")
    pick = ui.choose("Which file is your CV?", options, default)
    if pick == "__clear":
        settings.save(path, {"cv": ""})
        ui.say("  no CV: jobs won't be scored against one")
        return
    if pick == "__type":
        pick = settings.clean_path(ui.ask("\nPath to your CV (.pdf .docx .txt .md):"))
        if not pick:
            return
    if not Path(pick).is_file():
        ui.say(f"  can't find a file at {pick}")
        return
    try:
        cv = cvmatch.load(pick)
    except Exception as exc:
        ui.say(f"  {exc}")
        return
    settings.save(path, {"cv": pick})
    ui.say(f"  read {len(cv.skills)} skills from {Path(pick).name}: {', '.join(sorted(cv.skills))}")


def edit_one(ui, key, path):
    s = settings.load(path)
    if key == "cv":
        pick_cv(ui, path)
    elif key in settings.OPTIONS:
        opts = [(row[0], row[1]) for row in settings.OPTIONS[key]]
        settings.save(path, {key: ui.choose(NAMES[key], opts, s[key])})
    elif key == "remote":
        picked = pick_remote(ui, s["remote"])
        settings.save(path, {"remote": picked})
    elif key == "timezone":
        options = [(timezones.AUTO, f"Detect from this computer (now {timezones.fmt(timezones.system_offset())})")]
        options += [(timezones.key_for(o), f"{timezones.fmt(o):<9}{places}") for o, places in timezones.ZONES]
        current = s["timezone"] if s["timezone"] in dict(options) else timezones.AUTO
        settings.save(path, {"timezone": ui.choose("Your time zone", options, current)})
    elif key == "tz_range":
        options = [(str(h), label) for h, label in timezones.RANGES]
        picked = ui.choose("How far from your time zone are you happy to work?", options, str(s["tz_range"]) if str(s["tz_range"]) in dict(options) else "3")
        settings.save(path, {"tz_range": int(picked)})
    elif key == "places":
        text = ui.ask("\nWhere would you work on-site? For example: Accra, Ghana (blank = anywhere):")
        settings.save(path, {"places": text})
    elif key in ("no_senior", "show_all", "open_report", "mouse", "onsite"):
        question = {"no_senior": "Drop senior, staff, lead and manager titles?", "show_all": "Also show region-locked and on-site roles?",
                    "onsite": "Also keep on-site and hybrid roles, not only remote ones?",
                    "open_report": "Open report.html in your browser when a run finishes?",
                    "mouse": "Use the mouse in the full-screen screens? (turn off to select and copy text)"}[key]
        settings.save(path, {key: ui.yes(question, s[key])})
    elif key == "days":
        settings.save(path, {"days": min(365, ui.number("Look back how many days?", s["days"]))})
    elif key == "browser":
        text = ui.ask("\nBrowser: chrome, default, or a command (blank = automatic):")
        settings.save(path, {"browser": text})
    elif key == "theme":
        import tui
        settings.save(path, {"theme": ui.choose("Color theme", [(k, t["label"]) for k, t in tui.THEMES.items()],
                                                s["theme"] if s["theme"] in tui.THEMES else tui.DEFAULT_THEME, preview=True)})


def reset_flow(ui, keys=None, assume_yes=False):
    """Choose what to reset, show exactly what will happen, ask, then do it. Returns an exit code."""
    root = ui.root
    if not keys:
        options = [(k, reset.ITEMS[k][0]) for k in reset.available(root)] + [("all", "Everything above")]
        keys = [ui.choose("What do you want to reset?", options, "seen")]
    try:
        todo = reset.plan(root, keys)
    except ValueError as exc:
        ui.say(str(exc))
        return 2
    if not todo:
        ui.say("  Nothing to reset: those files don't exist yet.")
        return 0
    ui.say("This will:")
    for _, path, how in todo:
        ui.say("  - " + reset.describe(path, how))
    if not assume_yes and not ui.yes("Go ahead?", False):
        ui.say("  Nothing was changed.")
        return 0
    for line in reset.apply(root, keys):
        ui.say("  " + line)
    return 0


def edit_settings(ui):
    """The settings list: pick a row to change it. Saved to settings.json as you go."""
    path = ui.path("settings.json")
    last = "cv"
    while True:
        s = settings.load(path)
        wide = max(len(n) for n in NAMES.values()) + 2
        options = [(k, f"{NAMES[k]:<{wide}}{describe(k, s[k])}") for k in FIELDS] + [("reset", "Reset things..."), ("done", "Done")]
        key = ui.choose("Settings", options, last)
        if key == "done":
            return
        last = key
        try:
            if key == "reset":
                reset_flow(ui)
            else:
                edit_one(ui, key, path)
        except Back:
            pass  # Esc inside a question: back to this list


def first_run(ui):
    """Once, the first time in a real terminal: the questions that matter, then write every setting out."""
    path = ui.path("settings.json")
    ui.say("Welcome to Job Radar. A few questions to set it up. Esc skips one, and Settings changes them any time.")
    for key in ("cv", "roles", "remote", "timezone", "tz_range", "sources", "theme", "open_report"):
        try:
            edit_one(ui, key, path)
        except Back:
            continue
    settings.save(path, settings.load(path))


# ---------------------------------------------------------------- browse

def load_jobs(ui):
    path = ui.path("jobs.json")
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []


def matches(job, f):
    if f["new"] and not job.get("new"):
        return False
    if f["region"] and job.get("region") != f["region"]:
        return False
    if f.get("board") and job.get("provider") != f["board"]:
        return False
    text = f["text"].lower()
    if text and text not in " ".join([job.get("title", ""), job.get("company", ""), job.get("location", ""),
                                      sources.name(job.get("provider"))]).lower():
        return False
    return True


def describe_filters(f):
    parts = []
    if f["text"]:
        parts.append(f'text "{f["text"]}"')
    if f["region"]:
        parts.append(REGION_TAG.get(f["region"], f["region"]))
    if f.get("board"):
        parts.append(sources.name(f["board"]))
    if f["new"]:
        parts.append("new only")
    return ", ".join(parts) or "none"


def append_line(path, line):
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    paths.atomic_write(path, existing + sep + line + "\n")


def mark_applied(ui, job):
    append_line(ui.path("applied.txt"), f"{job['company']} | {job['title']}")
    msg = f"saved to applied.txt: {job['company']} | {job['title']}"
    ui.say("  " + msg)
    return msg


def exclude_company(ui, job):
    known = {jr.squash(l) for l in jr.read_lines(ui.path("exclude.txt"))}
    if jr.squash(job["company"]) not in known:
        append_line(ui.path("exclude.txt"), job["company"])
    msg = f"{job['company']} will be skipped from now on (exclude.txt)"
    ui.say("  " + msg)
    return msg


def detail(ui, job):
    """Show one role. Returns 'applied', 'excluded' or None."""
    ui.say("\n" + "-" * 70)
    ui.say(f"{job['title']}\n{job['company']} · {job.get('location') or 'no location given'}")
    ui.say(f"{REGION_TAG.get(job.get('region'), job.get('region'))}: {job.get('region_reason', '')}")
    ui.say(f"posted {(job.get('posted') or '?')[:10]}" + (f" · {job['pay']}" if job.get("pay") else "")
           + f" · fit {job.get('score', 0)}" + (" · NEW" if job.get("new") else ""))
    if job.get("why"):
        ui.say("why: " + "; ".join(job["why"]))
    if job.get("description"):
        ui.say("\n" + job["description"].strip()[:400])
    ui.say("\n" + job.get("url", ""))
    while True:
        cmd = ui.ask("\n[o]pen in browser · [a]pplied · [x] exclude company · [b]ack >").lower()
        if cmd in ("b", "", "back"):
            return None
        if cmd == "o":
            try:
                ui.opener(job.get("url", ""))
            except Exception:
                pass
            ui.say("  opened (or copy the link above)")
        elif cmd == "a":
            mark_applied(ui, job)
            return "applied"
        elif cmd == "x":
            exclude_company(ui, job)
            return "excluded"


def browse(ui):
    if ui.fancy:
        return browse_fullscreen(ui)
    if ui.prefers_web():
        ui.say("\nOpening the interactive report in your browser: search, filter and sort there.")
        return open_report(ui)
    return browse_plain(ui)


def browse_fullscreen(ui):
    import tui
    jobs = load_jobs(ui)
    if not jobs:
        ui.say("\nNo results yet. Run a search first.")
        return
    quiet = UI(ui.root, out=lambda text: None)

    def open_job(job):
        ok, label = openurl.open_url(job.get("url", ""))
        return f"Opened in {label}" if ok else f"Couldn't open {label}. Link: {job.get('url', '')}"

    handlers = {"open": open_job, "applied": lambda j: mark_applied(quiet, j), "exclude": lambda j: exclude_company(quiet, j)}
    tui.run_browser(jobs, handlers, openurl.resolve(openurl.read_setting())[1], screen=ui.screen)


def browse_plain(ui):
    jobs = load_jobs(ui)
    if not jobs:
        ui.say("\nNo results yet. Run a search first.")
        return
    f = {"text": "", "region": "", "board": "", "new": False}
    page = 0
    while True:
        shown = [j for j in jobs if matches(j, f)]
        pages = max(1, math.ceil(len(shown) / PAGE))
        page = min(page, pages - 1)
        ui.say(f"\n{len(shown)} of {len(jobs)} roles · filters: {describe_filters(f)} · page {page + 1}/{pages}")
        for i, j in enumerate(shown[page * PAGE:(page + 1) * PAGE], page * PAGE + 1):
            tag = REGION_TAG.get(j.get("region"), "?")
            ui.say(f"{i:>4}. [{tag}]{' NEW' if j.get('new') else ''} {j['company']}: {j['title']}"
                   f"  ({(j.get('location') or '')[:40]})")
        cmd = ui.ask("\n[number] open · [n]ext · [p]rev · [f]ilter · [q]uit >").lower()
        if cmd in ("q", "b", "quit"):
            return
        if cmd == "n":
            page = min(page + 1, pages - 1)
        elif cmd == "p":
            page = max(page - 1, 0)
        elif cmd == "f":
            sub = ui.choose("Filter by", [("text", "Text in title, company or location"), ("region", "Region"),
                                          ("board", "Job board"), ("new", "New only (toggle)"), ("clear", "Clear all filters")], "text")
            if sub == "text":
                f["text"] = ui.ask("Text (blank to clear):")
            elif sub == "region":
                f["region"] = ui.choose("Region", [("", "Any")] + [(k, v.title()) for k, v in REGION_TAG.items()], "")
            elif sub == "board":
                present = sources.ordered(j.get("provider") for j in jobs)
                f["board"] = ui.choose("Job board", [("", "Any")] + [(k, sources.name(k)) for k in present], "")
            elif sub == "new":
                f["new"] = not f["new"]
            else:
                f.update(text="", region="", board="", new=False)
            page = 0
        elif cmd.isdigit() and 1 <= int(cmd) <= len(shown):
            job = shown[int(cmd) - 1]
            result = detail(ui, job)
            if result == "applied":
                jobs.remove(job)
            elif result == "excluded":
                jobs = [j for j in jobs if jr.squash(j["company"]) != jr.squash(job["company"])]
        else:
            ui.say("  not a command")


# ---------------------------------------------------------------- the rest

def add_company(ui):
    line = ui.ask("\nCareers URL (Greenhouse, Lever or Ashby), optionally '| Name':")
    if not line:
        return
    try:
        c = jr.parse_company_line(line)
    except ValueError as exc:
        ui.say(f"  {exc}")
        return
    if c is None:
        return
    have = {(x["provider"], x["slug"].lower()) for x in (jr.parse_company_line(l) for l in jr.read_lines(ui.path("companies.txt")) if l.strip() and not l.startswith("#")) if x}
    if (c["provider"], c["slug"].lower()) in have:
        ui.say(f"  {c['name']} is already in companies.txt")
        return
    append_line(ui.path("companies.txt"), line)
    ui.say(f"  added {c['name']} ({c['provider']}:{c['slug']})")


def show_themes(ui):
    """Write themes.html (every color theme, rendered with your own results) and open it."""
    import tui
    jobs = load_jobs(ui) or [{"title": "Backend Engineer", "company": "Acme", "location": "Berlin", "region": "emea",
                              "region_reason": "location in EMEA: Berlin", "posted": "2026-11-05", "score": 9, "new": True,
                              "why": ["engineering role"], "url": "https://example.com/1", "description": "Python and Go services."}]
    path = ui.path("themes.html")
    path.write_text(tui.theme_html(jobs), encoding="utf-8")
    ui.say(f"wrote {path}")
    ui.opener(openurl.file_target(path))
    return 0


def open_report(ui):
    report = ui.path("report.html")
    if not report.exists():
        ui.say("\nNo report yet. Run a search first.")
        return
    ui.say(f"\n{report}")
    try:
        if ui.opener(openurl.file_target(report)) is False:
            ui.say("  Couldn't start a browser. Open that file yourself, or set one in Settings.")
    except Exception:
        pass


def main(argv=(), inp=input, out=print, root=jr.HERE, runner=jr.main, opener=None, fancy=None, cv_dirs=None):
    argv = list(argv)
    if fancy is None:  # only a real terminal gets the full-screen interface
        import tui
        fancy = "--plain" not in argv and inp is input and tui.available()
    ui = (FancyUI if fancy else UI)(root, inp, out, runner, opener)
    ui.cv_dirs = cv_dirs
    menu_ran = False
    words = [a for a in argv if not a.startswith("-")]
    command = words[0] if words else None  # only the first word picks the command: "reset settings" resets settings
    try:
        if command == "settings":
            try:
                edit_settings(ui)
            except Back:
                pass
            return 0
        if ui.real_console() and not ui.path("settings.json").exists():
            first_run(ui)
        if not fancy and ui.prefers_web():
            ui.say("Tip: for the full-screen interface, run  pip install windows-curses  (or use WSL). "
                   "Until then, Browse opens the interactive report in your browser.")
        if command == "reset":
            try:
                return reset_flow(ui, words[1:] or None, assume_yes=("--yes" in argv or "-y" in argv))
            except Back:
                return 0
        if command == "browse":
            try:
                browse(ui)
            except Back:
                pass
            return 0
        if command == "themes":
            return show_themes(ui)
        if not fancy:
            ui.say("Job Radar")
        menu_ran = True
        while True:
            try:
                choice = ui.choose("What now?", [("search", "Search for jobs"), ("browse", "Browse the last results"),
                                                 ("add", "Add a company"), ("report", "Open report.html"),
                                                 ("settings", "Settings"), ("quit", "Quit")], "search")
            except Back:
                break
            try:
                if choice == "search":
                    search(ui)
                elif choice == "browse":
                    browse(ui)
                elif choice == "add":
                    add_company(ui)
                elif choice == "report":
                    open_report(ui)
                elif choice == "settings":
                    edit_settings(ui)
                else:
                    break
            except Back:
                continue
    except (Quit, KeyboardInterrupt):
        ui.say("")
    finally:
        if menu_ran:
            ui.say("bye")
        ui.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
