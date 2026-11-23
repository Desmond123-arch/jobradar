"""Time zones: where you are, how far from it you are happy to work, and whether a posting fits.

A posting is judged on the time-zone wording in its location and in the sentences of its description that talk
about hours ("Overlap with CET required", "UTC-5 to UTC+2", "PST hours", "within 3 hours of UTC").

  - Wording that names a zone (CET, PST, "Eastern time", "European hours") fits if that zone is within your
    range of hours from yours.
  - Wording that states who may apply ("UTC-5 to UTC+2", "within 3 hours of UTC") fits only if you are inside it.
  - "Any time zone" wording always fits. A posting that says nothing about time zones is left alone.

All offsets are standard time (no daylight saving). A range of 2 or 3 hours covers the one-hour shift.
This is wording matching, so it can be wrong: it is a filter to save you reading, not a promise.
"""

import re
import time
from dataclasses import dataclass

AUTO = "auto"
ANY_RANGE = 24  # a range this wide means "don't filter by time zone"
DEFAULT_RANGE = 3

# Standard-time offsets, with places that use them. The label is what the dropdown shows.
ZONES = [
    (-12, "Baker Island"), (-11, "Pago Pago"), (-10, "Honolulu"), (-9, "Anchorage"),
    (-8, "Los Angeles, Vancouver, Seattle (PST)"), (-7, "Denver, Phoenix, Calgary (MST)"),
    (-6, "Chicago, Mexico City, Dallas (CST)"), (-5, "New York, Toronto, Bogotá (EST)"),
    (-4, "Halifax, Caracas, Santiago"), (-3.5, "St. John's"), (-3, "São Paulo, Buenos Aires"),
    (-2, "South Georgia"), (-1, "Azores, Cape Verde"),
    (0, "Accra, Dakar, London, Lisbon (GMT)"), (1, "Lagos, Algiers, Paris, Berlin (WAT, CET)"),
    (2, "Johannesburg, Cairo, Athens, Kyiv (SAST, EET)"), (3, "Nairobi, Istanbul, Riyadh, Moscow (EAT)"),
    (3.5, "Tehran"), (4, "Dubai, Baku"), (4.5, "Kabul"), (5, "Karachi, Tashkent"), (5.5, "Mumbai, Delhi, Colombo"),
    (5.75, "Kathmandu"), (6, "Dhaka, Almaty"), (6.5, "Yangon"), (7, "Bangkok, Jakarta, Ho Chi Minh City"),
    (8, "Singapore, Beijing, Perth, Manila"), (9, "Tokyo, Seoul"), (9.5, "Adelaide, Darwin"),
    (10, "Sydney, Melbourne, Brisbane (AEST)"), (11, "Noumea, Solomon Islands"), (12, "Auckland, Fiji (NZST)"),
    (13, "Tonga"), (14, "Kiritimati"),
]

RANGES = [
    (0, "Same time zone only"), (1, "Within 1 hour"), (2, "Within 2 hours"), (3, "Within 3 hours"),
    (4, "Within 4 hours"), (5, "Within 5 hours"), (6, "Within 6 hours"), (8, "Within 8 hours"),
    (12, "Within 12 hours"), (ANY_RANGE, "Any: don't filter by time zone"),
]


# ---------------------------------------------------------------- your zone

def fmt(offset):
    """-8 -> 'UTC-8', 5.5 -> 'UTC+5.5', 0 -> 'UTC+0'."""
    n = f"{abs(offset):g}"
    return f"UTC{'-' if offset < 0 else '+'}{n}"


def key_for(offset):
    return fmt(offset)


_KEY = re.compile(r"^UTC([+-])(\d{1,2}(?:\.\d{1,2})?)$")


def valid(setting):
    """'auto', or 'UTC+1' / 'UTC-8' / 'UTC+5.5' within -12 to +14."""
    if setting == AUTO:
        return True
    m = _KEY.match(setting) if isinstance(setting, str) else None
    return bool(m) and -12 <= (1 if m.group(1) == "+" else -1) * float(m.group(2)) <= 14


def normalize(text):
    """What a person might type for a zone ('auto', 'UTC+1', 'utc-8', '+1', '5.5') as a stored setting, or None."""
    t = (text or "").strip()
    if t.lower() == AUTO:
        return AUTO
    m = re.match(r"(?i)^(?:utc|gmt)?\s*([+-]?)\s*(\d{1,2}(?:\.\d{1,2})?)$", t)
    if not m:
        return None
    key = f"UTC{m.group(1) or '+'}{float(m.group(2)):g}"
    return key if valid(key) else None


FALLBACK = 0  # UTC+0 (GMT: Accra, Dakar, London) when this computer's zone can't be read


def system_offset():
    """This computer's standard-time offset from UTC, in hours. UTC+0 if it can't be read or looks wrong."""
    try:
        offset = round(-time.timezone / 3600 * 4) / 4
    except (TypeError, ValueError, OverflowError):
        return FALLBACK
    return offset if -12 <= offset <= 14 else FALLBACK


def resolve(setting):
    """A setting ('auto' or 'UTC+1') as an offset in hours. Anything unreadable means this computer's zone."""
    if setting and setting != AUTO and valid(setting):
        m = _KEY.match(setting)
        return (1 if m.group(1) == "+" else -1) * float(m.group(2))
    return system_offset()


def describe(setting):
    """What to show for a stored setting."""
    if not setting or setting == AUTO:
        return f"this computer ({fmt(system_offset())})"
    offset = resolve(setting)
    places = next((p for o, p in ZONES if o == offset), "")
    return f"{fmt(offset)}  {places}".strip()


def range_label(hours):
    return "off" if hours >= ANY_RANGE else ("same zone" if hours == 0 else f"within {hours:g}h")


# ---------------------------------------------------------------- reading postings

ABBR = {
    "PST": -8, "PDT": -7, "MST": -7, "MDT": -6, "EST": -5, "EDT": -4, "AKST": -9, "HST": -10, "NST": -3.5,
    "GMT": 0, "UTC": 0, "WET": 0, "WEST": 1, "BST": 1, "CET": 1, "CEST": 2, "EET": 2, "EEST": 3, "MSK": 3,
    "WAT": 1, "CAT": 2, "EAT": 3, "SAST": 2, "PKT": 5, "SGT": 8, "HKT": 8, "AWST": 8, "JST": 9, "KST": 9,
    "ACST": 9.5, "AEST": 10, "AEDT": 11, "NZST": 12, "NZDT": 13,
}  # no CST or IST: each names two zones a world apart, so a guess would hide good jobs
_ABBR = "|".join(sorted(ABBR, key=len, reverse=True))
ABBR_RX = re.compile(rf"(?<![A-Za-z])({_ABBR})(?![A-Za-z])")  # capitals only, so "est" and "cat" the words never match
US_NAMED = {"pacific": -8, "mountain": -7, "central": -6, "eastern": -5}
US_NAMED_RX = re.compile(r"\b(pacific|mountain|central|eastern)\s+(?:standard\s+|daylight\s+)?time\b", re.I)
NAMED = [  # (pattern, offset)
    (re.compile(r"\bcentral european(?:\s+\w+)?\s+time\b", re.I), 1),
    (re.compile(r"\beastern european(?:\s+\w+)?\s+time\b", re.I), 2),
    (re.compile(r"\b(?:western european|british|greenwich(?: mean)?)(?:\s+\w+)?\s+time\b", re.I), 0),
    (re.compile(r"\bwest(?:ern)? africa(?:n)?\s+time\b", re.I), 1),
    (re.compile(r"\bcentral africa(?:n)?\s+time\b", re.I), 2),
    (re.compile(r"\beast(?:ern)? africa(?:n)?\s+time\b", re.I), 3),
    (re.compile(r"\bsouth africa(?:n)?\s+(?:standard\s+)?time\b", re.I), 2),
    (re.compile(r"\b(?:european|europe|eu|cet)\s+(?:business\s+|working\s+|office\s+)?hours\b", re.I), 1),
    (re.compile(r"\b(?:uk|british|gmt)\s+(?:business\s+|working\s+|office\s+)?hours\b", re.I), 0),
    (re.compile(r"\b(?:australian|aest)\s+(?:business\s+|working\s+)?hours\b", re.I), 10),
    (re.compile(r"\b(?:west|east)\s+coast\s+(?:hours|time)\b", re.I), None),  # handled below: needs the word
]
EAST_COAST, WEST_COAST = re.compile(r"\beast\s+coast\b", re.I), re.compile(r"\bwest\s+coast\b", re.I)
SPANS = [  # (pattern, low, high): "US hours" covers the whole country, so being near either edge counts
    (re.compile(r"\b(?:us|u\.s\.|north american|american)\s+(?:business\s+|working\s+|office\s+)?(?:hours|time ?zones?)\b", re.I), -8, -5),
]
ANY_ZONE = re.compile(
    r"\b(?:any|all|every|no)\s+time\s?zones?\b|\btime\s?zone[- ]?(?:agnostic|flexible|independent|friendly)\b|"
    r"\bregardless of (?:your )?(?:time\s?zone|location)\b|\bwork(?:ing)? from anywhere\b|\banywhere in the world\b", re.I)

_SIGN = r"[+\-−–±]"
_OFFSET = rf"(?:UTC|GMT)\s*({_SIGN})\s*(\d{{1,2}})(?::?(\d{{2}}))?"
OFFSET_RX = re.compile(_OFFSET, re.I)
RANGE_TAIL = re.compile(rf"\s*(?:to|through|until|and|[-–—/])\s*(?:(?:UTC|GMT)\s*)?([+\-−–])\s*(\d{{1,2}})(?::?(\d{{2}}))?", re.I)
WITHIN = re.compile(r"(?:\+/-|±|within|plus or minus)\s*(\d{1,2})\s*(?:hours?|hrs?|h)\s*(?:of|from|around|to)\s*", re.I)
CUE = re.compile(r"\b(?:hours|time\s?zones?|timezones?|overlap|working|business|availab\w*|must|required|only|based|core)\b", re.I)


def _num(sign, hours, minutes=None):
    value = int(hours) + (int(minutes) / 60 if minutes else 0)
    return -value if sign in "-−–" else value


@dataclass
class Mention:
    kind: str      # "point": one zone. "span": covers lo..hi, near an edge counts. "window": you must be inside lo..hi.
    lo: float
    hi: float
    text: str


def _label(m):
    return m.text if m.kind == "window" else f"{m.text} ({fmt(m.lo) if m.lo == m.hi else fmt(m.lo) + ' to ' + fmt(m.hi)})"


def mentions(text):
    """Every statement about time zones in this text."""
    found, taken = [], []

    def free(a, b):
        return not any(a < y and x < b for x, y in taken)

    def add(m, a, b):
        taken.append((a, b))
        found.append(m)

    for m in WITHIN.finditer(text):  # "within 3 hours of UTC+1": a window around a zone
        n = int(m.group(1))
        tail = text[m.end():m.end() + 24]
        centre = None
        o = OFFSET_RX.match(tail)
        if o and o.group(1) != "±":
            centre, used = _num(o.group(1), o.group(2), o.group(3)), o.end()
        elif o:
            centre, used = 0, o.end()
        else:
            a = ABBR_RX.match(tail)
            if a:
                centre, used = ABBR[a.group(1)], a.end()
        if centre is not None and free(m.start(), m.end() + used):
            add(Mention("window", centre - n, centre + n, f"within {n}h of {fmt(centre)}"), m.start(), m.end() + used)

    for m in OFFSET_RX.finditer(text):
        if not free(m.start(), m.end()):
            continue
        sign = m.group(1)
        if sign == "±":  # "UTC±3": anyone within 3 hours of UTC
            n = _num("+", m.group(2), m.group(3))
            add(Mention("window", -n, n, m.group(0).strip()), m.start(), m.end())
            continue
        first = _num(sign, m.group(2), m.group(3))
        tail = RANGE_TAIL.match(text, m.end())
        if tail:
            second = _num(tail.group(1), tail.group(2), tail.group(3))
            lo, hi = sorted((first, second))
            add(Mention("window", lo, hi, f"{fmt(lo)} to {fmt(hi)}"), m.start(), tail.end())
        else:
            add(Mention("point", first, first, m.group(0).strip()), m.start(), m.end())

    for pattern, lo, hi in SPANS:
        for m in pattern.finditer(text):
            if free(m.start(), m.end()):
                add(Mention("span", lo, hi, m.group(0)), m.start(), m.end())

    for pattern, offset in NAMED:
        for m in pattern.finditer(text):
            if not free(m.start(), m.end()):
                continue
            o = offset if offset is not None else (-5 if EAST_COAST.search(m.group(0)) else -8)
            add(Mention("point", o, o, m.group(0)), m.start(), m.end())

    for m in US_NAMED_RX.finditer(text):
        if free(m.start(), m.end()):
            o = US_NAMED[m.group(1).lower()]
            add(Mention("point", o, o, m.group(0)), m.start(), m.end())

    for m in ABBR_RX.finditer(text):
        if free(m.start(), m.end()):
            o = ABBR[m.group(1)]
            add(Mention("point", o, o, m.group(1)), m.start(), m.end())
    return found


def sentences_about_hours(description):
    """The sentences of a description that talk about when you work, which is where a time zone means a requirement."""
    parts = re.split(r"(?<=[.!?;])\s+|\n+", description or "")
    return " . ".join(p for p in parts if CUE.search(p) and len(p) < 400)


@dataclass
class Verdict:
    status: str  # "ok", "mismatch" or "" when the posting says nothing about time zones
    note: str = ""


class Zone:
    """Your time zone and how far from it you are happy to work."""

    def __init__(self, offset, hours=DEFAULT_RANGE):
        self.offset, self.hours = float(offset), float(hours)

    def fits(self, m):
        if m.kind == "window":
            return m.lo <= self.offset <= m.hi
        distance = 0 if m.lo <= self.offset <= m.hi else min(abs(self.offset - m.lo), abs(self.offset - m.hi))
        return distance <= self.hours

    def apart(self, m):
        if m.lo <= self.offset <= m.hi:
            return 0
        return min(abs(self.offset - m.lo), abs(self.offset - m.hi))

    def judge(self, found):
        if not found:
            return Verdict("")
        good = [m for m in found if self.fits(m)]
        if good:
            m = good[0]
            return Verdict("ok", f"time zone: {m.text}" if m.kind == "window" else
                           f"time zone: {_label(m)}, {self.apart(m):g}h from you" if self.apart(m) else f"time zone: {_label(m)}, same as you")
        m = min(found, key=self.apart)
        if m.kind == "window":
            return Verdict("mismatch", f"time zone: wants {m.text}, you are {fmt(self.offset)}")
        return Verdict("mismatch", f"time zone: {_label(m)} is {self.apart(m):g}h from you (you are {fmt(self.offset)})")

    def check(self, location, description=""):
        """Does this posting fit your time zone? Location wording decides first; the description only speaks
        through sentences about hours, so a passing mention of a city office never rules a job out."""
        if self.hours >= ANY_RANGE:
            return Verdict("")
        text = f"{location or ''} . {description or ''}"
        if ANY_ZONE.search(text):
            return Verdict("ok", "time zone: any")
        here = self.judge(mentions(location or ""))
        if here.status:
            return here
        return self.judge(mentions(sentences_about_hours(description)))


def make(setting=AUTO, hours=DEFAULT_RANGE):
    """A Zone, or None when the range is wide open (so callers can skip all the work)."""
    return None if hours >= ANY_RANGE else Zone(resolve(setting), hours)


if __name__ == "__main__":
    print("this computer:", describe(AUTO))
