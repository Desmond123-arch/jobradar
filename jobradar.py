"""jobradar: check companies' own job boards for jobs you can actually take, remote or on-site.

Reads the free public APIs behind Greenhouse, Lever and Ashby job boards, and several job
sites (no key needed), keeps the roles you ask for (the kinds of remote role you tick, and
on-site roles in the places you name or anywhere), skips companies you've excluded and roles
you've applied to, and marks what's new since the last run. Run `python cli.py` for the menu.

  python jobradar.py                 run and write report.html
  python jobradar.py --days 60       look back 60 days instead of 30
  python jobradar.py --all           also show region-locked and on-site roles

Company-wide search (no companies.txt needed):

  python jobradar.py --everything    every role in EMEA from every discoverable
                                     Greenhouse/Lever/Ashby board plus the job
                                     aggregators (same as --discover --aggregators
                                     --emea --any-role)
  python jobradar.py --discover      also scan every board found by slug discovery
  python jobradar.py --aggregators   also pull Remotive, Arbeitnow, Jobicy, Himalayas, RemoteOK, HN
  python jobradar.py --emea          keep roles located anywhere in Europe/Middle East/Africa
  python jobradar.py --any-role      don't limit to AI and engineering titles

Add companies by pasting their careers page URL into companies.txt.
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paths

import cvmatch
import openurl
import scopes
import settings
import sources
import timezones

HERE = paths.data_dir()  # your files; see paths.py
UA = "jobradar/1.0 (personal job search)"

# ---------------------------------------------------------------- progress

class Progress:
    """A one-line progress bar on stderr. In a terminal it redraws in place; piped, it prints a line every ~10%.
    Silent unless enabled, so library use and tests print nothing."""

    def __init__(self, enabled=True, stream=None, clock=time.monotonic):
        self.stream = stream or sys.stderr
        self.enabled = enabled
        self.tty = enabled and getattr(self.stream, "isatty", lambda: False)() and "NO_COLOR" not in os.environ
        try:  # a legacy Windows code page can't print block characters; use plain ones there
            "█░✓".encode(getattr(self.stream, "encoding", None) or "utf-8")
            self.FILL, self.EMPTY, self.CHECK = "█", "░", "✓"
        except (UnicodeEncodeError, LookupError):
            self.FILL, self.EMPTY, self.CHECK = "#", "-", "OK"
        if self.tty and os.name == "nt":
            os.system("")  # switches on ANSI colors in cmd.exe and Windows PowerShell
        self.clock = clock
        self.label, self.total, self.n, self.t0, self.active, self._last_pct = "", 0, 0, 0.0, False, -1

    @staticmethod
    def clock_text(seconds):
        seconds = int(max(0, seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def line(self, columns=80):
        frac = (self.n / self.total) if self.total else 0.0
        elapsed = self.clock() - self.t0
        eta = f"  ETA {self.clock_text(elapsed / frac - elapsed)}" if frac >= 0.03 and frac < 1 else ""
        left = f"  {self.label} "
        for right in (f" {self.n:,}/{self.total:,}  {frac:>4.0%}  {self.clock_text(elapsed)}{eta}",
                      f" {self.n:,}/{self.total:,}  {frac:>4.0%}", f" {frac:>4.0%}"):  # drop detail on narrow terminals
            width = columns - len(left) - len(right) - 1
            if width >= 8:
                break
        width = max(0, min(36, width))
        filled = int(width * min(frac, 1.0))
        return left, self.FILL * filled, self.EMPTY * (width - filled), right

    def _draw(self):
        if not self.tty:
            return
        left, fill, empty, right = self.line(shutil.get_terminal_size((80, 20)).columns)
        self.stream.write(f"\r\x1b[2m{left}\x1b[0m\x1b[38;5;207m{fill}\x1b[38;5;238m{empty}\x1b[0m\x1b[2m{right}\x1b[0m\x1b[K")
        self.stream.flush()

    def start(self, label, total):
        if not self.enabled:
            return
        self.label, self.total, self.n, self.t0, self.active, self._last_pct = label, total, 0, self.clock(), True, -1
        self._draw()

    def advance(self, n=1):
        if not (self.enabled and self.active):
            return
        self.n += n
        if self.tty:
            self._draw()
            return
        pct = int(10 * self.n / self.total) if self.total else 10
        if pct != self._last_pct:
            self._last_pct = pct
            self.stream.write(f"  {self.label}: {self.n:,}/{self.total:,}\n")
            self.stream.flush()

    def log(self, message):
        """Print a line above the bar without breaking it."""
        if not self.enabled:
            return
        if self.tty and self.active:
            self.stream.write("\r\x1b[K")
        self.stream.write(message + "\n")
        self.stream.flush()
        self._draw()

    def abort(self):
        """End the bar's line cleanly when a run is stopped half way."""
        if self.enabled and self.active and self.tty:
            self.stream.write("\r\x1b[K")
            self.stream.flush()
        self.active = False

    def finish(self, note=""):
        if not (self.enabled and self.active):
            return
        self.active = False
        if self.tty:
            self.stream.write(f"\r\x1b[K  \x1b[38;5;78m{self.CHECK}\x1b[0m {self.label} \x1b[2m{note}  {self.clock_text(self.clock() - self.t0)}\x1b[0m\n")
            self.stream.flush()


PROGRESS = Progress(enabled=False)

STOP = threading.Event()  # set when a run is cancelled, so requests already queued give up at once


def pooled(workers, fn, items):
    """pool.map that cancels the queued work and stops waiting the moment the caller is interrupted (Ctrl+C),
    instead of finishing thousands of requests first."""
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        yield from pool.map(fn, items)
    finally:
        STOP.set()
        pool.shutdown(wait=False, cancel_futures=True)


def use_progress(progress):
    global PROGRESS
    PROGRESS = progress


# ---------------------------------------------------------------- companies

URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
]


def parse_company_line(line: str):
    """A line is a careers URL, or 'provider:slug', optionally followed by '| Display Name'."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    name = None
    if "|" in line:
        line, name = [p.strip() for p in line.split("|", 1)]
    m = re.match(r"^(greenhouse|lever|ashby):([A-Za-z0-9_.%-]+)$", line)
    if m:
        return {"provider": m.group(1), "slug": m.group(2), "name": name or m.group(2)}
    for provider, rx in URL_PATTERNS:
        m = rx.search(line)
        if m:
            return {"provider": provider, "slug": m.group(1), "name": name or m.group(1)}
    raise ValueError(f"can't tell which job board this is: {line!r} (use a Greenhouse, Lever or Ashby URL)")


def read_lines(path: Path):
    if not path.exists():
        return []
    return [l.rstrip("\n") for l in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- fetching

def fetch_json(url: str, tries: int = 3):
    last = None
    for attempt in range(tries):
        if STOP.is_set():
            raise ConnectionError("stopped")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise LookupError("board not found (check the slug)") from exc
            last = exc
        except Exception as exc:  # flaky connections: retry
            last = exc
        time.sleep(2 * (attempt + 1))
    raise ConnectionError(f"could not reach {url}: {last}")


@dataclass
class Job:
    company: str
    title: str
    url: str
    location: str
    remote: bool
    posted: str  # ISO date, best available
    description: str
    provider: str
    job_id: str
    pay: str = ""
    region: str = ""          # worldwide | africa-emea | restricted | onsite | unclear
    region_reason: str = ""
    score: int = 0
    why: list = field(default_factory=list)
    stretch: bool = False
    new: bool = False
    detail_url: str = ""      # set when the description still has to be fetched
    cv_match: int = -1        # % of the skills the posting names that are on your CV; -1 = not scored
    cv_missing: list = field(default_factory=list)
    tz_note: str = ""         # what the posting says about time zones, when it says something that fits
    scopes: list = field(default_factory=list)  # the kinds of remote role it is (worldwide, africa, ...), see scopes.py

    @property
    def key(self):
        return f"{self.provider}:{self.company}:{self.job_id}"


def strip_html(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def iso_from_ms(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def from_greenhouse(company, data, slug=""):
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "") or ""
        out.append(Job(
            company=company, title=j.get("title", ""), url=j.get("absolute_url", ""),
            location=loc, remote="remote" in loc.lower(),
            posted=j.get("first_published") or j.get("updated_at") or "",
            description=strip_html(j.get("content", "")), provider="greenhouse", job_id=str(j.get("id", "")),
            detail_url="" if "content" in j or not slug else f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j.get('id', '')}",
        ))
    return out


def from_lever(company, data, slug=""):
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        loc = ", ".join([l for l in locs if l])
        wt = (j.get("workplaceType") or "").lower()
        pay = ""
        sr = j.get("salaryRange") or {}
        if sr.get("min"):
            per = {"per-year-salary": "a year", "per-month-salary": "a month", "per-hour-wage": "an hour"}.get(sr.get("interval", ""), "")
            pay = f"{sr.get('currency', '')} {sr.get('min'):,} to {sr.get('max', 0):,} {per}".strip()
        out.append(Job(
            company=company, title=j.get("text", ""), url=j.get("hostedUrl", ""),
            location=loc, remote=wt == "remote" or "remote" in loc.lower(),
            posted=iso_from_ms(j.get("createdAt")),
            description=(j.get("descriptionPlain") or "") + " " + (j.get("additionalPlain") or ""),
            provider="lever", job_id=str(j.get("id", "")), pay=pay,
        ))
    return out


def from_ashby(company, data, slug=""):
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = ", ".join([l for l in locs if l])
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        wt = (j.get("workplaceType") or "").lower()
        out.append(Job(
            company=company, title=j.get("title", ""), url=j.get("jobUrl", ""),
            location=loc, remote=bool(j.get("isRemote")) or wt == "remote" or "remote" in loc.lower(),
            posted=j.get("publishedAt", ""), description=j.get("descriptionPlain", "") or strip_html(j.get("descriptionHtml", "")),
            provider="ashby", job_id=str(j.get("id", "")), pay=comp,
        ))
    return out


ENDPOINTS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", from_greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{slug}?mode=json", from_lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", from_ashby),
}


def fetch_company(c, fetch=fetch_json, light=False):
    """light: skip Greenhouse's bulky job bodies; run() fetches them only for roles that survive filtering."""
    url_tpl, parse = ENDPOINTS[c["provider"]]
    url = url_tpl.format(slug=c["slug"])
    if light and c["provider"] == "greenhouse":
        url = url.replace("?content=true", "")
    return parse(c["name"], fetch(url), slug=c["slug"])


# ---------------------------------------------------------------- judging

# Clear statements that anyone can apply, and softer ones ("fully remote") that only mean no office. A soft one
# doesn't count if the location also names a restricted place: "Fully Remote (US-only)" is a US job.
WORLDWIDE_STRONG = re.compile(r"\b(anywhere|worldwide|world ?wide|all countries|any (country|location))\b", re.I)
WORLDWIDE_SOFT = re.compile(r"\b(global(ly)?|fully remote|remote[- ]first)\b", re.I)
WORLDWIDE = re.compile(f"(?:{WORLDWIDE_STRONG.pattern})|(?:{WORLDWIDE_SOFT.pattern})", re.I)
AFRICA_EMEA = re.compile(r"\b(africa|nigeria|lagos|emea|sub-saharan|gmt\+?1|wat|cet|europe, middle east)\b", re.I)
# Anywhere in Europe, the Middle East or Africa. Only used with --emea.
EMEA_PLACES = re.compile(
    r"\b(emea|europe|european|eu|eea|uk|u\.k\.|gb|great britain|united kingdom|england|scotland|wales|ireland|"
    r"germany|france|spain|portugal|italy|netherlands|belgium|luxembourg|switzerland|austria|poland|czech(ia| republic)?|"
    r"slovakia|hungary|romania|bulgaria|greece|croatia|serbia|slovenia|estonia|latvia|lithuania|finland|sweden|norway|"
    r"denmark|iceland|ukraine|cyprus|malta|"
    r"london|dublin|berlin|munich|hamburg|frankfurt|paris|amsterdam|rotterdam|brussels|madrid|barcelona|lisbon|porto|"
    r"rome|milan|zurich|geneva|vienna|warsaw|krakow|prague|budapest|bucharest|sofia|athens|stockholm|copenhagen|"
    r"oslo|helsinki|tallinn|riga|vilnius|edinburgh|manchester|belfast|cardiff|"
    r"middle east|mena|uae|united arab emirates|dubai|abu dhabi|saudi arabia|riyadh|qatar|doha|bahrain|kuwait|oman|"
    r"jordan|lebanon|israel|tel aviv|turkey|t[uü]rkiye|istanbul|egypt|cairo|morocco|tunisia|algeria|"
    r"south africa|cape town|johannesburg|kenya|nairobi|ghana|accra|rwanda|kigali|uganda|tanzania|ethiopia|senegal|"
    r"ivory coast|c[oô]te d'ivoire|zimbabwe|zambia|botswana|mauritius)\b", re.I)
# Places that, when they are the only places named, mean Lagos can't apply.
RESTRICTED = re.compile(
    r"\b(us|usa|u\.s\.|united states|canada|north america|americas|latam|latin america|uk|united kingdom|"
    r"europe|eu|eea|germany|france|spain|portugal|netherlands|poland|ireland|india|apac|asia|australia|"
    r"new zealand|brazil|mexico|argentina|colombia|philippines|singapore|japan|israel|[a-z]+, [a-z]{2})\b", re.I)
DESC_LOCK = re.compile(
    r"(must (be|reside|live) (based )?in (the )?(us|u\.s\.|united states|canada|uk|europe|eu)|"
    r"authori[sz]ed to work in the (us|u\.s\.|united states|uk)|"
    r"(us|u\.s\.)[- ]based (only|candidates)|only (accepting|considering) (applicants|candidates) (from|in|based in)|"
    r"within the (us|u\.s\.|united states|eu|uk)|(us|est|pst|cet) time ?zone (only|required)|"
    r"we (can|are able to) (only )?hire in)", re.I)


def classify_region(job: Job, emea: bool = False):
    loc = job.location or ""
    if AFRICA_EMEA.search(loc):  # includes on-site in Lagos, which is fine for you
        return "africa-emea", f"location names Africa/EMEA: {loc}"
    if emea and EMEA_PLACES.search(loc):  # on-site or remote, anywhere in the region
        return "emea", f"location in EMEA: {loc}"
    if not job.remote and not WORLDWIDE.search(loc):
        return "onsite", f"not marked remote ({loc or 'no location'})"
    if WORLDWIDE.search(loc):
        if not WORLDWIDE_STRONG.search(loc):
            named = re.sub(r"\bremote\b", "", WORLDWIDE_SOFT.sub("", loc), flags=re.I).strip(" ,-–()/|%0123456789")
            if RESTRICTED.search(named):
                return "restricted", f"says remote, but only in: {loc}"
        if DESC_LOCK.search(job.description):
            m = DESC_LOCK.search(job.description)
            return "restricted", f"says worldwide, but the description says: '{m.group(0)}'"
        return "worldwide", f"location: {loc}"
    stripped = re.sub(r"\bremote\b", "", loc, flags=re.I).strip(" ,-–()/|")
    if RESTRICTED.search(stripped):
        return "restricted", f"remote, but only in: {loc}"
    if not stripped:
        if DESC_LOCK.search(job.description):
            m = DESC_LOCK.search(job.description)
            return "restricted", f"remote, but the description says: '{m.group(0)}'"
        return "unclear", "says Remote with no region; check the description"
    return "unclear", f"remote, region unclear: {loc}"


TITLE_STRONG = re.compile(r"\b(ai|a\.i\.|ml|machine learning|llm|genai|generative|agent(s|ic)?|applied (ai|scientist)|nlp|voice|speech|conversational|search|relevance|retrieval|rag|evals?|evaluation|forward[- ]deployed)\b", re.I)
TITLE_OK = re.compile(r"\b(backend|back-end|python|platform|software engineer|full[- ]stack|solutions engineer|data engineer)\b", re.I)
TITLE_SKIP = re.compile(r"\b(sales|account (executive|manager)|recruit|marketing|designer|legal|counsel|finance|accountant|hr|people partner|customer success|support specialist|intern(ship)?|sdr|bdr)\b", re.I)
SENIOR_STRETCH = re.compile(r"\b(staff|principal|director|head of|vp|vice president|chief|distinguished|manager)\b", re.I)
# --no-senior: drop anything above plain "Software Engineer" level.
SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|head of|director|vp|vice president|chief|distinguished|manager|architect|iii|iv|v)\b", re.I)
SKILLS = {
    "langgraph": 3, "pydantic": 2, "fastapi": 3, "livekit": 4, "deepgram": 3, "rag": 3, "retrieval": 2,
    "vector": 2, "embedding": 2, "llm": 2, "agent": 2, "evals": 3, "evaluation": 1, "neo4j": 3,
    "knowledge graph": 3, "voice": 2, "speech": 2, "python": 1, "postgres": 1, "openai": 1, "anthropic": 1,
    "langchain": 2, "search relevance": 4, "hybrid search": 4, "ranking": 2, "guardrail": 2,
}


def score_job(job: Job):
    why = []
    s = 0
    if TITLE_STRONG.search(job.title):
        s += 10
        why.append("AI role")
    elif TITLE_OK.search(job.title):
        s += 4
        why.append("engineering role")
    desc = job.description.lower()
    hits = [k for k in SKILLS if k in desc]
    s += sum(SKILLS[k] for k in hits)
    if hits:
        why.append("mentions " + ", ".join(hits[:6]))
    if job.region in ("africa-emea", "emea"):
        s += 4
    job.stretch = bool(SENIOR_STRETCH.search(job.title))
    if job.stretch:
        s -= 4
        why.append("senior title, stretch")
    if job.tz_note:
        why.append(job.tz_note)
    job.score, job.why = s, why
    return s


def is_engineering_ai(job: Job):
    if TITLE_SKIP.search(job.title):
        return False
    return bool(TITLE_STRONG.search(job.title) or TITLE_OK.search(job.title))


# Titles to keep for --roles. Seniority is ignored.
GENERIC_TITLE = r"software|back[- ]?end|front[- ]?end|full[- ]?stack|web|mobile|application|platform|api"
# A typical full-stack/backend developer's title words, used for --roles cv when there is no CV to read.
DEFAULT_TITLE_TOKENS = [
    "cloud", "devops", "automation", "python", "java(?!\\s*script)", "golang", "javascript", "typescript", "node(\\.?js)?",
    "react( native)?", "angular", "ionic", "spring( boot)?", "flask", "express(\\.?js)?", "browser extension",
    "chrome extension", "sdet", "qa automation", "test automation"]
# CV skill (names from cvmatch.SKILLS) -> the words it makes a job title worth keeping
SKILL_TITLE_TOKENS = {
    "Python": "python", "Java": "java(?!\\s*script)", "Go": "golang", "JavaScript": "javascript", "TypeScript": "typescript",
    "Kotlin": "kotlin|android", "Swift": "swift|ios", "Rust": "rust", "C#": "net|dotnet", "PHP": "php", "Ruby": "ruby|rails",
    "Scala": "scala", "Elixir": "elixir", "React": "react", "React Native": "react native", "Angular": "angular", "Vue": "vue",
    "Next.js": "next\\.?js", "Node.js": "node(\\.?js)?", "Ionic": "ionic", "Flutter": "flutter", "Spring Boot": "spring( boot)?",
    "Django": "django", "Flask": "flask", "FastAPI": "fastapi", "Rails": "rails|ruby", "Laravel": "laravel",
    "Playwright": "sdet|qa automation|test automation", "Selenium": "sdet|qa automation|test automation",
    "Cypress": "sdet|qa automation|test automation", "Test automation": "sdet|qa automation|test automation|automation",
    "Browser extensions": "browser extension|chrome extension",
    "AWS": "cloud|devops|sre", "GCP": "cloud|devops|sre", "Azure": "cloud|devops|sre", "Docker": "devops|sre",
    "Kubernetes": "devops|sre|cloud", "Terraform": "devops|sre|cloud|infrastructure",
    "LLMs": "ai|ml|llm", "RAG": "ai|ml|llm", "Agents": "ai|ml|llm", "PyTorch": "ai|ml|machine learning",
    "TensorFlow": "ai|ml|machine learning", "Machine learning": "ai|ml|machine learning", "NLP": "ai|ml|nlp", "Voice AI": "ai|voice|speech",
    "Spark": "data", "Airflow": "data", "dbt": "data", "Snowflake": "data", "BigQuery": "data", "Kafka": "data|streaming",
}


def cv_roles_regex(tokens):
    """Titles like 'Backend Engineer', 'Python Developer', 'Software Engineer (Go)' for these skill words."""
    words = "|".join(tokens)
    return re.compile(
        rf"\b({GENERIC_TITLE}|{words})\b.*\b(engineer|developer|programmer|architect)\b|"
        rf"\b(engineer|developer),? \(?(go|golang|{words}|backend|frontend|full[- ]?stack)\)?\s*$|"
        r"\b(swe|sde|sdet)\b|\bgo (engineer|developer)\b|\bmember of technical staff\b", re.I)


def roles_regex_for_cv(skills):
    """--roles cv tailored to the skills found on the user's CV; the typical-stack regex when none map to titles."""
    tokens = sorted({SKILL_TITLE_TOKENS[s] for s in skills if s in SKILL_TITLE_TOKENS})
    return cv_roles_regex(tokens) if tokens else ROLE_PRESETS["cv"]


ROLE_PRESETS = {
    "web": re.compile(r"\b(back[- ]?end|front[- ]?end|full[- ]?stack|software (engineer|developer)|web (developer|engineer))\b", re.I),
    "cv": cv_roles_regex(DEFAULT_TITLE_TOKENS),
}
# Same as TITLE_SKIP but keeps interns: seniority and experience don't matter for --roles.
ROLE_SKIP = re.compile(r"\b(sales|account (executive|manager)|recruit|marketing|designer|legal|counsel|finance|accountant|hr|people partner|customer success|support specialist|sdr|bdr)\b", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def posted_dt(job):
    try:
        d = datetime.fromisoformat(job.posted.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def squash(s):
    return norm(s).replace(" ", "")


def blocked(region, show_all, emea):
    """Regions dropped unless --all. In the older --emea mode a bare "Remote" is dropped too."""
    return not show_all and (region in ("restricted", "onsite", "timezone") or (emea and region == "unclear"))


def place_ok(location, places):
    """Is this location one of the places you would work on-site? No places listed means anywhere."""
    if not places:
        return True
    return any(re.search(r"(?<![A-Za-z])" + re.escape(p) + r"(?![A-Za-z])", location or "", re.I) for p in places)


def assess(job, emea=False, tz=None, onsite=False, places=(), remote=None):
    """Where can this job be done from, and does its time zone suit you? Sets job.tz_note and job.scopes and returns
    (region, reason). An on-site role is kept (as 'nearby') when you allowed on-site roles and it is somewhere you listed.

    remote is the list of kinds of remote role you ticked (see scopes.py). A role is kept if it belongs to any of them.
    Without it, the older rules apply (classify_region and emea)."""
    job.tz_note, job.scopes = "", []
    if remote is None:
        return _assess_legacy(job, emea, tz, onsite, places)
    if not job.remote:  # on-site or hybrid: only the On-site setting can bring it in, whatever its place
        if onsite and place_ok(job.location, places):
            where = job.location or "no location given"
            return "nearby", (f"on-site, in a place you listed: {where}" if places else f"on-site: {where}")
        return "onsite", f"on-site, not remote ({job.location or 'no location given'})"
    found, reason = scopes.of(job.location, job.description)
    if tz:
        verdict = tz.check(job.location, job.description)
        if verdict.status == "mismatch":
            job.scopes = sorted(found)
            return "timezone", verdict.note
        if verdict.status == "ok":
            found.discard("unclear")  # it does say something about where: its hours
            found.add("my-timezone")
            job.tz_note = verdict.note
    elif "my-timezone" in remote and timezones.mentions(job.location or ""):
        found.discard("unclear")  # no range set, so any time zone named counts
        found.add("my-timezone")
    job.scopes = sorted(found)
    chosen = scopes.label_for(found, remote)
    if chosen:
        return chosen, reason if chosen != "my-timezone" else (job.tz_note or reason)
    if found:
        names = ", ".join(scopes.LABELS[k] for k in scopes.KEYS if k in found)
        return "restricted", f"for {names} only, which you have not ticked ({job.location})"
    return "restricted", reason


def _assess_legacy(job, emea, tz, onsite, places):
    region, reason = classify_region(job, emea)
    if region == "onsite" and onsite and place_ok(job.location, places):
        where = job.location or "no location given"
        return "nearby", (f"on-site, in a place you listed: {where}" if places else f"on-site: {where}")
    if tz and region in ("africa-emea", "emea", "worldwide", "unclear"):
        verdict = tz.check(job.location, job.description)
        if verdict.status == "mismatch":
            return "timezone", verdict.note
        if verdict.status == "ok":
            job.tz_note = verdict.note
    return region, reason


def hydrate(job, fetch):
    """Fill in a description that the board's list endpoint left out."""
    try:
        job.description = strip_html((fetch(job.detail_url) or {}).get("content", ""))
    except Exception:
        pass  # keep the location-only verdict; the posting link still works


def run(companies, excluded, applied, days, show_all, seen, fetch=fetch_json, now=None,
        any_role=False, role_rx=None, no_senior=False, emea=False, workers=1, light=False, extra_jobs=(), dead_out=None, cv=None,
        tz=None, onsite=False, places=(), remote=None, listed_out=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    hn_cutoff = now - timedelta(days=max(days, HN_MIN_DAYS))
    excluded_n = {squash(x) for x in excluded}
    applied_n = set()
    for line in applied:
        if "|" in line:
            c, t = [p.strip() for p in line.split("|", 1)]
            applied_n.add((norm(c), norm(t)))
    kept, problems = [], []
    counts = {"fetched": 0, "not_ai": 0, "old": 0, "excluded": 0, "applied": 0, "region": 0,
              "dupe": 0, "dead": 0, "boards": 0, "senior": 0}

    todo = []
    for c in companies:
        if squash(c["name"]) in excluded_n:
            counts["excluded"] += 1
        else:
            todo.append(c)

    def load(c):
        try:
            return c, fetch_company(c, fetch, light), None
        except Exception as exc:
            return c, [], exc

    all_jobs, board_keys = [], set()
    PROGRESS.start("Reading job boards", len(todo))
    for c, jobs, exc in pooled(workers, load, todo):
        PROGRESS.advance()
        if exc is None:
            counts["boards"] += 1
            all_jobs.extend(jobs)
        elif c.get("discovered"):
            counts["dead"] += 1  # guessed slugs are often not boards; don't list them as problems
            if isinstance(exc, LookupError) and dead_out is not None:
                dead_out.add(f"{c['provider']}:{c['slug']}")
        else:
            problems.append(f"{c['name']} ({c['provider']}:{c['slug']}): {exc}")
    PROGRESS.finish(f"{counts['boards']:,} boards read, {counts['dead']:,} had none")
    board_keys = {(norm(j.company), norm(j.title)) for j in all_jobs}
    for j in extra_jobs:
        k = (norm(j.company), norm(j.title))
        if k in board_keys:
            counts["dupe"] += 1
            continue
        board_keys.add(k)
        all_jobs.append(j)

    if listed_out is not None:  # everything the boards list right now, before any filter: what "still open" means
        listed_out.update(j.key for j in all_jobs)
    pending = []
    for job in all_jobs:
        counts["fetched"] += 1
        if role_rx is not None:
            wanted = bool(role_rx.search(job.title)) and not ROLE_SKIP.search(job.title)
        else:
            wanted = any_role or is_engineering_ai(job)
        if not wanted:
            counts["not_ai"] += 1
            continue
        if no_senior and SENIOR_TITLE.search(job.title):
            counts["senior"] += 1
            continue
        p = posted_dt(job)
        if p and p < (hn_cutoff if job.provider == "hackernews" else cutoff):
            counts["old"] += 1
            continue
        if (norm(job.company), norm(job.title)) in applied_n:
            counts["applied"] += 1
            continue
        job.region, job.region_reason = assess(job, emea, tz, onsite, places, remote)
        if blocked(job.region, show_all, emea):
            counts["region"] += 1
            continue
        pending.append(job)

    to_hydrate = [j for j in pending if j.detail_url]
    if to_hydrate:
        PROGRESS.start("Reading postings", len(to_hydrate))
        for _ in pooled(workers, lambda j: hydrate(j, fetch), to_hydrate):
            PROGRESS.advance()
        PROGRESS.finish(f"{len(to_hydrate):,} postings")
    for job in pending:
        if job.detail_url:  # the description may reveal a lock the location didn't
            job.region, job.region_reason = assess(job, emea, tz, onsite, places, remote)
            if blocked(job.region, show_all, emea):
                counts["region"] += 1
                continue
        score_job(job)
        if cv:
            cvmatch.apply(job, cv)
        job.new = job.key not in seen
        kept.append(job)
    order = REGION_ORDER
    kept.sort(key=lambda j: (not j.new, order[j.region], -j.score))
    return kept, problems, counts


# ---------------------------------------------------------------- aggregators
# Job aggregators that search across every company. Free and keyless. Each returns Jobs.

def _remote_job(company, title, url, location, posted, description, provider, job_id, pay="", remote=True):
    return Job(company=company or "?", title=title or "", url=url or "", location=location or "", remote=remote,
               posted=posted or "", description=strip_html(description), provider=provider, job_id=str(job_id), pay=pay)


def iso_from_s(s):
    return iso_from_ms(int(s) * 1000) if s else ""


def agg_remotive(fetch, pages):
    d = fetch("https://remotive.com/api/remote-jobs")
    return [_remote_job(j.get("company_name"), j.get("title"), j.get("url"), j.get("candidate_required_location"),
                        j.get("publication_date"), j.get("description"), "remotive", j.get("id"), j.get("salary") or "")
            for j in d.get("jobs", [])]


def agg_arbeitnow(fetch, pages):
    out = []
    for page in range(1, pages + 1):
        d = fetch(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        for j in d.get("data", []):
            loc = j.get("location") or ""
            out.append(_remote_job(j.get("company_name"), j.get("title"), j.get("url"), loc,
                                   iso_from_s(j.get("created_at")), j.get("description"), "arbeitnow", j.get("slug"),
                                   remote=bool(j.get("remote")) or "remote" in loc.lower()))
        if not (d.get("links") or {}).get("next"):
            break
    return out


def agg_jobicy(fetch, pages):
    out = []
    for geo in ("emea",):  # "africa" is rejected (HTTP 400); "emea" already covers it
        cursor = ""
        for _ in range(pages):
            d = fetch(f"https://jobicy.com/api/v2/remote-jobs?count=100&geo={geo}" + (f"&cursor={cursor}" if cursor else ""))
            for j in d.get("jobs", []):
                geo_txt = j.get("jobGeo") or ""
                pay = f"{j.get('salaryCurrency', '')} {j.get('salaryMin')} to {j.get('salaryMax')} {j.get('salaryPeriod', '')}".strip() if j.get("salaryMin") else ""
                out.append(_remote_job(j.get("companyName"), j.get("jobTitle"), j.get("url"), geo_txt or "EMEA",
                                       j.get("pubDate"), j.get("jobDescription"), "jobicy", j.get("id"), pay))
            cursor = d.get("nextCursor") or ""
            if not d.get("hasMore") or not cursor:
                break
    return out


def agg_himalayas(fetch, pages):
    out = []
    for page in range(pages):
        d = fetch(f"https://himalayas.app/jobs/api?limit=20&offset={page * 20}")
        for j in d.get("jobs", []):
            locs = j.get("locationRestrictions") or []
            lo, hi = j.get("minSalary"), j.get("maxSalary")
            out.append(_remote_job(j.get("companyName"), j.get("title"), j.get("applicationLink") or j.get("guid"),
                                   ", ".join(locs) if locs else "Worldwide", iso_from_s(j.get("pubDate")),
                                   j.get("description"), "himalayas", j.get("guid"),
                                   f"{j.get('currency', '')} {lo:,} to {hi:,}".strip() if lo and hi else ""))
        if not d.get("jobs"):
            break
    return out


def agg_remoteok(fetch, pages):
    d = fetch("https://remoteok.com/api")
    return [_remote_job(j.get("company"), j.get("position"), j.get("url"), j.get("location") or "Worldwide",
                        j.get("date"), j.get("description"), "remoteok", j.get("id"))
            for j in d if isinstance(j, dict) and j.get("position")]


HN_ROLE = re.compile(r"\b(engineer|developer|programmer|scientist|architect|sre|devops|swe|founding|cto|researcher|designer|manager|lead)", re.I)
# "Who is hiring?" is posted once a month and most replies land in its first days, so a short --days window
# would hide nearly all of it by the third week. Its posts stay open for the month, so they get a longer one.
HN_MIN_DAYS = 35
HN_THREAD = re.compile(r"^ask hn: who is hiring\b", re.I)  # not "who wants to be hired" / "freelancer"
HN_PLACE = re.compile(r"\b(remote|onsite|on-site|hybrid|in[- ]office)\b|, [A-Z]{2}\b|\b(usa?|uk|eu|emea|europe|worldwide|global)\b", re.I)
HN_NOT_LOCATION = re.compile(r"https?://|[$€£]|\b(full[- ]?time|part[- ]?time|contract|salary|equity|visa|series [a-e]|yc [a-z]\d+)\b", re.I)


def parse_hn_post(item):
    """One top-level 'Who is hiring?' comment -> Job, or None when it doesn't follow the
    'Company | Role | Location | ...' header convention the thread asks for."""
    text = item.get("text") or ""
    first = re.split(r"<p>|\n", text, maxsplit=1)[0]
    parts = [p.strip() for p in strip_html(first).split("|") if p.strip()]
    if len(parts) < 2 or len(parts[0]) > 80:
        return None
    company, rest = parts[0], parts[1:]
    roles = [p for p in rest if HN_ROLE.search(p) and len(p) < 140 and "://" not in p]
    if not roles:  # no role word: take the first part that isn't a place or work mode
        roles = [p for p in rest if not HN_PLACE.search(p) and not HN_NOT_LOCATION.search(p)][:1]
    title = "; ".join(roles) or "See post"
    location = " | ".join(p for p in rest if p not in roles and not HN_NOT_LOCATION.search(p) and len(p) < 100)
    return _remote_job(company, title, f"https://news.ycombinator.com/item?id={item.get('id')}", location,
                       item.get("created_at"), text, "hackernews", item.get("id"),
                       remote=bool(re.search(r"\bremote\b", first, re.I)))


def agg_hackernews(fetch, pages):
    """Hacker News 'Ask HN: Who is hiring?' via the Algolia HN API (keyless). Reads the latest
    monthly thread, plus earlier ones when pages > 5; run() still drops posts older than --days."""
    # Found fresh each run: a new thread is posted every month, so nothing here names a month, year or id.
    # The whoishiring bot posts it; the plain search is the fallback if that account ever changes.
    threads = []
    for url in ("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=12",
                "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=%22who%20is%20hiring%22&hitsPerPage=30"):
        hits = fetch(url).get("hits", [])
        threads = [h["objectID"] for h in hits if HN_THREAD.search(h.get("title") or "") and (h.get("num_comments") or 0) >= 20]
        if threads:
            break
    out = []
    for tid in threads[: 1 + (pages > 5)]:
        thread = fetch(f"https://hn.algolia.com/api/v1/items/{tid}")
        out += [j for c in thread.get("children", []) if c.get("text") and (j := parse_hn_post(c))]
    return out


AGGREGATORS = {"remotive": agg_remotive, "arbeitnow": agg_arbeitnow, "jobicy": agg_jobicy,
               "himalayas": agg_himalayas, "remoteok": agg_remoteok,
               "hackernews": agg_hackernews}


def fetch_aggregators(pages=5, fetch=fetch_json):
    """Returns (jobs, problems); one source being down never stops the others."""
    jobs, problems = [], []
    PROGRESS.start("Job aggregators", len(AGGREGATORS))
    for name, fn in AGGREGATORS.items():
        try:
            got = fn(fetch, pages)
            PROGRESS.log(f"  {name}: {len(got)} roles")
            jobs.extend(got)
        except Exception as exc:
            problems.append(f"aggregator {name}: {exc}")
        PROGRESS.advance()
    PROGRESS.finish(f"{len(jobs):,} roles")
    return jobs, problems


# ---------------------------------------------------------------- discovery
# No board has a search-all endpoint, so we find every company's slug from the Internet
# Archive's index of URLs (and Common Crawl's when it is up), then fetch each board.

DISCOVERY_HOSTS = {
    "greenhouse": ["boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"],
    "lever": ["jobs.lever.co", "jobs.eu.lever.co"],
    "ashby": ["jobs.ashbyhq.com"],
}
SLUG_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
NOT_SLUGS = {"embed", "jobs", "api", "static", "assets", "login", "about", "robots.txt", "sitemap.xml", "favicon.ico", "company"}
DISCOVERY_TTL_DAYS = 30


def fetch_text(url, timeout=150):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def slugs_from_urls(lines):
    found = {}
    for line in lines:
        m = re.match(r"https?://[^/]+/([^/?#\s]+)", line.strip())
        if not m:
            continue
        slug = urllib.parse.unquote(m.group(1))
        if SLUG_RX.match(slug) and slug.lower() not in NOT_SLUGS and not re.fullmatch(r"[0-9a-f-]{36}", slug):
            found.setdefault(slug.lower(), slug)
    return list(found.values())


def archive_urls(host, text=fetch_text):
    q = f"https://web.archive.org/cdx/search/cdx?url={host}/*&fl=original&collapse=urlkey&filter=statuscode:200&limit=250000"
    return text(q).splitlines()


def commoncrawl_urls(host, text=fetch_text):
    latest = json.loads(text("https://index.commoncrawl.org/collinfo.json", 30))[0]["cdx-api"]
    return [json.loads(l).get("url", "") for l in text(f"{latest}?url={host}/*&output=json&fl=url&limit=100000").splitlines() if l.startswith("{")]


def discover_slugs(text=fetch_text):
    """provider -> sorted slugs, merged from every source that answers. Returns (slugs, problems)."""
    out, problems = {}, []
    PROGRESS.start("Scanning the web archive", sum(len(h) for h in DISCOVERY_HOSTS.values()))
    for provider, hosts in DISCOVERY_HOSTS.items():
        urls = []
        for host in hosts:
            for source in (archive_urls, commoncrawl_urls):
                try:
                    urls.extend(source(host, text))
                except Exception as exc:
                    problems.append(f"discovery {source.__name__} {host}: {exc}")
            PROGRESS.advance()
        out[provider] = sorted(slugs_from_urls(urls), key=str.lower)
        PROGRESS.log(f"  discovered {len(out[provider]):,} {provider} slugs")
    PROGRESS.finish(f"{sum(len(v) for v in out.values()):,} slugs")
    return out, problems


def load_discovery(path, refresh=False, text=fetch_text, now=None):
    """Cached discovery: {'at': iso, 'slugs': {...}, 'dead': [...]}. Re-scans after DISCOVERY_TTL_DAYS."""
    now = now or datetime.now(timezone.utc)
    cache = {}
    if path.exists() and not refresh:
        try:
            cache = json.loads(path.read_text())
            if now - datetime.fromisoformat(cache["at"]) > timedelta(days=DISCOVERY_TTL_DAYS):
                cache = {}
        except Exception:
            cache = {}
    problems = []
    if not cache:
        slugs, problems = discover_slugs(text)
        cache = {"at": now.isoformat(), "slugs": slugs, "dead": []}
        if all(slugs.values()):  # a scan where a source was down would hide boards for 30 days
            paths.atomic_write(path, json.dumps(cache))
    return cache, problems


def pretty_name(slug):
    """'acme-corp' -> 'Acme Corp'. Keeps names that already have capitals (e.g. 'SignalFire')."""
    words = re.sub(r"[-_.]+", " ", slug).strip()
    return words if re.search(r"[A-Z]", words) else words.title()


def discovered_companies(cache, known):
    """Board entries for discovered slugs, minus ones already in companies.txt and ones known to be dead."""
    have = {(c["provider"], c["slug"].lower()) for c in known}
    dead = set(cache.get("dead", []))
    out = []
    for provider, slugs in cache.get("slugs", {}).items():
        for slug in slugs:
            if (provider, slug.lower()) in have or f"{provider}:{slug}" in dead:
                continue
            out.append({"provider": provider, "slug": slug, "name": pretty_name(slug), "discovered": True})
    return out


# ---------------------------------------------------------------- report

# key, badge text, badge style, filter-button text (None: no button). Order is the order of the report's buttons.
REGION_META = [
    ("africa-emea", "Open to Africa / EMEA", "ok", "Africa / EMEA"),  # older results
    ("africa", "Africa", "ok", "Africa"),
    ("nearby", "On-site, included", "ok", "On-site"),
    ("emea", "EMEA", "ok", "EMEA"),
    ("my-timezone", "My time zone", "ok", "My time zone"),
    ("worldwide", "Worldwide", "ok", "Worldwide"),
    ("europe", "Europe", "ok", "Europe"),
    ("middle-east", "Middle East", "ok", "Middle East"),
    ("americas", "Americas", "ok", "Americas"),
    ("asia-pacific", "Asia-Pacific", "ok", "Asia-Pacific"),
    ("unclear", "Check location", "warn", "Check location"),
    ("timezone", "Other time zone", "bad", "Other time zone"),
    ("restricted", "Region locked", "bad", "Region locked"),
    ("onsite", "On-site", "bad", "On-site elsewhere"),
]
REGION_LABEL = {key: (label, style) for key, label, style, _ in REGION_META}
REGION_ORDER = {key: i for i, (key, *_rest) in enumerate(REGION_META)}  # how results are ranked within "new" and "seen"


def display_company(name):
    """Discovered boards only know their slug ("polygon-labs"); make it readable without touching real names."""
    if not name or name != name.lower() or " " in name or "." in name:
        return name
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", name) if w)


REPORT_CSS = """
:root{--bg:#f4f5f2;--panel:#eceee8;--card:#fff;--ink:#1b1d1a;--dim:#5f635b;--faint:#8a8e85;--line:#dfe2da;--line2:#cfd3c9;
--ok:#1f6b3b;--okbg:#e0f0e5;--warn:#7d5400;--warnbg:#faedcc;--bad:#a3302a;--badbg:#f8e3e1;--new:#2147ad;--newbg:#e3e9fb;
--accent:#2147ad;--mark:#fbe8a6;--shadow:0 1px 2px rgba(20,24,18,.06),0 4px 14px rgba(20,24,18,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#121411;--panel:#181b17;--card:#1a1d19;--ink:#e8eae4;--dim:#a2a69d;--faint:#7b8076;
--line:#2b2f29;--line2:#3a3f37;--ok:#7fd49e;--okbg:#17291e;--warn:#efc15f;--warnbg:#2b2413;--bad:#f0928a;--badbg:#2d1817;
--new:#a3b8ff;--newbg:#1b2340;--accent:#a3b8ff;--mark:#4a3f12;--shadow:0 1px 2px rgba(0,0,0,.3),0 6px 18px rgba(0,0,0,.25)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scrollbar-color:var(--line2) transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif;font-variant-numeric:tabular-nums}
::selection{background:var(--mark);color:var(--ink)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
a{color:inherit;text-underline-offset:3px;text-decoration-thickness:1px;text-decoration-color:var(--line2)}
a:hover{text-decoration-color:currentColor}
main{max-width:1120px;margin:0 auto;padding:32px 16px 72px}
header h1{margin:0;font-size:24px;letter-spacing:-.02em;line-height:1.2}
.sum{margin:6px 0 0;color:var(--dim)}
.sum b{color:var(--ink);font-weight:600}
details{color:var(--dim);font-size:13.5px}
details summary{cursor:pointer;width:max-content;max-width:100%}
details summary:hover{color:var(--ink)}
.how{margin:8px 0 0}
.how ul{margin:6px 0 0;padding-left:18px}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);margin:20px -16px 0;padding:12px 16px 10px;border-bottom:1px solid transparent;transition:border-color .2s}
.bar.stuck{border-bottom-color:var(--line)}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.search{position:relative;flex:1 1 320px;min-width:0}
.search svg{position:absolute;left:11px;top:50%;translate:0 -50%;color:var(--faint);pointer-events:none}
.search input{width:100%;height:38px;padding:0 64px 0 34px;border:1px solid var(--line2);border-radius:10px;background:var(--card);color:var(--ink);font:inherit;caret-color:var(--accent)}
.search input::placeholder{color:var(--faint)}
.search input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
.search kbd{position:absolute;right:10px;top:50%;translate:0 -50%;font:12px/1 inherit;color:var(--faint);border:1px solid var(--line2);border-radius:5px;padding:3px 6px}
.search input:not(:placeholder-shown)~kbd{display:none}
.clear{position:absolute;right:6px;top:50%;translate:0 -50%;display:none;border:0;background:none;color:var(--dim);font:inherit;font-size:13px;padding:6px 8px;border-radius:6px;cursor:pointer}
.clear:hover{color:var(--ink);background:var(--panel)}
.search input:not(:placeholder-shown)~.clear{display:block}
.seg{display:inline-flex;padding:3px;gap:2px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
.seg button{border:0;background:none;color:var(--dim);font:inherit;font-size:13.5px;padding:5px 11px;border-radius:7px;cursor:pointer;white-space:nowrap;transition:background .15s,color .15s}
.seg button:hover{color:var(--ink)}
.seg button[aria-pressed=true]{background:var(--card);color:var(--ink);box-shadow:var(--shadow);font-weight:600}
.seg button .n{color:var(--faint);font-weight:400;margin-left:5px}
.seg button[disabled]{opacity:.45;cursor:default}
select,.toggle{height:34px;border:1px solid var(--line2);border-radius:9px;background:var(--card);color:var(--ink);font:inherit;font-size:13.5px;padding:0 10px;cursor:pointer}
.toggle{display:inline-flex;align-items:center;gap:7px;user-select:none}
.toggle input{accent-color:var(--accent);margin:0}
.filters{margin-top:10px}
.spacer{flex:1}
.count{color:var(--dim);font-size:13.5px;margin:12px 0 10px}
.count b{color:var(--ink)}
.b{display:inline-block;font-size:11.5px;font-weight:700;line-height:18px;padding:0 8px;border-radius:99px;white-space:nowrap}
.ok{color:var(--ok);background:var(--okbg)}.warn{color:var(--warn);background:var(--warnbg)}.bad{color:var(--bad);background:var(--badbg)}.new{color:var(--new);background:var(--newbg)}
mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 1px}
.cards{display:grid;gap:10px}
.job{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px 13px}
.job .h{display:flex;gap:16px;align-items:flex-start;justify-content:space-between}
.job h2{margin:0;font-size:16.5px;line-height:1.35;letter-spacing:-.01em;text-wrap:balance}
.job .co{margin:3px 0 0;color:var(--dim)}
.job .co b{color:var(--ink);font-weight:600}
.job .tags{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:9px}
.job .why{margin:7px 0 0;color:var(--faint);font-size:13px}
.fit{flex:none;text-align:right;line-height:1.1}
.fit b{display:block;font-size:20px;font-weight:650;letter-spacing:-.02em}
.fit span{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
.meta{color:var(--dim);font-size:13px}
.dim{color:var(--faint)}
table{width:100%;border-collapse:separate;border-spacing:0;background:var(--card);border:1px solid var(--line);border-radius:12px;font-size:13.5px}
thead th{position:sticky;top:var(--bar-h,0px);z-index:2;background:var(--panel);text-align:left;font-weight:600;color:var(--dim);font-size:12.5px;padding:0;border-bottom:1px solid var(--line);white-space:nowrap}
thead th:first-child{border-top-left-radius:11px}thead th:last-child{border-top-right-radius:11px}
thead th button{all:unset;display:flex;gap:4px;align-items:center;width:100%;box-sizing:border-box;padding:9px 12px;cursor:pointer}
thead th button:hover{color:var(--ink)}
thead th button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
thead th[aria-sort] button{color:var(--ink)}
thead th .arr{font-size:10px;opacity:.8}
thead th.num button{justify-content:flex-end}
tbody td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:color-mix(in srgb,var(--panel) 60%,transparent)}
td.num{text-align:right;font-weight:600}
td.t a{font-weight:600}
td.t .b{margin-left:6px;vertical-align:1px}
td.w{max-width:300px}
td.w .loc{display:block;margin-top:3px;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.d{white-space:nowrap;color:var(--dim)}
.more{display:flex;justify-content:center;margin:16px 0 0}.more[hidden]{display:none}
.more button{height:36px;padding:0 16px;border:1px solid var(--line2);border-radius:9px;background:var(--card);color:var(--ink);font:inherit;font-size:13.5px;cursor:pointer}
.more button:hover{border-color:var(--faint)}
.empty{background:var(--card);border:1px dashed var(--line2);border-radius:12px;padding:28px 20px;text-align:center;color:var(--dim)}
.empty p{margin:0 0 12px}
.empty button{height:34px;padding:0 14px;border:1px solid var(--line2);border-radius:9px;background:var(--panel);color:var(--ink);font:inherit;font-size:13.5px;cursor:pointer}
footer{margin-top:32px;display:grid;gap:10px}
footer ul{margin:8px 0 0;padding-left:18px;max-height:320px;overflow:auto}
.note{color:var(--faint);font-size:13px;margin:0}
@media (max-width:760px){
  main{padding-top:22px}
  .seg{overflow-x:auto;max-width:100%;scrollbar-width:none}
  .filters .spacer{display:none}
  td.w .loc,th.c,td.c,th.d,td.d{display:none}
  table{font-size:13px}
  tbody td{padding:8px}thead th button{padding:8px}
  td.w{width:1%;white-space:nowrap}
  td.t .sub{display:block;color:var(--dim);font-weight:400;margin-top:2px}
}
@media (min-width:761px){td.t .sub{display:none}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

REPORT_JS = r"""
(() => {
const RAW = JSON.parse(document.getElementById('jobs').textContent);
const META = JSON.parse(document.getElementById('region-meta').textContent);
const REGIONS = [['all','All']].concat(META.map(m => [m.key, m.button]));
const LABEL = Object.fromEntries(META.map(m => [m.key, [m.label, m.style]]));
const SRC_META = JSON.parse(document.getElementById('source-meta').textContent);
const SRC = Object.fromEntries(SRC_META.map(m => [m.key, m.label]));
const sourceName = k => SRC[k] || k || 'Unknown';
const PAGE = 120;
const jobs = RAW.map((j, i) => ({...j, i, hay: [j.t, j.c, j.l, j.rr, j.w, j.pay, sourceName(j.v)].join(' \u0001 ').toLowerCase()}));
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = u => /^https?:\/\//i.test(u || '') ? u : '#';
const store = {get(k){try{return localStorage.getItem('jobradar.'+k)}catch(e){return null}}, set(k,v){try{localStorage.setItem('jobradar.'+k,v)}catch(e){}}};

const st = {q:'', terms:[], neg:[], region:'all', source:'all', newOnly:false, sort:'best', dir:1, view: store.get('view') === 'table' ? 'table' : 'cards', shown: PAGE};

function parseQuery(q) {
  const words = q.toLowerCase().match(/"[^"]+"|\S+/g) || [];
  st.terms = []; st.neg = [];
  for (let w of words) {
    const neg = w.startsWith('-') && w.length > 1;
    if (neg) w = w.slice(1);
    w = w.replace(/^"|"$/g, '');
    if (w) (neg ? st.neg : st.terms).push(w);
  }
  termRx = st.terms.map(wordRx); negRx = st.neg.map(wordRx);
}
// A term matches from the start of a word, so "rust" finds Rust but not Trustly.
const reEsc = t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const wordRx = t => new RegExp('(^|[^a-z0-9])' + reEsc(t), 'i');
let termRx = [], negRx = [];
const matchesText = j => termRx.every(r => r.test(j.hay)) && !negRx.some(r => r.test(j.hay));

function hl(s) {
  let out = esc(s);
  if (!st.terms.length) return out;
  const rx = new RegExp('(^|[^a-z0-9])(' + st.terms.map(t => reEsc(esc(t))).join('|') + ')', 'gi');
  return out.replace(/(<[^>]*>)|([^<]+)/g, (m, tag, txt) => tag || txt.replace(rx, '$1<mark>$2</mark>'));
}

const SORTS = {
  best: (a, b) => a.i - b.i,
  fit: (a, b) => b.s - a.s || a.i - b.i,
  date: (a, b) => (b.p || '').localeCompare(a.p || '') || a.i - b.i,
  company: (a, b) => a.c.localeCompare(b.c, undefined, {sensitivity:'base'}) || a.i - b.i,
  title: (a, b) => a.t.localeCompare(b.t, undefined, {sensitivity:'base'}) || a.i - b.i,
  source: (a, b) => sourceName(a.v).localeCompare(sourceName(b.v)) || a.i - b.i,
  cv: (a, b) => (b.cv ?? -1) - (a.cv ?? -1) || a.i - b.i,
};

let filtered = [];
function compute() {
  // Each row of buttons counts what would be left with the OTHER row's choice applied, so the numbers always add up.
  const base = jobs.filter(j => matchesText(j) && (!st.newOnly || j.n));
  const inRegion = j => st.region === 'all' || j.r === st.region;
  const inSource = j => st.source === 'all' || j.v === st.source;
  const counts = {all: 0}, bySource = {all: 0};
  for (const j of base) {
    if (inSource(j)) { counts.all++; counts[j.r] = (counts[j.r] || 0) + 1; }
    if (inRegion(j)) { bySource.all++; bySource[j.v] = (bySource[j.v] || 0) + 1; }
  }
  filtered = base.filter(j => inRegion(j) && inSource(j));
  const cmp = SORTS[st.sort];
  filtered.sort(st.dir === 1 ? cmp : (a, b) => cmp(b, a));
  return {regions: counts, sources: bySource};
}

const SHORT = {'africa-emea':'Africa / EMEA','unclear':'Check','my-timezone':'My zone','asia-pacific':'APAC','middle-east':'Mid. East'};
function regionBadge(j, short) {
  const [label, cls] = LABEL[j.r] || [j.r, 'warn'];
  return `<span class="b ${cls}" title="${esc(label)}">${esc(short && SHORT[j.r] || label)}</span>`;
}
function badges(j) {
  return regionBadge(j) + (j.n ? ' <span class="b new">NEW</span>' : '') + (j.x ? ' <span class="b warn" title="Senior title: likely above your level">stretch</span>' : '');
}

const HAS_CV = RAW.some(j => (j.cv ?? -1) >= 0);
function cvChip(j) {
  if ((j.cv ?? -1) < 0) return '';
  const cls = j.cv >= 70 ? 'ok' : j.cv >= 40 ? 'warn' : 'bad';
  const tip = j.cm && j.cm.length ? 'Missing from your CV: ' + j.cm.join(', ') : 'Every skill it names is on your CV';
  return `<span class="b ${cls}" title="${esc(tip)}">CV ${j.cv}%</span>`;
}
function card(j) {
  return `<article class="job"><div class="h"><div><h2><a href="${esc(safeUrl(j.u))}" target="_blank" rel="noopener">${hl(j.t)}</a></h2>`
    + `<p class="co"><b>${hl(j.c)}</b> · ${hl(j.l || 'No location given')}</p></div>`
    + `<div class="fit" title="Fit score"><b>${j.s}</b><span>fit</span></div></div>`
    + `<div class="tags">${badges(j)}${cvChip(j) ? ' ' + cvChip(j) : ''}<span class="meta">Posted ${esc(j.p || 'date unknown')} · ${esc(sourceName(j.v))}${j.pay ? ' · ' + esc(j.pay) : ''}</span></div>`
    + `<p class="why">${esc(j.rr)}${j.w ? ' · ' + hl(j.w) : ''}</p></article>`;
}

function trow(j) {
  return `<tr><td class="num">${j.s}</td>` + (HAS_CV ? `<td class="num cv">${cvChip(j) || '<span class="dim">–</span>'}</td>` : '')
    + `<td class="t"><a href="${esc(safeUrl(j.u))}" target="_blank" rel="noopener">${hl(j.t)}</a>${j.n ? '<span class="b new">NEW</span>' : ''}${j.x ? '<span class="b warn">stretch</span>' : ''}<span class="sub">${hl(j.c)}</span></td>`
    + `<td class="c">${hl(j.c)}</td>`
    + `<td class="c">${esc(sourceName(j.v))}</td>`
    + `<td class="w">${regionBadge(j, true)}<span class="loc" title="${esc(j.l)}">${hl(j.l || 'No location given')}</span></td>`
    + `<td class="d">${esc(j.p || '?')}</td></tr>`;
}

const COLS = [['s','fit','Fit','num'], ...(HAS_CV ? [['cv','cv','CV','num cv']] : []),['t','title','Role',''],['c','company','Company','c'],['v','source','Board','c'],['w',null,'Where',''],['d','date','Posted','d']];
function thead() {
  return '<thead><tr>' + COLS.map(([k, sort, label, cls]) => {
    const active = sort && st.sort === sort;
    const aria = active ? ` aria-sort="${st.dir === 1 ? (sort === 'company' || sort === 'title' ? 'ascending' : 'descending') : (sort === 'company' || sort === 'title' ? 'descending' : 'ascending')}"` : '';
    const inner = sort ? `<button type="button" data-sort="${sort}">${label}<span class="arr">${active ? (st.dir === 1 ? '▾' : '▴') : ''}</span></button>` : `<button type="button" tabindex="-1" style="cursor:default">${label}</button>`;
    return `<th class="${cls}"${aria}>${inner}</th>`;
  }).join('') + '</tr></thead>';
}

const out = $('#out'), more = $('#more'), count = $('#count');
function draw(reset) {
  if (reset) { st.shown = PAGE; window.scrollTo({top: Math.min(window.scrollY, $('#bar').offsetTop)}); }
  const both = compute(), counts = both.regions;
  document.querySelectorAll('#sources button').forEach(b => {
    const n = both.sources[b.dataset.s] || 0;
    b.querySelector('.n').textContent = n.toLocaleString();
    b.setAttribute('aria-pressed', String(b.dataset.s === st.source));
    b.disabled = !n && b.dataset.s !== st.source;
    b.hidden = b.dataset.s !== 'all' && !n && b.dataset.s !== st.source;
  });
  document.querySelectorAll('#regions button').forEach(b => {
    const n = counts[b.dataset.r] || 0;
    b.querySelector('.n').textContent = n.toLocaleString();
    b.setAttribute('aria-pressed', String(b.dataset.r === st.region));
    b.disabled = !n && b.dataset.r !== st.region;
    b.hidden = b.dataset.r !== 'all' && !n && b.dataset.r !== st.region;  // only the kinds of role this list has
  });
  document.querySelectorAll('#views button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === st.view)));
  $('#sort').value = st.sort;
  const page = filtered.slice(0, st.shown);
  const total = filtered.length;
  count.innerHTML = total
    ? `Showing <b>${Math.min(st.shown, total).toLocaleString()}</b> of <b>${total.toLocaleString()}</b> role${total === 1 ? '' : 's'}`
    : '';
  if (!total) {
    out.innerHTML = `<div class="empty"><p>No roles match${st.q.trim() ? ` “${esc(st.q.trim())}”` : ''}${st.region !== 'all' ? ' in this location group' : ''}${st.source !== 'all' ? ` from ${esc(sourceName(st.source))}` : ''}${st.newOnly ? ' among new roles' : ''}.</p><button type="button" id="reset">Clear search and filters</button></div>`;
    $('#reset').onclick = () => { st.q = ''; $('#q').value = ''; parseQuery(''); st.region = 'all'; st.source = 'all'; st.newOnly = false; $('#newonly').checked = false; draw(true); };
  } else if (st.view === 'table') {
    out.innerHTML = `<table>${thead()}<tbody>${page.map(trow).join('')}</tbody></table>`;
  } else {
    out.innerHTML = `<div class="cards">${page.map(card).join('')}</div>`;
  }
  more.hidden = st.shown >= total;
  more.querySelector('button').textContent = `Show ${Math.min(PAGE, total - st.shown).toLocaleString()} more`;
}

function appendMore() {
  if (st.shown >= filtered.length) return;
  const next = filtered.slice(st.shown, st.shown + PAGE);
  st.shown += PAGE;
  const host = st.view === 'table' ? out.querySelector('tbody') : out.querySelector('.cards');
  host.insertAdjacentHTML('beforeend', next.map(st.view === 'table' ? trow : card).join(''));
  count.innerHTML = `Showing <b>${Math.min(st.shown, filtered.length).toLocaleString()}</b> of <b>${filtered.length.toLocaleString()}</b> roles`;
  more.hidden = st.shown >= filtered.length;
  more.querySelector('button').textContent = `Show ${Math.min(PAGE, filtered.length - st.shown).toLocaleString()} more`;
}

if (!HAS_CV) $('#sort option[value=cv]')?.remove();
$('#regions').innerHTML = REGIONS.map(([r, l]) => `<button type="button" data-r="${r}" aria-pressed="false">${l}<span class="n"></span></button>`).join('');
$('#regions').addEventListener('click', e => { const b = e.target.closest('button'); if (!b || b.disabled) return; st.region = b.dataset.r; draw(true); });
$('#sources').innerHTML = [['all','All']].concat(SRC_META.map(m => [m.key, m.label])).map(([s, l]) => `<button type="button" data-s="${s}" aria-pressed="false">${esc(l)}<span class="n"></span></button>`).join('');
$('#sources').addEventListener('click', e => { const b = e.target.closest('button'); if (!b || b.disabled) return; st.source = b.dataset.s; draw(true); });
$('#views').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; st.view = b.dataset.v; store.set('view', st.view); draw(false); });
$('#sort').addEventListener('change', e => { st.sort = e.target.value; st.dir = 1; draw(true); });
$('#newonly').addEventListener('change', e => { st.newOnly = e.target.checked; draw(true); });
out.addEventListener('click', e => {
  const b = e.target.closest('th button[data-sort]'); if (!b) return;
  const s = b.dataset.sort;
  if (st.sort === s) st.dir = -st.dir; else { st.sort = s; st.dir = 1; }
  draw(false);
  out.querySelector(`th button[data-sort="${s}"]`)?.focus();
});
let timer;
const q = $('#q');
q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = q.value; parseQuery(q.value); draw(true); }, 90); });
$('#clear').addEventListener('click', () => { q.value = ''; st.q = ''; parseQuery(''); draw(true); q.focus(); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== q && !/input|select|textarea/i.test(document.activeElement?.tagName || '')) { e.preventDefault(); q.focus(); q.select(); }
  else if (e.key === 'Escape' && document.activeElement === q && q.value) { q.value = ''; st.q = ''; parseQuery(''); draw(true); }
});
more.querySelector('button').addEventListener('click', appendMore);
if ('IntersectionObserver' in window) new IntersectionObserver(es => { if (es[0].isIntersecting) appendMore(); }, {rootMargin: '900px'}).observe(more);
const bar = $('#bar');
const setBarH = () => document.documentElement.style.setProperty('--bar-h', bar.offsetHeight + 'px');
if ('ResizeObserver' in window) new ResizeObserver(setBarH).observe(bar); setBarH();
const sentinel = $('#bar-sentinel');
if ('IntersectionObserver' in window) new IntersectionObserver(([e]) => bar.classList.toggle('stuck', !e.isIntersecting)).observe(sentinel);
draw(true);
})();
"""

SEARCH_ICON = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')


def render(jobs, problems, counts, days, now, skipped_label="not an AI or engineering title"):
    data = [{
        "t": j.title, "c": display_company(j.company), "u": j.url, "l": j.location, "r": j.region,
        "rr": j.region_reason, "p": (j.posted or "")[:10], "pay": j.pay, "s": j.score,
        "w": "; ".join(j.why), "n": bool(j.new), "x": bool(j.stretch),
        "cv": j.cv_match, "cm": j.cv_missing, "v": j.provider,
    } for j in jobs]
    # "</" would close the <script> early; JSON allows the escaped form.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    regions_json = json.dumps([{"key": k, "label": label, "style": style, "button": button}
                               for k, label, style, button in REGION_META], separators=(",", ":"))
    sources_json = json.dumps([{"key": k, "label": sources.name(k)} for k in sources.ordered(j.provider for j in jobs)],
                              separators=(",", ":"))
    new_n = sum(j.new for j in jobs)
    skipped = [
        (counts.get("not_ai", 0), skipped_label),
        (counts.get("senior", 0), "senior-level titles"),
        (counts.get("old", 0), f"posted more than {days} days ago"),
        (counts.get("region", 0), "region-locked, on-site or no region stated"),
        (counts.get("applied", 0), "already applied"),
        (counts.get("excluded", 0), "from excluded companies"),
        (counts.get("dupe", 0), "duplicates from aggregators"),
    ]
    how = "".join(f"<li>{n:,} {html.escape(why)}</li>" for n, why in skipped if n)
    probs = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
    failed = (f'<details><summary>Boards that failed ({len(problems)})</summary><ul>{probs}</ul></details>'
              if problems else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Job Radar</title><style>{REPORT_CSS}</style></head><body><main>
<header><h1>Job Radar</h1>
<p class="sum"><b>{len(jobs):,}</b> roles · <b>{new_n:,}</b> new · last {days} days · checked {now:%d %b %Y, %H:%M} UTC</p>
<details class="how"><summary>How this list was filtered</summary><p>Read {counts.get('boards', 0):,} job boards
({counts.get('dead', 0):,} discovered names had no board). Left out:</p><ul>{how or '<li>nothing</li>'}</ul></details></header>
<div id="bar-sentinel"></div>
<div class="bar" id="bar"><div class="row">
<label class="search">{SEARCH_ICON}<input id="q" type="search" placeholder="Search roles, companies, skills" aria-label="Search roles" autocomplete="off" spellcheck="false"><kbd>/</kbd><button type="button" class="clear" id="clear">Clear</button></label>
<div class="seg" id="views" role="group" aria-label="View"><button type="button" data-v="cards" aria-pressed="true">Cards</button><button type="button" data-v="table" aria-pressed="false">Table</button></div>
</div>
<div class="row filters"><div class="seg" id="regions" role="group" aria-label="Location"></div><span class="spacer"></span>
<label class="toggle"><input type="checkbox" id="newonly"> New only</label>
<select id="sort" aria-label="Sort by"><option value="best">Best match</option><option value="fit">Fit score</option><option value="date">Newest</option><option value="company">Company A–Z</option><option value="title">Role A–Z</option><option value="cv">CV match</option></select>
</div>
<div class="row filters"><span class="meta">Job board</span><div class="seg" id="sources" role="group" aria-label="Job board"></div></div></div>
<p class="count" id="count" aria-live="polite"></p>
<div id="out"><noscript><p class="empty">This report needs JavaScript to show its {len(jobs):,} roles.</p></noscript></div>
<div class="more" id="more" hidden><button type="button">Show more</button></div>
<footer>{failed}
<p class="note">Location labels come from each posting's own text and can be wrong. Open the posting and check before you apply. Search tips: type several words to match all of them, put a phrase in "quotes", and start a word with - to exclude it.</p></footer>
</main><script type="application/json" id="region-meta">{regions_json}</script><script type="application/json" id="source-meta">{sources_json}</script><script type="application/json" id="jobs">{payload}</script><script>{REPORT_JS}</script></body></html>"""


def _remote_arg(text):
    try:
        return ",".join(scopes.parse(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _zone_arg(text):
    zone = timezones.normalize(text)
    if zone is None:
        raise argparse.ArgumentTypeError(f"{text!r} is not a time zone: use auto, or something like UTC+1, UTC-8 or UTC+5.5")
    return zone


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="include region-locked and on-site roles")
    ap.add_argument("--remote", type=_remote_arg, default=",".join(scopes.DEFAULT), metavar="KINDS",
                    help="the kinds of remote role to keep, separated by commas: " + ", ".join(scopes.KEYS)
                         + f" (default: {','.join(scopes.DEFAULT)})")
    ap.add_argument("--emea", action="store_true", help="the older shortcut for --remote " + ",".join(scopes.EMEA_WIDE))
    ap.add_argument("--any-role", action="store_true", help="don't limit to AI and engineering titles")
    ap.add_argument("--roles", choices=sorted(ROLE_PRESETS), help="only these titles instead of AI/engineering (web = backend, frontend, fullstack, software engineer, web developer; cv = the stack on your CV)")
    ap.add_argument("--no-senior", action="store_true", help="drop senior, staff, principal, lead, manager and similar titles")
    ap.add_argument("--discover", action="store_true", help="also read every Greenhouse/Lever/Ashby board found by slug discovery")
    ap.add_argument("--refresh-discovery", action="store_true", help="re-scan for board slugs now instead of using the cache")
    ap.add_argument("--aggregators", action="store_true", help="also pull Remotive, Arbeitnow, Jobicy, Himalayas, RemoteOK and Hacker News Who is hiring")
    ap.add_argument("--agg-pages", type=int, default=5, help="pages to read per paginated aggregator (default 5)")
    ap.add_argument("--everything", action="store_true", help="--discover --aggregators --emea --any-role")
    ap.add_argument("--workers", type=int, default=16, help="parallel requests (default 16)")
    ap.add_argument("--out", default=str(HERE / "report.html"))
    ap.add_argument("--cv", help="score jobs against your CV (.pdf/.docx/.txt/.md); cv.* next to jobradar.py is used by default")
    ap.add_argument("--no-cv", action="store_true", help="ignore any CV file")
    ap.add_argument("--open", action="store_true", help="open report.html in your browser when the run finishes")
    ap.add_argument("--timezone", type=_zone_arg, default=timezones.AUTO, metavar="ZONE",
                    help="your time zone: auto (this computer, the default) or something like UTC+1, UTC-8, UTC+5.5")
    ap.add_argument("--tz-range", type=float, default=timezones.DEFAULT_RANGE, metavar="HOURS",
                    help="hours from your time zone you are happy to work (default 3). 24 or more: don't filter by time zone")
    ap.add_argument("--onsite", action="store_true", help="also keep on-site and hybrid roles, not only remote ones")
    ap.add_argument("--places", default="", help='where you would work on-site, e.g. "Accra, Ghana" (blank: anywhere)')
    ap.add_argument("--no-settings", action="store_true", help="ignore settings.json (python cli.py settings edits it)")
    return ap


def parse_args(argv=None, settings_path=None):
    """Flags, with settings.json supplying the defaults. Anything typed on the command line wins."""
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_parser()
    if "--no-settings" in argv:
        args = ap.parse_args(argv)
    else:
        args = ap.parse_args(settings.to_args(settings.load(settings_path)) + argv)
    if args.emea:  # the older shortcut for a wide set of boxes
        args.remote = ",".join(scopes.EMEA_WIDE)
    return args


SEEN_KEEP_DAYS = 90  # forget a role this long after it was last listed on any board


def load_seen_state(path, today=None):
    """What has been shown, as {role key: the date it was last listed on a board}, and a note if the file had to be
    set aside. Older files were a plain list of keys; those are read with today's date, so they get a full
    SEEN_KEEP_DAYS from now. A damaged file (a save that was cut off) is moved to <name>.damaged so it can never
    stop every future run."""
    path = Path(path)
    today = today or datetime.now(timezone.utc).date().isoformat()
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(k): today for k in data}, None
        if not isinstance(data, dict):
            raise ValueError("not a list or a mapping")
        return {str(k): (v if isinstance(v, str) and _is_date(v) else today) for k, v in data.items()}, None
    except (ValueError, OSError):
        aside = path.with_name(path.name + ".damaged")
        try:
            os.replace(path, aside)
        except OSError:
            aside = None
        kept = f"; the old file is kept as {aside.name}" if aside else ""
        return {}, f"{path.name} was damaged (a run was probably cut off while saving), so every role counts as new this time{kept}"


def load_seen(path):
    """The role keys already shown, and a note if the file had to be set aside (see load_seen_state)."""
    state, note = load_seen_state(path)
    return set(state), note


def _is_date(text):
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def update_seen(state, listed, shown, today, keep_days=SEEN_KEEP_DAYS):
    """The seen list after a run. Every role that is listed on a board now, or was shown, is dated today. Roles that
    nothing has listed for keep_days are forgotten, so the file follows the boards instead of growing for ever.
    Returns (new state, how many were forgotten)."""
    state = dict(state)
    for key in set(listed) | set(shown):
        state[key] = today
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    kept = {k: d for k, d in state.items() if d >= cutoff}
    return kept, len(state) - len(kept)


def main(argv=None):
    """Run a search. Ctrl+C stops it cleanly: nothing is half-saved, so your files are exactly as they were."""
    try:
        return _main(argv)
    except KeyboardInterrupt:
        PROGRESS.abort()
        print("\nStopped. Your results, seen list and settings are as they were.")
        return 130


def _main(argv=None):
    STOP.clear()
    for stream in (sys.stdout, sys.stderr):  # a Windows code page must not crash a run over a "·" or an emoji
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    if args.cv:
        args.cv = settings.locate_cv(args.cv, HERE)
    if args.everything:
        args.discover = args.aggregators = args.emea = args.any_role = True
    if args.emea:
        args.remote = ",".join(scopes.EMEA_WIDE)

    companies, bad = [], []
    for line in read_lines(HERE / "companies.txt"):
        try:
            c = parse_company_line(line)
            if c:
                companies.append(c)
        except ValueError as exc:
            bad.append(str(exc))

    use_progress(Progress(enabled=True))
    problems = list(bad)
    cache, cache_path = None, HERE / "discovered.json"
    if args.discover:
        scanning = args.refresh_discovery or not cache_path.exists()
        print("loading discovered boards" + (" (this scans the web archive and takes a few minutes)" if scanning else "") + "...")
        cache, disc_problems = load_discovery(cache_path, args.refresh_discovery)
        problems += disc_problems
        companies += discovered_companies(cache, companies)
    extra = []
    if args.aggregators:
        print("reading job aggregators...")
        extra, agg_problems = fetch_aggregators(args.agg_pages)
        problems += agg_problems
    if not companies and not extra:
        print("companies.txt is empty. Paste careers URLs into it, or use --discover / --aggregators.")
        return 1

    state_path = HERE / "seen.json"
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    seen_state, seen_note = load_seen_state(state_path, today)
    seen = set(seen_state)
    listed = set()
    if seen_note:
        problems.append(seen_note)
    print(f"checking {len(companies)} job boards...")
    dead = set()
    cv = None
    cv_path = None if args.no_cv else (args.cv or cvmatch.find_cv(HERE))
    if cv_path:
        try:
            cv = cvmatch.load(cv_path)
            names = sorted(cv.skills)
            more = f" and {len(names) - 6} more" if len(names) > 6 else ""
            print(f"scoring against {Path(cv_path).name}: {len(cv.skills)} skills ({', '.join(names[:6])}{more})")
        except FileNotFoundError:
            problems.append(f"CV: no file at {cv_path}. Pick it again in Settings, or clear it to go without a CV.")
        except Exception as exc:
            problems.append(f"CV {Path(cv_path).name}: {exc}")
    role_rx = ROLE_PRESETS.get(args.roles)
    if args.roles == "cv" and cv:
        role_rx = roles_regex_for_cv(cv.skills)
        print(f"keeping titles that fit the {len(cv.skills)} skills on your CV")
    elif args.roles == "cv":
        print("no CV to read, so the titles are a typical software and backend list. Set your CV in Settings to tailor them")
    tz = timezones.make(args.timezone, args.tz_range)
    places = tuple(p.strip() for p in re.split(r"[,;]", args.places) if p.strip())
    remote = scopes.parse(args.remote)
    print("remote roles: " + (", ".join(scopes.LABELS[k] for k in remote) if remote else "none ticked, so only on-site roles can show"))
    if tz:
        print(f"time zone: {timezones.fmt(tz.offset)}"
              f"{' (this computer)' if args.timezone == timezones.AUTO else ''}, roles within {tz.hours:g}h of it")
    if args.onsite:
        print("on-site roles: " + (", ".join(places) if places else "anywhere"))
    jobs, run_problems, counts = run(
        companies, read_lines(HERE / "exclude.txt"), read_lines(HERE / "applied.txt"),
        args.days, args.all, seen, now=now, any_role=args.any_role, role_rx=role_rx, no_senior=args.no_senior,
        remote=remote, listed_out=listed,
        workers=args.workers, light=args.discover, extra_jobs=extra, dead_out=dead, cv=cv,
        tz=tz, onsite=args.onsite, places=places,
    )
    problems += run_problems
    if cache is not None and dead:
        cache["dead"] = sorted(set(cache.get("dead", [])) | dead)
        paths.atomic_write(cache_path, json.dumps(cache))
    # Each file is saved whole or not at all. The seen list goes last: if a run is cut off before it, the roles
    # just show as new again instead of being marked seen without ever reaching your results.
    paths.atomic_write(args.out, render(jobs, problems, counts, args.days, now,
                                        "outside the role filter" if (args.roles or args.any_role) else "not an AI or engineering title"))
    paths.atomic_write(HERE / "jobs.json", json.dumps([asdict(j) | {"description": j.description[:400]} for j in jobs], indent=2))
    seen_state, forgotten = update_seen(seen_state, listed, (j.key for j in jobs), today)
    paths.atomic_write(state_path, json.dumps(seen_state, sort_keys=True, indent=0))
    if forgotten:
        print(f"forgot {forgotten:,} old roles that no board has listed for {SEEN_KEEP_DAYS} days")

    new = [j for j in jobs if j.new]
    print(f"\n{len(jobs)} matching roles, {len(new)} new.\n")
    for j in new[:25]:
        print(f"  NEW  [{REGION_LABEL[j.region][0]}] {j.company}: {j.title}\n       {j.url}")
    if problems:
        print(f"\n{len(problems)} problems:")
        for p in problems[:30]:
            print("   ", p)
    print(f"\nreport: {args.out}")
    if args.open:
        ok, label = openurl.open_url(openurl.file_target(args.out))
        print(f"opened in {label}" if ok else f"couldn't open a browser; open {args.out} yourself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
