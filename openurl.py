"""Open a link in the browser you want, including Windows Chrome from inside WSL.

Order of choice:
  1. the JOBRADAR_BROWSER environment variable
  2. "browser" in settings.json next to this file
  3. automatic: Windows Chrome when running in WSL, else the system default

The setting is either a keyword or a command:
  chrome      find Chrome on Windows (works from WSL), else google-chrome / chromium
  default     the system's default browser
  <command>   any command; "{url}" marks where the link goes, otherwise it is added at the end
              e.g.  "/mnt/c/Program Files/Mozilla Firefox/firefox.exe"
"""

import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

import paths

HERE = paths.data_dir()  # your files; see paths.py
WSL_CHROME = [
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]
WSL_CHROME_GLOB = "/mnt/c/Users/*/AppData/Local/Google/Chrome/Application/chrome.exe"


def in_wsl():
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def read_setting(root=HERE):
    import settings
    return settings.load(Path(root) / "settings.json")["browser"]


def windows_chrome_paths(env):
    rel = ("Google", "Chrome", "Application", "chrome.exe")
    return [str(Path(env[var]).joinpath(*rel)) for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if env.get(var)]


def find_chrome(wsl, exists=os.path.exists, which=shutil.which, globber=glob.glob, win=False, env=None):
    """Path or name of a Chrome we can launch, or None."""
    if wsl:
        for p in WSL_CHROME + globber(WSL_CHROME_GLOB):
            if exists(p):
                return p
    if win:  # native Windows (PowerShell, cmd)
        for p in windows_chrome_paths(os.environ if env is None else env):
            if exists(p):
                return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if which(name):
            return name
    return None


def resolve(setting="", env=None, wsl=None, platform=None, exists=os.path.exists, which=shutil.which, globber=glob.glob):
    """Returns (argv_template, label). argv_template is None for the system default (webbrowser)."""
    env = os.environ if env is None else env
    wsl = in_wsl() if wsl is None else wsl
    platform = platform or sys.platform
    choice = (env.get("JOBRADAR_BROWSER") or setting or "").strip()

    if choice and choice.lower() not in ("chrome", "default", "auto"):
        argv = shlex.split(choice, posix=(platform != "win32"))
        if "{url}" not in " ".join(argv):
            argv.append("{url}")
        return argv, Path(argv[0]).stem or argv[0]
    if choice.lower() == "default":
        return None, "your default browser"

    # "chrome" or automatic
    chrome = find_chrome(wsl, exists, which, globber, win=(platform == "win32"), env=env)
    if chrome:
        return [chrome, "{url}"], "Chrome"
    if choice.lower() == "chrome":
        return None, "your default browser (Chrome not found)"
    if wsl and which("wslview"):
        return ["wslview", "{url}"], "your Windows default browser"
    return None, "your default browser"


def file_target(path, wsl=None, which=shutil.which, runner=subprocess.run):
    """What to hand a browser for a local file. From WSL a Windows browser needs the Windows path (\\\\wsl.localhost\\...)."""
    path = Path(path)
    wsl = in_wsl() if wsl is None else wsl
    if wsl and which("wslpath"):
        try:
            out = runner(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                return out
        except Exception:
            pass
    return path.as_uri()


def detach_kwargs(name=None):
    """Popen options that let the browser outlive this program: a new session on POSIX, a detached process on Windows."""
    if (name or os.name) == "nt":
        return {"creationflags": 0x00000008 | 0x00000200}  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return {"start_new_session": True}


def open_url(url, setting=None, runner=subprocess.Popen, fallback=webbrowser.open, **kw):
    """Open url. Returns (ok, label); never raises, so a missing browser can't crash the UI."""
    if not url:
        return False, "no link"
    argv, label = resolve(read_setting() if setting is None else setting, **kw)
    try:
        if argv is None:
            return bool(fallback(url)), label
        runner([a.replace("{url}", url) for a in argv], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, **detach_kwargs())
        return True, label
    except Exception:
        return False, label


if __name__ == "__main__":
    print(resolve(read_setting()))
