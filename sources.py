"""The job boards and sites results come from, with the names to show for them.

A result's `provider` is the key (what the code calls it); the label is what a person reads. They are kept in the
order they should appear in filters. A provider this list doesn't know still works: it is shown under its own key.
"""

SOURCES = [
    ("greenhouse", "Greenhouse"),
    ("lever", "Lever"),
    ("ashby", "Ashby"),
    ("remotive", "Remotive"),
    ("arbeitnow", "Arbeitnow"),
    ("jobicy", "Jobicy"),
    ("himalayas", "Himalayas"),
    ("remoteok", "RemoteOK"),
    ("hackernews", "Hacker News"),
]
NAMES = dict(SOURCES)
KEYS = [k for k, _ in SOURCES]


def name(key):
    return NAMES.get(key) or (str(key).title() if key else "Unknown")


def ordered(keys):
    """These provider keys in filter order: the known ones first, then any others alphabetically."""
    present = {k for k in keys if k}
    return [k for k in KEYS if k in present] + sorted(present - set(KEYS))
