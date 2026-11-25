"""Update Job Radar to the latest version on GitHub.

  jobradar update              see what is new, then update (asks first)
  jobradar update --check      only look; change nothing
  jobradar update --yes        update without asking
  python cli.py update         the same, from a folder

How it updates depends on how it was installed:

  a folder (git clone)     git pull --ff-only. Refuses if you have changes of your own, so nothing is lost.
  pipx                     pipx install --force, from GitHub
  uv tool                  uv tool install --force, from GitHub
  pip (a virtual env)      pip install --force-reinstall, from GitHub

Why "force"? The version number does not change with every commit, so "upgrade" would say you are up to date when
you are not. Your settings, results and company list live in your own folder and are never touched.
"""

import http.client
import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import net

REPO = "Desmond123-arch/jobradar"
GIT_URL = f"git+https://github.com/{REPO}.git"
API = f"https://api.github.com/repos/{REPO}"
SRC = Path(__file__).resolve().parent
UA = "jobradar-update (https://github.com/Desmond123-arch/jobradar)"

LABELS = {"checkout": "a folder (git clone)", "pipx": "pipx", "uv": "uv tool", "pip": "pip"}
# Everything that can go wrong talking to GitHub: no network, a dropped connection mid-answer, an odd answer.
NETWORK_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, KeyError, TypeError)


# ---------------------------------------------------------------- what is installed

def how_installed(src=SRC, prefix=None):
    """'checkout', 'pipx', 'uv' or 'pip'."""
    src = Path(src)
    if (src / ".git").exists() and "site-packages" not in src.parts:
        return "checkout"
    parts = [p.lower() for p in Path(prefix or sys.prefix).parts]
    if "pipx" in parts:
        return "pipx"
    if "uv" in parts and "tools" in parts:
        return "uv"
    return "pip"


def installed_commit(kind, src=SRC, runner=subprocess.run, dist=None):
    """The commit this copy is at, or None when it can't be told (installed from a local folder or a download)."""
    if kind == "checkout":
        try:
            done = runner(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15)
            return done.stdout.strip() or None if done.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        text = (dist or importlib.metadata.distribution("jobradar")).read_text("direct_url.json")
        return (json.loads(text).get("vcs_info") or {}).get("commit_id") if text else None
    except (importlib.metadata.PackageNotFoundError, ValueError, OSError, AttributeError):
        return None


def extras():
    """The optional extras this copy already uses, so an update keeps them."""
    have = set()
    if importlib.util.find_spec("pypdf") is not None:
        have.add("pdf")
    try:
        importlib.metadata.version("windows-curses")
        have.add("windows")
    except importlib.metadata.PackageNotFoundError:
        pass
    return "all" if have == {"pdf", "windows"} else ",".join(sorted(have))


def spec():
    e = extras()
    return f"jobradar[{e}] @ {GIT_URL}" if e else f"jobradar @ {GIT_URL}"


# ---------------------------------------------------------------- GitHub

def fetch_json(url, timeout=20, tries=2):
    """GET a GitHub API url as JSON. One retry, because a dropped connection is usually a blip."""
    net.install()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (http.client.HTTPException, ConnectionError, TimeoutError):
            if attempt + 1 == tries:
                raise


def latest_commit(fetch=fetch_json):
    data = fetch(f"{API}/commits/main")
    return data["sha"], (data.get("commit", {}).get("message") or "").splitlines()[0]


def whats_new(base, head, fetch=fetch_json, limit=8):
    """{'status': behind/ahead/identical/diverged, 'count': n, 'subjects': [...]}: how we stand against GitHub, with
    the new changes oldest first. Uses the small list of recent commits when our commit is in it, and GitHub's
    heavier compare only when it is not (we are ahead, or far behind)."""
    recent = fetch(f"{API}/commits?sha=main&per_page=50")
    shas = [c.get("sha") for c in recent]
    if base in shas:
        newer = recent[:shas.index(base)]
        subjects = [(c.get("commit", {}).get("message") or "").splitlines()[0] for c in reversed(newer)]
        return {"status": "behind" if newer else "identical", "count": len(newer), "subjects": subjects[-limit:]}
    data = fetch(f"{API}/compare/{base}...{head}?per_page=1")
    subjects = [(c.get("commit", {}).get("message") or "").splitlines()[0] for c in data.get("commits", [])]
    api = data.get("status")
    # GitHub says whether *head* is ahead of or behind base. Seen from our side (base is us) it is the other way round.
    if api == "ahead":
        status, count = "behind", data.get("ahead_by", len(subjects))     # GitHub has new changes we don't
    elif api == "behind":
        status, count = "ahead", data.get("behind_by", 0)                 # we have changes GitHub doesn't
    else:
        status, count = api, data.get("ahead_by", 0)
    return {"status": status, "count": count, "subjects": subjects[-limit:]}


# ---------------------------------------------------------------- how to update

def command(kind, which=shutil.which, python=None, src=SRC):
    """The command that updates this copy, as a list."""
    python = python or sys.executable
    s = spec()
    if kind == "checkout":
        return ["git", "-C", str(src), "pull", "--ff-only"]
    if kind == "pipx" and which("pipx"):
        return ["pipx", "install", "--force", s]
    if kind == "uv" and which("uv"):
        return ["uv", "tool", "install", "--force", s]
    return [python, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps", s]


def printable(cmd):
    return " ".join(f'"{c}"' if " " in c or "@" in c else c for c in cmd)


def dirty(src=SRC, runner=subprocess.run):
    """Does this folder have changes that a pull could trip over? (Untracked files don't count.)"""
    done = runner(["git", "-C", str(src), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=15)
    return bool(done.stdout.strip())


def branch(src=SRC, runner=subprocess.run):
    done = runner(["git", "-C", str(src), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=15)
    return done.stdout.strip()


def short(sha):
    return (sha or "?")[:7]


# ---------------------------------------------------------------- the command

def main(argv=(), say=print, ask=input, fetch=fetch_json, runner=subprocess.run, kind=None, src=SRC, which=shutil.which,
         windows=None):
    argv = list(argv)
    check_only = "--check" in argv
    assume_yes = "--yes" in argv or "-y" in argv
    windows = (os.name == "nt") if windows is None else windows
    kind = kind or how_installed(src)
    current = installed_commit(kind, src, runner)
    say(f"Job Radar is installed as {LABELS[kind]}, at {short(current)}." if current else
        f"Job Radar is installed as {LABELS[kind]}. Which version, I can't tell.")

    try:
        latest, headline = latest_commit(fetch)
    except NETWORK_ERRORS as exc:
        if net.MARKER in str(exc):
            say(net.FIX)
            return 1
        say(f"Couldn't reach GitHub to check ({type(exc).__name__}). Try again in a moment, or when you are online.")
        return 1

    if current and current == latest:
        say(f"You are up to date ({short(latest)}: {headline}).")
        return 0
    if current:
        try:
            news = whats_new(current, latest, fetch)
        except NETWORK_ERRORS:
            news = None  # the list of what is new is a nicety: the update itself does not need it
        if news and news["status"] == "ahead":
            say(f"You are {news['count']} change{'s' if news['count'] != 1 else ''} ahead of GitHub ({short(current)} vs {short(latest)}). Nothing to update.")
            return 0
        if news and news["status"] == "behind":
            say(f"{news['count']} new change{'s' if news['count'] != 1 else ''} on GitHub (latest {short(latest)}):")
            for subject in news["subjects"]:
                say(f"  - {subject}")
            if news["count"] > len(news["subjects"]):
                say(f"  ...and {news['count'] - len(news['subjects'])} older")
        else:
            say(f"GitHub has a different version ({short(latest)}: {headline}).")
    else:
        say(f"The latest on GitHub is {short(latest)}: {headline}.")
    if check_only:
        say("Run `jobradar update` to get it." if kind != "checkout" else "Run `python cli.py update` to get it.")
        return 0

    if kind == "checkout":
        try:
            if branch(src, runner) != "main":
                say(f"This folder is on the branch '{branch(src, runner)}', not main. Update it yourself with git, or switch to main first.")
                return 1
            if dirty(src, runner):
                say("This folder has changes of your own that haven't been committed. Commit or stash them, then run this again. Nothing was changed.")
                return 1
        except (OSError, subprocess.SubprocessError):
            say("Couldn't run git here. Is it installed?")
            return 1

    cmd = command(kind, which, src=src)
    if windows and kind != "checkout":
        say("\nOn Windows a program can't replace itself while it is running. Close this window, open a new one, and run:\n")
        say("  " + printable(cmd))
        say("\nYour settings, results and company list are kept.")
        return 0
    if not assume_yes:
        try:
            if ask("Update now? [Y/n] ").strip().lower().startswith("n"):
                say("Nothing was changed.")
                return 0
        except (EOFError, KeyboardInterrupt):
            say("\nNothing was changed.")
            return 1
    say(f"\nRunning: {printable(cmd)}\n")
    try:
        done = runner(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"\nCouldn't run that ({exc}). Run it yourself:\n  {printable(cmd)}")
        return 1
    if done.returncode != 0:
        say(f"\nThe update did not finish (exit code {done.returncode}). Nothing was lost; run it yourself to see why:\n  {printable(cmd)}")
        return done.returncode or 1
    say("\nUpdated. Start Job Radar again to use the new version. Your settings, results and company list are untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
