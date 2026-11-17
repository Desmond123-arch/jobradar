"""jobradar: check companies' own job boards for remote AI roles you can actually take.

Reads the free public APIs behind Greenhouse, Lever and Ashby job boards (no key
needed), keeps roles that are about AI and open to Lagos (worldwide, Africa or
EMEA), skips companies you've excluded and roles you've applied to, and marks
what's new since the last run.

  python jobradar.py                 run and write report.html
  python jobradar.py --days 60       look back 60 days instead of 30
  python jobradar.py --all           also show region-locked and on-site roles

Add companies by pasting their careers page URL into companies.txt.
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "jobradar/1.0 (personal job search)"

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


def from_greenhouse(company, data):
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "") or ""
        out.append(Job(
            company=company, title=j.get("title", ""), url=j.get("absolute_url", ""),
            location=loc, remote="remote" in loc.lower(),
            posted=j.get("first_published") or j.get("updated_at") or "",
            description=strip_html(j.get("content", "")), provider="greenhouse", job_id=str(j.get("id", "")),
        ))
    return out


def from_lever(company, data):
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


def from_ashby(company, data):
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


def fetch_company(c, fetch=fetch_json):
    url_tpl, parse = ENDPOINTS[c["provider"]]
    return parse(c["name"], fetch(url_tpl.format(slug=c["slug"])))


# ---------------------------------------------------------------- judging

WORLDWIDE = re.compile(r"\b(anywhere|worldwide|world ?wide|global(ly)?|all countries|any (country|location)|fully remote|remote[- ]first)\b", re.I)
AFRICA_EMEA = re.compile(r"\b(africa|nigeria|lagos|emea|sub-saharan|gmt\+?1|wat|cet|europe, middle east)\b", re.I)
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


def classify_region(job: Job):
    loc = job.location or ""
    if AFRICA_EMEA.search(loc):  # includes on-site in Lagos, which is fine for you
        return "africa-emea", f"location names Africa/EMEA: {loc}"
    if not job.remote and not WORLDWIDE.search(loc):
        return "onsite", f"not marked remote ({loc or 'no location'})"
    if WORLDWIDE.search(loc):
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
    if job.region == "africa-emea":
        s += 4
    job.stretch = bool(SENIOR_STRETCH.search(job.title))
    if job.stretch:
        s -= 4
        why.append("senior title, stretch")
    job.score, job.why = s, why
    return s


def is_engineering_ai(job: Job):
    if TITLE_SKIP.search(job.title):
        return False
    return bool(TITLE_STRONG.search(job.title) or TITLE_OK.search(job.title))


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def posted_dt(job):
    try:
        return datetime.fromisoformat(job.posted.replace("Z", "+00:00"))
    except Exception:
        return None


def run(companies, excluded, applied, days, show_all, seen, fetch=fetch_json, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    excluded_n = {norm(x) for x in excluded}
    applied_n = set()
    for line in applied:
        if "|" in line:
            c, t = [p.strip() for p in line.split("|", 1)]
            applied_n.add((norm(c), norm(t)))
    kept, problems, counts = [], [], {"fetched": 0, "not_ai": 0, "old": 0, "excluded": 0, "applied": 0, "region": 0}
    for c in companies:
        if norm(c["name"]) in excluded_n:
            counts["excluded"] += 1
            continue
        try:
            jobs = fetch_company(c, fetch)
        except Exception as exc:
            problems.append(f"{c['name']} ({c['provider']}:{c['slug']}): {exc}")
            continue
        for job in jobs:
            counts["fetched"] += 1
            if not is_engineering_ai(job):
                counts["not_ai"] += 1
                continue
            p = posted_dt(job)
            if p and p < cutoff:
                counts["old"] += 1
                continue
            if (norm(job.company), norm(job.title)) in applied_n:
                counts["applied"] += 1
                continue
            job.region, job.region_reason = classify_region(job)
            if not show_all and job.region in ("restricted", "onsite"):
                counts["region"] += 1
                continue
            score_job(job)
            job.new = job.key not in seen
            kept.append(job)
    order = {"africa-emea": 0, "worldwide": 1, "unclear": 2, "restricted": 3, "onsite": 4}
    kept.sort(key=lambda j: (not j.new, order[j.region], -j.score))
    return kept, problems, counts


# ---------------------------------------------------------------- report

REGION_LABEL = {
    "africa-emea": ("Open to Africa / EMEA", "ok"),
    "worldwide": ("Worldwide", "ok"),
    "unclear": ("Check location", "warn"),
    "restricted": ("Region locked", "bad"),
    "onsite": ("On-site", "bad"),
}


def render(jobs, problems, counts, days, now):
    def row(j):
        label, cls = REGION_LABEL[j.region]
        date = j.posted[:10] if j.posted else "?"
        badges = (' <span class="b new">NEW</span>' if j.new else "") + (' <span class="b warn">stretch</span>' if j.stretch else "")
        return (
            f'<article class="job"><div class="top"><span class="b {cls}">{html.escape(label)}</span>{badges}'
            f'<span class="meta">{html.escape(j.company)} · posted {date}{(" · " + html.escape(j.pay)) if j.pay else ""}</span></div>'
            f'<h2><a href="{html.escape(j.url)}" target="_blank" rel="noopener">{html.escape(j.title)}</a></h2>'
            f'<p class="loc">{html.escape(j.location or "no location given")}. <span class="dim">{html.escape(j.region_reason)}</span></p>'
            f'<p class="dim">Fit {j.score}: {html.escape("; ".join(j.why) or "title match")}</p></article>'
        )
    new_n = sum(j.new for j in jobs)
    probs = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Radar</title><style>
:root{{--bg:#f4f5f2;--card:#fff;--ink:#1b1d1a;--dim:#676b63;--line:#dfe2da;--ok:#21733f;--okbg:#e2f1e7;--warn:#8a5d00;--warnbg:#fbefd2;--bad:#a3302a;--badbg:#f8e3e1;--new:#2349b8;--newbg:#e3e9fb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121411;--card:#1a1d19;--ink:#e8eae4;--dim:#9a9e95;--line:#2b2f29;--ok:#79d39a;--okbg:#17291e;--warn:#efc15f;--warnbg:#2b2413;--bad:#f0928a;--badbg:#2d1817;--new:#9db4ff;--newbg:#1b2340}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:28px max(16px,env(safe-area-inset-left)) 60px}}
h1{{margin:0 0 4px;font-size:26px}}.sum{{color:var(--dim);margin:0 0 20px}}
.job{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 10px}}
.job h2{{font-size:17px;margin:6px 0 2px}}.job a{{color:var(--ink)}}.top{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.meta,.dim{{color:var(--dim);font-size:13px}}.loc{{margin:2px 0}}p{{margin:2px 0}}
.b{{font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:99px}}.ok{{color:var(--ok);background:var(--okbg)}}.warn{{color:var(--warn);background:var(--warnbg)}}
.bad{{color:var(--bad);background:var(--badbg)}}.new{{color:var(--new);background:var(--newbg)}}
details{{margin-top:24px;color:var(--dim)}}
</style></head><body><main>
<h1>Job Radar</h1>
<p class="sum">{len(jobs)} roles ({new_n} new) from the last {days} days · checked {now:%d %b %Y %H:%M} UTC.
Skipped: {counts['not_ai']} not AI or engineering, {counts['old']} older, {counts['region']} region-locked or on-site, {counts['applied']} already applied, {counts['excluded']} excluded companies.</p>
{''.join(row(j) for j in jobs) or '<p>No matching roles right now.</p>'}
<details><summary>Boards that failed ({len(problems)})</summary><ul>{probs}</ul></details>
<p class="dim">Location labels come from the job's own text and can be wrong. Open the posting and confirm before applying.</p>
</main></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="include region-locked and on-site roles")
    ap.add_argument("--out", default=str(HERE / "report.html"))
    args = ap.parse_args(argv)

    companies, bad = [], []
    for line in read_lines(HERE / "companies.txt"):
        try:
            c = parse_company_line(line)
            if c:
                companies.append(c)
        except ValueError as exc:
            bad.append(str(exc))
    if not companies:
        print("companies.txt is empty. Paste careers URLs into it, one per line.")
        return 1

    state_path = HERE / "seen.json"
    seen = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    now = datetime.now(timezone.utc)
    print(f"checking {len(companies)} job boards...")
    jobs, problems, counts = run(
        companies, read_lines(HERE / "exclude.txt"), read_lines(HERE / "applied.txt"),
        args.days, args.all, seen, now=now,
    )
    problems = bad + problems
    Path(args.out).write_text(render(jobs, problems, counts, args.days, now), encoding="utf-8")
    state_path.write_text(json.dumps(sorted(seen | {j.key for j in jobs}), indent=0))
    (HERE / "jobs.json").write_text(json.dumps([asdict(j) | {"description": j.description[:400]} for j in jobs], indent=2))

    new = [j for j in jobs if j.new]
    print(f"\n{len(jobs)} matching roles, {len(new)} new.\n")
    for j in new[:25]:
        print(f"  NEW  [{REGION_LABEL[j.region][0]}] {j.company}: {j.title}\n       {j.url}")
    if problems:
        print(f"\n{len(problems)} boards had problems:")
        for p in problems:
            print("   ", p)
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
