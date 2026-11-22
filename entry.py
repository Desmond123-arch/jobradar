"""Console commands created by `pipx install` / `uv tool install` (see pyproject.toml)."""

import sys


def menu():
    """jobradar: the interactive menu (python cli.py)."""
    import cli
    sys.exit(cli.main(sys.argv[1:]))


def search():
    """jobradar-search: a run straight from the command line (python jobradar.py)."""
    import jobradar
    sys.exit(jobradar.main(sys.argv[1:]))


def cv():
    """jobradar-cv: show the skills found in a CV (python cvmatch.py)."""
    import cvmatch
    sys.exit(cvmatch.main(sys.argv[1:]))
