"""Reset parts of Job Radar back to a fresh state.

  python cli.py reset                 pick what to reset from a list
  python cli.py reset seen            or name it: seen results cache settings applied excluded companies all
  python cli.py reset all --yes       --yes skips the "are you sure?" question

Nothing here touches your CV, and nothing is destroyed without a way back for the files that took you effort
to build: your settings, applied list, excluded list and company list are copied to <name>.bak first (one
generation). The files Job Radar can rebuild by itself (results, the seen list, the board cache) are simply deleted.
"""

import shutil
from pathlib import Path

import paths

# key -> (what it does, files, how)
#   delete   remove the file
#   clear    keep the file but empty it, after a .bak copy
#   backup   .bak copy, then remove
#   restore  .bak copy, then put the starter copy back (installed copies only)
ITEMS = {
    "seen": ("Forget what I've seen, so every role shows as NEW again", ["seen.json"], "delete"),
    "results": ("Clear the last results and report", ["jobs.json", "report.html"], "delete"),
    "cache": ("Clear the board cache, so the next search rescans for boards", ["discovered.json"], "delete"),
    "settings": ("Put my settings back to the defaults", ["settings.json"], "backup"),
    "applied": ("Clear my applied list", ["applied.txt"], "clear"),
    "excluded": ("Clear my excluded companies", ["exclude.txt"], "clear"),
    "companies": ("Restore the starter company list", ["companies.txt"], "restore"),
}
LEFTOVERS = (".tmp", ".damaged")  # half-saved or set-aside copies from interrupted runs


def available(root):
    """The reset choices that make sense here. The starter company list only exists in an installed copy."""
    return [k for k in ITEMS if k != "companies" or paths.starter_file("companies.txt")]


def expand(keys, root):
    keys = list(keys)
    if "all" in keys:
        return available(root)
    unknown = [k for k in keys if k not in ITEMS]
    if unknown:
        raise ValueError(f"don't know how to reset {', '.join(unknown)} (choose from: {', '.join(list(ITEMS) + ['all'])})")
    return [k for k in keys if k in available(root)]


def plan(root, keys):
    """[(key, path, how)] for the files that exist right now, so nothing is announced that wouldn't happen."""
    root = Path(root)
    out = []
    for key in expand(keys, root):
        _, files, how = ITEMS[key]
        for name in files:
            if (root / name).exists() or how == "restore":
                out.append((key, root / name, how))
    return out


def describe(path, how):
    """What a reset will do to this file, in words."""
    name = path.name
    return {"delete": f"remove {name}",
            "backup": f"reset {name} to the defaults (the old one is kept as {name}.bak)",
            "clear": f"empty {name} (the old one is kept as {name}.bak)",
            "restore": f"restore the starter {name} (your current one is kept as {name}.bak)"}[how]


def _backup(path):
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + ".bak"))
        return f" (the old one is kept as {path.name}.bak)"
    return ""


def apply(root, keys):
    """Do the reset. Returns a list of plain sentences saying what happened."""
    root = Path(root)
    done = []
    for key, path, how in plan(root, keys):
        if how == "delete":
            path.unlink()
            done.append(f"removed {path.name}")
        elif how == "backup":
            note = _backup(path)
            path.unlink()
            done.append(f"reset {path.name}{note}")
        elif how == "clear":
            note = _backup(path)
            paths.atomic_write(path, "")
            done.append(f"cleared {path.name}{note}")
        elif how == "restore":
            starter = paths.starter_file(path.name)
            if starter:
                note = _backup(path)
                shutil.copyfile(starter, path)
                done.append(f"restored the starter {path.name}{note}")
    for leftover in root.glob("*"):
        if leftover.suffix in LEFTOVERS and leftover.is_file():
            leftover.unlink()
    return done
