"""A full-screen terminal browser for jobradar results, styled after Charm's Glow and gum.

  python cli.py browse

Standard library only (curses). The screen is built by pure functions (frame, select_frame) that return
rows of (text, style) pieces, so they can be tested and previewed without a terminal; paint() draws them.
"""

import locale
import os
import textwrap
import time
import unicodedata

try:
    import curses
except ImportError:  # native Windows Python has no curses; run the CLI from WSL
    curses = None

ROUND = {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│"}
BAR, DOT_ON, DOT_OFF, ELLIPSIS, SEP, CURSOR_BLOCK = "┃", "●", "○", "…", " • ", "█"

REGION_STYLE = {"africa-emea": "ok", "emea": "ok", "worldwide": "ok", "unclear": "warn", "restricted": "bad", "onsite": "bad"}
REGION_NAME = {"africa-emea": "AFRICA/EMEA", "emea": "EMEA", "worldwide": "WORLDWIDE", "unclear": "CHECK LOCATION",
               "restricted": "LOCKED", "onsite": "ON-SITE"}
REGION_ORDER = ["africa-emea", "emea", "worldwide", "unclear", "restricted", "onsite"]
SORTS = [("best", "best match"), ("fit", "fit score"), ("date", "newest"), ("company", "company A-Z")]
ITEM_ROWS = 3  # title, meta, blank: the same rhythm Glow uses
DOUBLE_CLICK = 0.45  # seconds between two clicks on the same role that count as a double-click
KEY_FOR_LABEL = {"enter": 10, "esc": 27, "ctrl+u": 21, "/": "/", "r": "r", "n": "n", "s": "s", "t": "t", "a": "a",
                 "x": "x", "?": "?", "q": "q"}  # the key hints along the bottom are buttons


# ---------------------------------------------------------------- text helpers

def cells(s):
    """Terminal width of s: wide (CJK, emoji) characters take two cells, combining marks none."""
    n = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def clip(s, width):
    if width <= 0:
        return ""
    if cells(s) <= width:
        return s
    out, used = [], 0
    for ch in s:
        w = cells(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + ELLIPSIS


def spans_width(spans):
    return sum(cells(t) for t, _ in spans)


def fit_spans(spans, width):
    """Trim a row to width cells, never splitting the layout."""
    out, used = [], 0
    for text, style in spans:
        room = width - used
        if room <= 0:
            break
        piece = text if cells(text) <= room else clip(text, room)
        out.append((piece, style))
        used += cells(piece)
    return out


def pad_spans(spans, width):
    spans = fit_spans(spans, width)
    gap = width - spans_width(spans)
    return spans + [(" " * gap, "normal")] if gap > 0 else spans


def right_align(left, right, width):
    """left spans, then right spans flush to the right edge; the left side gives way first."""
    rw = spans_width(right)
    left = fit_spans(left, max(0, width - rw - 1))
    gap = width - spans_width(left) - rw
    return left + [(" " * max(gap, 0), "normal")] + right


def plain(rows):
    return ["".join(t for t, _ in r) for r in rows]


# ---------------------------------------------------------------- state

def parse_query(query):
    """'python "remote first" -sales' -> (must_have, must_not). Words are ANDed; "quoted phrases" stay whole."""
    must, must_not, buf, quoted = [], [], "", False
    for ch in query + " ":
        if ch == '"':
            quoted = not quoted
        elif ch == " " and not quoted:
            if buf:
                if buf.startswith("-") and len(buf) > 1:
                    must_not.append(buf[1:])
                else:
                    must.append(buf)
            buf = ""
        else:
            buf += ch
    return [m.lower() for m in must if m], [m.lower() for m in must_not if m]


def haystack(job):
    return " ".join([job.get("title", ""), job.get("company", ""), job.get("location", ""),
                     " ".join(job.get("why") or []), job.get("description", "")]).lower()


def key_of(raw):
    """Normalise curses input (int or str) to (code, char)."""
    if isinstance(raw, str):
        if len(raw) == 1 and ord(raw) < 32 or raw == "\x7f":
            return ord(raw), ""
        return (ord(raw) if len(raw) == 1 else -1), raw
    return raw, (chr(raw) if 32 <= raw < 127 else "")


class State:
    """Everything the screen shows. press() turns a keystroke into an event for the caller to carry out."""

    def __init__(self, jobs, browser_label="your browser", theme="glow"):
        self.all = [dict(j, _i=i) for i, j in enumerate(jobs)]
        self.browser_label = browser_label
        self.theme, self.theme_changed = theme, False
        self.hits, self._clicked, self._clicked_at = [], None, 0.0
        self.query, self.region, self.new_only, self.sort = "", "", False, "best"
        self.cursor = self.top = 0
        self.mode = "list"  # list | filter | help
        self.detail = True
        self.flash = ""
        self.per_page = 5
        self.refresh()

    # -- data
    @property
    def regions(self):
        present = {j.get("region") for j in self.all}
        return [""] + [r for r in REGION_ORDER if r in present]

    def refresh(self):
        must, must_not = parse_query(self.query)
        shown = []
        for j in self.all:
            if self.new_only and not j.get("new"):
                continue
            if self.region and j.get("region") != self.region:
                continue
            h = haystack(j)
            if all(m in h for m in must) and not any(m in h for m in must_not):
                shown.append(j)
        keyf = {"best": lambda j: j["_i"], "fit": lambda j: (-j.get("score", 0), j["_i"]),
                "date": lambda j: ("" if not j.get("posted") else j["posted"]), "company": lambda j: (j.get("company", "").lower(), j["_i"])}[self.sort]
        shown.sort(key=keyf, reverse=(self.sort == "date"))
        self.shown = shown
        self.cursor = max(0, min(self.cursor, len(shown) - 1))

    @property
    def job(self):
        return self.shown[self.cursor] if self.shown else None

    def remove_where(self, pred):
        self.all = [j for j in self.all if not pred(j)]
        self.refresh()

    def scroll_into_view(self):
        per = max(1, self.per_page)
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + per:
            self.top = self.cursor - per + 1
        self.top = max(0, min(self.top, max(0, len(self.shown) - per)))

    @property
    def filters_active(self):
        return bool(self.query or self.region or self.new_only)

    # -- input
    def move(self, delta):
        self.cursor = max(0, min(self.cursor + delta, len(self.shown) - 1))
        self.flash = ""

    def press(self, raw):
        code, ch = key_of(raw)
        if self.mode == "help":
            self.mode = "list"
            return None
        if self.mode == "filter":
            return self._press_filter(code, ch)
        keys = dict(down=getattr(curses, "KEY_DOWN", 258), up=getattr(curses, "KEY_UP", 259),
                    npage=getattr(curses, "KEY_NPAGE", 338), ppage=getattr(curses, "KEY_PPAGE", 339),
                    home=getattr(curses, "KEY_HOME", 262), end=getattr(curses, "KEY_END", 360),
                    enter=getattr(curses, "KEY_ENTER", 343))
        if code in (keys["down"],) or ch == "j":
            self.move(1)
        elif code == keys["up"] or ch == "k":
            self.move(-1)
        elif code == keys["npage"] or ch == " ":
            self.move(self.per_page)
        elif code == keys["ppage"] or ch == "b":
            self.move(-self.per_page)
        elif code == keys["home"] or ch == "g":
            self.cursor = 0
        elif code == keys["end"] or ch == "G":
            self.cursor = max(0, len(self.shown) - 1)
        elif code in (10, 13, keys["enter"]) or ch == "o":
            return ("open", self.job) if self.job else None
        elif ch == "/":
            self.mode = "filter"
        elif ch == "r":
            r = self.regions
            self.region = r[(r.index(self.region) + 1) % len(r)] if self.region in r else ""
            self.cursor = 0
            self.refresh()
        elif ch == "n":
            self.new_only = not self.new_only
            self.cursor = 0
            self.refresh()
        elif ch == "s":
            names = [k for k, _ in SORTS]
            self.sort = names[(names.index(self.sort) + 1) % len(names)]
            self.refresh()
        elif ch == "c":
            self.query, self.region, self.new_only = "", "", False
            self.cursor = 0
            self.refresh()
        elif ch == "d":
            self.detail = not self.detail
        elif ch == "t":
            names = list(THEMES)
            self.theme = names[(names.index(self.theme) + 1) % len(names)]
            self.theme_changed = True
            self.flash = f"theme: {THEMES[self.theme]['label']}"
        elif ch == "a":
            return ("applied", self.job) if self.job else None
        elif ch == "x":
            return ("exclude", self.job) if self.job else None
        elif ch == "?":
            self.mode = "help"
        elif ch == "q":
            return ("quit", None)
        elif code == 27:
            if self.filters_active:
                self.query, self.region, self.new_only = "", "", False
                self.cursor = 0
                self.refresh()
            else:
                return ("quit", None)
        return None

    # -- mouse
    def hit_at(self, x, y):
        """The clickable thing under a screen cell, as (action, argument), from the last frame drawn."""
        for y0, y1, x0, x1, action, arg in reversed(self.hits):
            if y0 <= y <= y1 and x0 <= x < x1:
                return action, arg
        return None

    def mouse(self, kind, x, y, now=None):
        """Handle a wheel turn or a left click. Returns an event like press() does, or None."""
        now = time.monotonic() if now is None else now
        if kind in ("up", "down"):
            if self.mode == "list":
                self.move(-1 if kind == "up" else 1)
            return None
        if kind != "click":
            return None
        if self.mode == "help":
            self.mode = "list"
            return None
        hit = self.hit_at(x, y)
        if not hit:
            return None
        action, arg = hit
        if action == "item":
            if self.mode == "filter":
                self.mode = "list"
            again = self._clicked == arg and now - self._clicked_at < DOUBLE_CLICK
            self.cursor, self.flash = arg, ""
            self._clicked, self._clicked_at = (None, 0.0) if again else (arg, now)
            return ("open", self.job) if again and self.job else None
        if action == "open":
            return ("open", self.job) if self.job else None
        if action == "key":
            return self.press(arg)
        return None

    def _press_filter(self, code, ch):
        backspace = (127, 8, getattr(curses, "KEY_BACKSPACE", 263))
        if code in (10, 13, getattr(curses, "KEY_ENTER", 343)):
            self.mode = "list"
        elif code == 27:
            self.query, self.mode = "", "list"
        elif code in backspace:
            self.query = self.query[:-1]
        elif code == 21:  # ctrl-u
            self.query = ""
        elif ch and ch.isprintable():
            self.query += ch
        self.cursor = 0
        self.refresh()
        return None


# ---------------------------------------------------------------- frames

def border_top(width, style="border", title=""):
    inner = width - 2
    label = f" {title} " if title else ""
    return [(ROUND["tl"] + ROUND["h"] + label + ROUND["h"] * max(0, inner - 1 - cells(label)) + ROUND["tr"], style)]


def boxed(rows, width, height, title=""):
    """Wrap rows in a rounded border exactly width x height."""
    inner_w, inner_h = width - 4, height - 2
    out = [border_top(width, "border", title)]
    for i in range(inner_h):
        body = pad_spans(rows[i] if i < len(rows) else [], inner_w)
        out.append([(ROUND["v"] + " ", "border")] + body + [(" " + ROUND["v"], "border")])
    out.append([(ROUND["bl"] + ROUND["h"] * (width - 2) + ROUND["br"], "border")])
    return out


def wrap_rows(text, width, style="normal"):
    rows = []
    for para in text.split("\n"):
        lines = textwrap.wrap(para, width=max(8, width), break_long_words=True, break_on_hyphens=False) or [""]
        rows += [[(l, style)] for l in lines]
    return rows


def chips(job):
    out = []
    if job.get("new"):
        out.append((" NEW ", "new"))
    tag = REGION_NAME.get(job.get("region"), "")
    if tag:
        out += ([(" ", "normal")] if out else []) + [(tag, REGION_STYLE.get(job.get("region"), "dim"))]
    return out


def item_rows(job, selected, width):
    bar = (BAR + " ", "bar") if selected else ("  ", "normal")
    inner = width - 2
    title = [bar, (clip(job.get("title", ""), inner), "sel_title" if selected else "title")]
    chip = chips(job)
    where = job.get("location") or "no location given"
    left = [bar, (job.get("company", ""), "sel_meta" if selected else "meta"), (" · " + where, "dim")]
    return [title, right_align(left, chip, width), []]


def detail_rows(job, width, browser_label):
    if not job:
        return [[("Nothing selected", "dim")]]
    rows = [[(job.get("title", ""), "sel_title")]]
    rows += [[(job.get("company", ""), "meta"), (" · " + (job.get("location") or "no location given"), "dim")], []]
    rows += [chips(job)] if chips(job) else []
    if job.get("region_reason"):
        rows += wrap_rows(job["region_reason"], width, "dim")
    facts = [f"posted {(job.get('posted') or '?')[:10]}", f"fit {job.get('score', 0)}"]
    if job.get("pay"):
        facts.insert(1, job["pay"])
    rows += [[(SEP.join(facts), "dim")], []]
    if job.get("why"):
        rows += wrap_rows("; ".join(job["why"]), width, "accent") + [[]]
    if job.get("description"):
        rows += wrap_rows(job["description"].strip(), width, "normal") + [[]]
    rows += [[(clip(job.get("url", ""), width), "link")], []]
    rows += [[("enter", "key"), (f" open in {browser_label}", "dim")]]
    return rows


def help_pairs():
    return [("↑/↓  j/k", "move"), ("pgup/pgdn", "page"), ("g / G", "top / bottom"), ("enter / o", "open the posting in your browser"),
            ("/", "filter: words are ANDed, \"quotes\" keep phrases, -word excludes"), ("r", "cycle region"),
            ("n", "new roles only"), ("s", "cycle sort"), ("c", "clear all filters"), ("d", "show or hide the details pane"), ("t", "cycle the color theme (remembered)"),
            ("a", "mark applied (saved to applied.txt)"), ("x", "exclude this company (saved to exclude.txt)"),
            ("esc", "clear filters, then quit"), ("q", "quit"),
            ("mouse", "click a role to select it, double-click to open it, scroll to move, click the hints below")]


def help_line(pairs):
    out = []
    for i, (k, d) in enumerate(pairs):
        out += ([(SEP, "sep")] if i else []) + [(k, "key"), (" " + d, "dim")]
    return out


def help_hits(pairs, y):
    """Click targets for a help_line drawn on row y: each key and its words together."""
    hits, x = [], 0
    for i, (k, d) in enumerate(pairs):
        if i:
            x += cells(SEP)
        width = cells(k) + cells(" " + d)
        if k in KEY_FOR_LABEL:
            hits.append((y, y, x, x + width, "key", KEY_FOR_LABEL[k]))
        x += width
    return hits


def frame(state, h, w):
    """The whole screen as h rows of (text, style) spans, each at most w cells wide.
    As a side effect it records state.hits: the rectangles on screen that a click can act on."""
    rows = []
    state.hits = []
    n_new = sum(1 for j in state.all if j.get("new"))
    head = [(" Job Radar ", "badge"), ("  " + f"{len(state.shown):,} of {len(state.all):,} roles", "dim")]
    if n_new:
        head += [(SEP, "sep"), (f" {n_new:,} new ", "new")]
    rows.append(head)

    if state.mode == "filter":
        rows.append([("/ ", "accent"), (state.query, "query"), (CURSOR_BLOCK, "accent")])
    else:
        parts = [(f"sort: {dict(SORTS)[state.sort]}", "dim")]
        if state.query:
            parts.append((f'filter: "{state.query}"', "accent"))
        if state.region:
            parts.append((f"region: {REGION_NAME[state.region].title()}", "accent"))
        if state.new_only:
            parts.append(("new only", "accent"))
        row = []
        for i, p in enumerate(parts):
            row += ([(SEP, "sep")] if i else []) + [p]
        rows.append(row)

    body_top = 2 if h < 24 else 3
    if body_top == 3:
        rows.append([])
    foot = 2
    body_h = max(3, h - body_top - foot)

    if state.mode == "help":
        pairs = help_pairs()
        kw = max(cells(k) for k, _ in pairs)
        inner = [[(k.ljust(kw) + "  ", "key"), (d, "dim")] for k, d in pairs]
        box_w = min(w, max(40, kw + 4 + max(cells(d) for _, d in pairs) + 4))
        box = boxed(inner, box_w, min(body_h, len(inner) + 2), "keys")
        rows += [[(" " * ((w - box_w) // 2), "normal")] + r for r in box]
    else:
        wide = state.detail and w >= 100
        list_w = max(46, int(w * 0.46)) if wide else w
        per = max(1, body_h // ITEM_ROWS)
        state.per_page = per
        state.scroll_into_view()
        left = []
        if not state.shown:
            left = [[], [("No roles match.", "dim")], [("Press c to clear the filters.", "dim")]] if state.all else \
                   [[], [("No results yet. Run a search first.", "dim")]]
        for idx in range(state.top, min(len(state.shown), state.top + per)):
            y = body_top + (idx - state.top) * ITEM_ROWS
            state.hits.append((y, y + ITEM_ROWS - 1, 0, list_w - (2 if wide else 0), "item", idx))
            left += item_rows(state.shown[idx], idx == state.cursor, list_w - (2 if wide else 0))
        left = [pad_spans(r, list_w - (2 if wide else 0)) for r in left[:body_h]]
        left += [[(" " * (list_w - (2 if wide else 0)), "normal")] for _ in range(body_h - len(left))]
        if wide:
            right_w = w - list_w
            detail = detail_rows(state.job, right_w - 4, state.browser_label)
            for r, spans in enumerate(detail[:body_h - 2]):
                first = spans[0] if spans else ("", "")
                if state.job and (first[1] == "link" or first == ("enter", "key")):  # the link, and the "enter open in ..." line
                    state.hits.append((body_top + 1 + r, body_top + 1 + r, list_w + 2, w - 2, "open", None))
            right = boxed(detail, right_w, body_h, "details")
            for l, r in zip(left, right):
                rows.append(l + [("  ", "normal")] + r)
        else:
            rows += left

    pages = max(1, -(-len(state.shown) // max(1, state.per_page)))
    page = state.cursor // max(1, state.per_page)
    if pages <= 1:
        dots = []
    elif pages <= 12:
        dots = [(DOT_ON if i == page else DOT_OFF, "accent" if i == page else "faint") for i in range(pages)]
        dots = [x for d in dots for x in (d, (" ", "normal"))]
    else:
        dots = [(f"{state.cursor + 1:,}/{len(state.shown):,}", "dim")]
    status = [(state.flash, "accent")] if state.flash else []
    rows.append(right_align(dots, status, w))
    if state.mode == "filter":
        footer = [("enter", "apply"), ("esc", "cancel"), ("ctrl+u", "clear")]
    else:
        footer = [("↑/↓", "move"), ("enter", "open"), ("/", "filter"), ("r", "region"), ("n", "new"),
                  ("s", "sort"), ("t", "theme"), ("a", "applied"), ("x", "exclude"), ("?", "more"), ("q", "quit")]
    state.hits += help_hits(footer, len(rows))
    rows.append(help_line(footer))
    rows = [fit_spans(r, w) for r in rows[:h]]
    return rows + [[] for _ in range(h - len(rows))]


SAMPLE_JOBS = [
    dict(title="Backend Engineer, Payments", company="Northwind Labs", location="Berlin, Germany", region="emea", new=True),
    dict(title="Full Stack Engineer", company="Helio Systems", location="Remote - EMEA", region="africa-emea", new=False),
]


def theme_sample(width):
    """A small mock job list, so a theme can be judged on more than a menu."""
    w = min(width, 72)
    rows = [[("  This is how the list will look", "dim")], []]
    for i, job in enumerate(SAMPLE_JOBS):
        rows += [[("  ", "normal")] + r for r in item_rows(job, i == 0, w - 2)]
    return rows


def select_frame(title, prompt, options, cursor, h, w, hint="↑/↓ move • enter select • esc cancel", notes=(), extra=(), hits=None):
    """gum-style single choice list: a pink > marks the current line. notes are short messages shown under the
    prompt; extra rows go below the options. The key hints are always the final row returned, even when the
    content has to be cut to fit a short window."""
    rows = [[(f" {title} ", "badge")], [], [(prompt, "accent")]]
    lines = [ln for n in notes for ln in str(n).splitlines() if ln.strip()][-10:]
    rows += [[(clip(ln.strip(), w - 2), "dim")] for ln in lines]
    rows.append([])
    for i, (_, label) in enumerate(options):
        on = i == cursor
        if hits is not None:
            hits.append((len(rows), len(rows), 0, w, "opt", i))
        rows.append([("> " if on else "  ", "bar" if on else "normal"), (f"{i + 1}. " if i < 9 else "   ", "faint"),
                     (clip(label, w - 8), "sel_title" if on else "normal")])
    if extra:
        rows += [[]] + list(extra)
    help_row = help_line([(k, d) for k, d in (p.split(" ", 1) for p in hint.split(" • "))])
    body = rows[:max(0, h - 1)]
    rows = body + [[]] * max(0, min(2, h - len(body) - 1)) + [help_row]
    return [fit_spans(r, w) for r in rows[:h]]


# ---------------------------------------------------------------- curses

# Palettes. "glow" uses the colors in Charm's Glow source (ui/styles.go) and gum (cursor 212, header 99); the rest are
# the official Catppuccin Mocha, Tokyo Night and Dracula palettes, plus Rosé Pine, Gruvbox Dark and Nord.
# bg None keeps your terminal's own background; the others paint theirs.
THEMES = {
    "glow": dict(label="Glow", bg=None, text="#DDDDDD", meta="#979797", dim="#626262", faint="#3C3C3C",
                 accent="#EE6FF8", accent_dim="#99519E", bar="#AD58B4", border="#7D56F4", badge_fg="#ECFD65", badge_bg="#EE6FF8",
                 key="#979797", link="#89F0CB", ok="#04B575", warn="#ECFD65", bad="#ED567A", new_fg="#FFFDF5", new_bg="#04B575"),
    "catppuccin": dict(label="Catppuccin Mocha", bg="#1E1E2E", text="#CDD6F4", meta="#A6ADC8", dim="#7F849C", faint="#45475A",
                       accent="#CBA6F7", accent_dim="#B4BEFE", bar="#F5C2E7", border="#B4BEFE", badge_fg="#11111B", badge_bg="#CBA6F7",
                       key="#89B4FA", link="#89DCEB", ok="#A6E3A1", warn="#F9E2AF", bad="#F38BA8", new_fg="#11111B", new_bg="#A6E3A1"),
    "tokyonight": dict(label="Tokyo Night", bg="#1A1B26", text="#C0CAF5", meta="#A9B1D6", dim="#565F89", faint="#3B4261",
                       accent="#BB9AF7", accent_dim="#9D7CD8", bar="#BB9AF7", border="#7AA2F7", badge_fg="#16161E", badge_bg="#7AA2F7",
                       key="#7DCFFF", link="#7DCFFF", ok="#9ECE6A", warn="#E0AF68", bad="#F7768E", new_fg="#16161E", new_bg="#9ECE6A"),
    "dracula": dict(label="Dracula", bg="#282A36", text="#F8F8F2", meta="#F8F8F2", dim="#6272A4", faint="#44475A",
                    accent="#FF79C6", accent_dim="#BD93F9", bar="#FF79C6", border="#BD93F9", badge_fg="#282A36", badge_bg="#FF79C6",
                    key="#8BE9FD", link="#8BE9FD", ok="#50FA7B", warn="#F1FA8C", bad="#FF5555", new_fg="#282A36", new_bg="#50FA7B"),
    "rosepine": dict(label="Rosé Pine", bg="#191724", text="#E0DEF4", meta="#908CAA", dim="#6E6A86", faint="#403D52",
                     accent="#C4A7E7", accent_dim="#EBBCBA", bar="#EB6F92", border="#C4A7E7", badge_fg="#191724", badge_bg="#EBBCBA",
                     key="#9CCFD8", link="#9CCFD8", ok="#9CCFD8", warn="#F6C177", bad="#EB6F92", new_fg="#191724", new_bg="#9CCFD8"),
    "gruvbox": dict(label="Gruvbox Dark", bg="#282828", text="#EBDBB2", meta="#A89984", dim="#928374", faint="#504945",
                    accent="#FE8019", accent_dim="#D79921", bar="#FE8019", border="#83A598", badge_fg="#282828", badge_bg="#FABD2F",
                    key="#8EC07C", link="#83A598", ok="#B8BB26", warn="#FABD2F", bad="#FB4934", new_fg="#282828", new_bg="#B8BB26"),
    "nord": dict(label="Nord", bg="#2E3440", text="#D8DEE9", meta="#81A1C1", dim="#4C566A", faint="#3B4252",
                 accent="#88C0D0", accent_dim="#81A1C1", bar="#88C0D0", border="#5E81AC", badge_fg="#2E3440", badge_bg="#88C0D0",
                 key="#8FBCBB", link="#88C0D0", ok="#A3BE8C", warn="#EBCB8B", bad="#BF616A", new_fg="#2E3440", new_bg="#A3BE8C"),
}
DEFAULT_THEME = "glow"

# 8-colour fallback (fg, attrs) for terminals without 256 colours
ANSI8 = {"normal": (7, ""), "title": (7, ""), "sel_title": (5, "b"), "sel_meta": (5, ""), "meta": (7, ""), "dim": (7, ""),
         "faint": (7, ""), "sep": (7, ""), "bar": (5, "b"), "badge": (0, "b"), "accent": (5, ""), "query": (7, "b"),
         "key": (4, ""), "border": (4, ""), "link": (6, "u"), "ok": (2, "b"), "warn": (3, "b"), "bad": (1, "b"), "new": (0, "b")}


def styles_for(t):
    """style name -> (fg hex, bg hex or None, flags). b bold, u underline."""
    bg = t["bg"]
    return {
        "normal": (t["text"], bg, ""), "title": (t["text"], bg, ""), "sel_title": (t["accent"], bg, "b"),
        "sel_meta": (t["accent_dim"], bg, ""), "meta": (t["meta"], bg, ""), "dim": (t["dim"], bg, ""),
        "faint": (t["faint"], bg, ""), "sep": (t["faint"], bg, ""), "bar": (t["bar"], bg, "b"),
        "badge": (t["badge_fg"], t["badge_bg"], "b"), "accent": (t["accent"], bg, ""), "query": (t["text"], bg, "b"),
        "key": (t["key"], bg, ""), "border": (t["border"], bg, ""), "link": (t["link"], bg, "u"),
        "ok": (t["ok"], bg, "b"), "warn": (t["warn"], bg, "b"), "bad": (t["bad"], bg, "b"),
        "new": (t["new_fg"], t["new_bg"], "b"),
    }


def to256(hexcolor):
    """Nearest xterm-256 color to #RRGGBB (curses can't do truecolor)."""
    r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
    cube = (0, 95, 135, 175, 215, 255)
    ci = [min(range(6), key=lambda i: abs(cube[i] - v)) for v in (r, g, b)]
    cr, cg, cb = (cube[i] for i in ci)
    cube_d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    level = max(0, min(23, round((((r + g + b) / 3) - 8) / 10)))
    gv = 8 + 10 * level
    gray_d = (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2
    return 232 + level if gray_d < cube_d else 16 + 36 * ci[0] + 6 * ci[1] + ci[2]


def init_styles(theme_name):
    curses.start_color()
    try:
        curses.use_default_colors()
        default_bg = -1
    except curses.error:
        default_bg = curses.COLOR_BLACK
    rich = curses.COLORS >= 256
    attrs = {}
    for i, (name, (fg, bg, flags)) in enumerate(styles_for(THEMES[theme_name]).items(), 1):
        if rich:
            f, b = to256(fg), (to256(bg) if bg else default_bg)
        else:
            f, b = ANSI8[name][0], default_bg
            flags = ANSI8[name][1]
        try:
            curses.init_pair(i, f, b)
            a = curses.color_pair(i)
        except curses.error:
            a = 0
        for flag, bit in (("b", curses.A_BOLD), ("u", curses.A_UNDERLINE)):
            if flag in flags:
                a |= bit
        attrs[name] = a
    return attrs


def settings_file():
    import settings
    return settings.FILE


def load_theme():
    import settings
    name = os.environ.get("JOBRADAR_THEME", "")
    if name not in THEMES:
        name = settings.load(settings_file())["theme"]
    return name if name in THEMES else DEFAULT_THEME


def save_theme(name):
    import settings
    try:
        settings.save(settings_file(), {"theme": name})
    except (OSError, ValueError):
        pass  # a read-only folder shouldn't stop you using the screen


def theme_html(jobs, w=104, h=26):
    """Every theme rendering the same screen, as one HTML page you can open in a browser."""
    import html as _html
    cards = []
    for name, t in THEMES.items():
        st = State(jobs, "Chrome", name)
        st.cursor = min(1, max(0, len(st.shown) - 1))
        sty = styles_for(t)
        bg = t["bg"] or "#121212"
        lines = []
        for spans in frame(st, h, w):
            out = []
            for text, style in spans:
                fg, sbg, flags = sty.get(style, sty["normal"])
                css = f"color:{fg};" + (f"background:{sbg};" if sbg else "") + ("font-weight:700;" if "b" in flags else "") + ("text-decoration:underline;" if "u" in flags else "")
                out.append(f'<span style="{css}">{_html.escape(text)}</span>')
            lines.append("".join(out))
        sw = "".join(f'<i style="background:{t[k]}" title="{k} {t[k]}"></i>' for k in ("accent", "accent_dim", "bar", "border", "ok", "warn", "bad", "new_bg", "key"))
        cards.append(f'<section><h2>{_html.escape(t["label"])} <span class="sw">{sw}</span></h2>'
                     f'<pre style="background:{bg};color:{t["text"]}">' + "\n".join(lines) + "</pre></section>")
    return ("<!doctype html><meta charset=utf-8><title>Job Radar themes</title><style>"
            "body{margin:0;background:#0d0d10;color:#ddd;font:15px system-ui,sans-serif;padding:28px 20px 60px}"
            "h1{margin:0 0 4px;font-size:22px}p{margin:0 0 24px;color:#8a8a94}"
            "section{margin:0 auto 28px;max-width:960px}h2{font-size:15px;margin:0 0 8px;display:flex;gap:12px;align-items:center}"
            ".sw i{display:inline-block;width:16px;height:16px;border-radius:50%;margin-right:5px}"
            "pre{margin:0;padding:16px 18px;border-radius:12px;overflow:auto;line-height:1.28;font:13px/1.28 'Cascadia Mono',Consolas,Menlo,monospace;"
            "box-shadow:0 0 0 1px #ffffff14,0 10px 30px #0008}</style>"
            "<h1>Job Radar themes</h1><p>Press <b>t</b> in the browser to cycle them. In a terminal the colors snap to the nearest of 256, so they look a touch different.</p>"
            + "".join(cards))


def paint(stdscr, rows, attrs):
    stdscr.erase()  # the background is set once per theme (Screen.set_theme), never per frame
    for y, spans in enumerate(rows):
        x = 0
        for text, style in spans:
            if not text:
                continue
            try:
                stdscr.addstr(y, x, text, attrs.get(style, 0))
            except curses.error:
                pass  # writing the bottom-right cell raises even though it draws
            x += cells(text)
    stdscr.refresh()


def setup():
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")


def available():
    return curses is not None and os.isatty(0) and os.isatty(1)


def mouse_kind(bstate):
    """What a curses mouse state means to us: 'click' (left button), 'up' or 'down' (wheel), or None."""
    wheel_up, wheel_down = getattr(curses, "BUTTON4_PRESSED", 0), getattr(curses, "BUTTON5_PRESSED", 0)
    if wheel_up and bstate & wheel_up:
        return "up"
    if wheel_down and bstate & wheel_down:
        return "down"
    left = curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED
    return "click" if bstate & left else None


def read_mouse():
    """The pending mouse event as (kind, x, y), or None if it isn't one we use."""
    try:
        _, x, y, _, bstate = curses.getmouse()
    except curses.error:
        return None
    kind = mouse_kind(bstate)
    return (kind, x, y) if kind else None


def mouse_enabled():
    """JOBRADAR_MOUSE=0 or 1 wins; otherwise the Mouse setting (on by default)."""
    import settings
    env = os.environ.get("JOBRADAR_MOUSE", "").strip().lower()
    if env:
        return env not in ("0", "off", "no", "false")
    return settings.load(settings_file())["mouse"]


class Screen:
    """One curses session shared by consecutive menus. Opening and closing it for every question made the whole
    terminal flash between questions, so the session stays up until something needs the normal terminal."""

    def __init__(self, theme=None):
        self.stdscr, self.attrs, self.theme, self.mouse = None, {}, theme, False

    def open(self):
        if self.stdscr is not None:
            return self.stdscr
        setup()
        self.stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.mouse = False
        if mouse_enabled():
            try:
                self.mouse = bool(curses.mousemask(curses.ALL_MOUSE_EVENTS)[0])
                curses.mouseinterval(0)  # report a press at once instead of waiting to see if it is a double-click
            except curses.error:
                self.mouse = False
        self.set_theme(self.theme or load_theme())
        return self.stdscr

    def set_theme(self, name):
        self.theme = name
        self.attrs = init_styles(name)
        self.stdscr.bkgd(" ", self.attrs["normal"])

    def close(self):
        if self.stdscr is None:
            return
        stdscr, self.stdscr = self.stdscr, None
        try:
            if self.mouse:
                curses.mousemask(0)
            stdscr.keypad(False)
            curses.nocbreak()
            curses.echo()
        except curses.error:
            pass
        curses.endwin()


def run_browser(jobs, handlers, browser_label="your browser", screen=None):
    """Full-screen list. handlers: {'open': fn(job)->str, 'applied': fn(job)->str, 'exclude': fn(job)->str}.
    Each handler returns the message to flash; 'applied' and 'exclude' also remove roles from the list."""
    own = screen is None
    screen = screen or Screen()
    try:
        stdscr = screen.open()
        state = State(jobs, browser_label, screen.theme)
        while True:
            h, w = stdscr.getmaxyx()
            paint(stdscr, frame(state, h, w), screen.attrs)
            try:
                raw = stdscr.get_wch()
            except curses.error:
                continue
            except KeyboardInterrupt:
                return
            if raw == curses.KEY_RESIZE:
                continue
            if raw == curses.KEY_MOUSE:
                m = read_mouse()
                event = state.mouse(*m) if m else None
            else:
                event = state.press(raw)
            if state.theme_changed:
                state.theme_changed = False
                screen.set_theme(state.theme)
                save_theme(state.theme)
            if not event:
                continue
            kind, job = event
            if kind == "quit":
                return
            state.flash = handlers[kind](job) or ""
            if kind == "applied":
                state.remove_where(lambda j: j["_i"] == job["_i"])
            elif kind == "exclude":
                state.remove_where(lambda j: j.get("company") == job.get("company"))
    finally:
        if own:
            screen.close()


def run_select(title, prompt, options, default, screen=None, notes=(), preview=False):
    """Arrow-key chooser. Returns the chosen key, or None when cancelled.
    preview=True is for choosing a color theme: the keys are theme names, the screen switches to each one as you
    move over it, and Esc puts the original back."""
    own = screen is None
    screen = screen or Screen()
    keys = [k for k, _ in options]
    try:
        stdscr = screen.open()
        original = screen.theme
        cur = keys.index(default) if default in keys else 0
        if preview and keys[cur] in THEMES and screen.theme != keys[cur]:
            screen.set_theme(keys[cur])
        while True:
            h, w = stdscr.getmaxyx()
            extra = theme_sample(w) if preview else ()
            hits = []
            paint(stdscr, select_frame(title, prompt, options, cur, h, w, notes=notes, extra=extra, hits=hits), screen.attrs)
            try:
                raw = stdscr.get_wch()
            except curses.error:
                continue
            code, ch = key_of(raw)
            before = cur
            if code == curses.KEY_MOUSE:
                m = read_mouse()
                if m and m[0] in ("up", "down"):
                    cur = max(0, min(len(keys) - 1, cur + (-1 if m[0] == "up" else 1)))
                elif m and m[0] == "click":
                    for y0, y1, x0, x1, action, arg in hits:
                        if action == "opt" and y0 <= m[2] <= y1 and x0 <= m[1] < x1:
                            cur = arg
                            if preview:
                                screen.set_theme(keys[cur])
                            return keys[cur]
            elif code == curses.KEY_DOWN or ch == "j":
                cur = (cur + 1) % len(keys)
            elif code == curses.KEY_UP or ch == "k":
                cur = (cur - 1) % len(keys)
            elif ch.isdigit() and 1 <= int(ch) <= len(keys):
                cur = int(ch) - 1
                if preview:
                    screen.set_theme(keys[cur])
                return keys[cur]
            elif code in (10, 13, curses.KEY_ENTER):
                return keys[cur]
            elif code == 27 or ch == "q":
                if preview and original:
                    screen.set_theme(original)
                return None
            if preview and cur != before:
                screen.set_theme(keys[cur])
    finally:
        if own:
            screen.close()
