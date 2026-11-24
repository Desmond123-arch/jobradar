"""Open one of your lists (companies.txt, exclude.txt, applied.txt) in a text editor.

  jobradar edit              choose a list
  jobradar edit companies    open companies.txt (also: exclude, applied)

Which editor, first match wins:
  1. JOBRADAR_EDITOR, then VISUAL, then EDITOR (the usual variables; a command with arguments is fine)
  2. Windows: Notepad.  macOS: your default text editor.
  3. Linux and WSL: nano, micro, vim or vi, whichever is installed; on WSL then Windows Notepad; on Linux then
     whatever your desktop opens text files with.
If none can start, the file's path is printed so you can open it yourself.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

LISTS = {
    "companies": ("companies.txt", "Companies to search"),
    "exclude": ("exclude.txt", "Companies to skip"),
    "applied": ("applied.txt", "Roles you have applied for"),
}
TERMINAL_EDITORS = ["nano", "micro", "vim", "vi"]


def split(command, windows):
    """A command typed as one string, as a list. Windows paths keep their backslashes."""
    tokens = shlex.split(command, posix=not windows)
    return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]


def resolve(env=None, platform=None, wsl=False, which=shutil.which):
    """(command start as a list, label), or (None, '') when no editor can be found."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    windows = platform == "win32"
    for var in ("JOBRADAR_EDITOR", "VISUAL", "EDITOR"):
        text = (env.get(var) or "").strip()
        if text:
            argv = split(text, windows)
            if argv:
                return argv, Path(argv[0].replace("\\", "/")).name
    if windows:
        return ["notepad"], "Notepad"
    if platform == "darwin":
        return ["open", "-W", "-t"], "your default text editor"
    for name in TERMINAL_EDITORS:
        if which(name):
            return [name], name
    if wsl and which("notepad.exe"):
        return ["notepad.exe"], "Windows Notepad"
    if which("xdg-open"):
        return ["xdg-open"], "your desktop's text editor"
    return None, ""


def windows_path(path, runner=subprocess.run):
    """A WSL path as Windows spells it, for Notepad. The original if it can't be converted."""
    try:
        done = runner(["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=10)
        return done.stdout.strip() or str(path) if done.returncode == 0 else str(path)
    except (OSError, subprocess.SubprocessError):
        return str(path)


def command_for(path, argv, wsl=False, runner=subprocess.run):
    target = windows_path(path, runner) if wsl and argv[0].lower().endswith("notepad.exe") else str(path)
    return list(argv) + [target]


def ensure(path, starter=None):
    """Make sure the file exists so an editor opens it rather than complaining."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if starter and Path(starter).exists():
        path.write_text(Path(starter).read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")


def problems(name, text, parse):
    """Lines of companies.txt that can't be understood: [(line number, text, why)]."""
    if name != "companies":
        return []
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            parse(line)
        except ValueError as exc:
            found.append((number, line.strip(), str(exc)))
    return found


def edit(name, path, parse, say=print, runner=subprocess.run, env=None, platform=None, wsl=False, which=shutil.which,
         starter=None, suspend=lambda: None):
    """Open the list, then say what changed. A .bak copy is made first. Returns 0, or 1 when no editor started."""
    ensure(path, starter)
    before = path.read_text(encoding="utf-8", errors="replace")
    argv, label = resolve(env, platform, wsl, which)
    if argv is None:
        say(f"No text editor found. Open this file yourself:\n  {path}\n"
            "Or name one with the JOBRADAR_EDITOR variable, for example  JOBRADAR_EDITOR=nano")
        return 1
    backup = path.with_name(path.name + ".bak")
    try:
        backup.write_text(before, encoding="utf-8")
    except OSError:
        pass
    suspend()
    say(f"Opening {path.name} in {label}. Save and close it to come back.")
    try:
        runner(command_for(path, argv, wsl, runner))
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"Couldn't start {label} ({exc}). Open this file yourself:\n  {path}")
        return 1
    after = path.read_text(encoding="utf-8", errors="replace")
    if after == before:
        say(f"No changes to {path.name}.")
        return 0
    say(f"Saved {path.name}. The old one is kept as {backup.name}. It is used on your next search.")
    for number, line, why in problems(name, after, parse):
        say(f"  line {number} won't be read: {line}  ({why})")
    return 0
