# Job Radar

> Find remote AI engineering roles you can actually take — from Lagos, Africa, or anywhere with an EMEA-open board — without touching a recruiter portal.

Job Radar reads the **free public JSON APIs** behind [Greenhouse](https://boards-api.greenhouse.io), [Lever](https://api.lever.co/v0/postings) and [Ashby](https://api.ashbyhq.com/posting-api/job-board) job boards (no API key, no sign-up). It filters to AI and engineering roles that are open to Lagos / Africa / EMEA or worldwide, hides roles you've already applied to, marks what's new since last run, scores every role for fit, and writes a self-contained `report.html`.

```
python jobradar.py
start report.html          # Windows
open report.html           # macOS / Linux
```

---

## Why this exists

Most job boards are built around the assumption you're already in San Francisco or London. This one starts from the other end: given a list of companies whose APIs I can hit for free, what's actually open to me? It won't apply for you — it just surfaces the signal.

---

## Quick start

**Requirements:** Python 3.10+. No third-party packages.

```bash
git clone https://github.com/OsasDTEch/jobradar.git
cd jobradar

# verify everything works
python -m unittest -v          # 13 tests → OK

# first run
python jobradar.py
```

Open `report.html`. Run it again tomorrow and new roles are marked **NEW**.

---

## How it works

```
companies.txt  ──►  fetch each board's public API
                        │
                   filter: AI/engineering title only
                   filter: posted within --days (default 30)
                   filter: not in applied.txt
                   filter: not in exclude.txt
                        │
                   classify region:
                     africa-emea  ✅  (location says Africa / Nigeria / EMEA)
                     worldwide    ✅  (says "anywhere" and description agrees)
                     unclear      ⚠️  (says Remote, no region — read the posting)
                     restricted   ✗   (Remote - US only, etc.)
                     onsite       ✗   (no remote, not in Africa)
                        │
                   score for fit  (AI title + skill keyword matches)
                   mark new vs seen
                        │
                   report.html  +  jobs.json  +  seen.json
```

---

## Adding companies

Paste the careers page URL into `companies.txt`, one per line:

```
https://jobs.ashbyhq.com/somecompany | Some Company
https://jobs.lever.co/othercompany
https://job-boards.greenhouse.io/thirdcompany
```

The `| Name` part is optional display text. Lines starting with `#` are comments.

**How to find the right URL:** open a job posting on [ycombinator.com/jobs](https://www.ycombinator.com/jobs), [hnhiring.com](https://hnhiring.com/locations/remote), [startup.jobs](https://startup.jobs) or a company's own careers page. If the Apply button takes you to an address starting with `jobs.ashbyhq.com`, `jobs.lever.co` or `job-boards.greenhouse.io`, copy that domain + slug into `companies.txt`. Wrong slugs show up under "Boards that failed" in the report.

---

## Tracking applications

Every time you apply, add one line to `applied.txt`:

```
Company Name | Job title
```

That role won't appear in future runs.

---

## Excluding companies

Add a company name to `exclude.txt` (one per line) to skip it entirely — useful for companies you've ruled out or that only post roles you can't take.

---

## Command-line options

| Flag | Default | Effect |
|------|---------|--------|
| `--days N` | 30 | Include roles posted within the last N days |
| `--all` | off | Also show region-locked and on-site roles |
| `--out PATH` | `report.html` | Write the report to a different path |

---

## Running the tests

```bash
python -m unittest -v
```

13 tests cover parsing, filtering, region classification, scoring, the applied/excluded logic, and HTML rendering. They use fixture data and never hit the network.

---

## Automated daily run (GitHub Actions)

The included workflow (`.github/workflows/radar.yml`) runs every morning at **06:37 Lagos time**, runs the tests, fetches all boards, and commits a fresh `report.html` and `jobs.json` back to the repo. You can also trigger it manually from the Actions tab.

> **Keep your fork private.** `applied.txt` contains your application history and `seen.json` / `report.html` contain the results. None of that should be public.

To enable it:

1. Fork or clone this repo into your own **private** GitHub repo.
2. Push your `companies.txt` and (optionally) `applied.txt`.
3. The workflow runs automatically — no secrets needed beyond the default `GITHUB_TOKEN`.

---

## File reference

| File | Purpose |
|------|---------|
| `jobradar.py` | Main script. Run directly or import in tests. |
| `companies.txt` | One careers-page URL per line. Edit this. |
| `applied.txt` | Roles you've applied to. `Company \| Title` per line. |
| `exclude.txt` | Company names to skip entirely. |
| `seen.json` | Auto-generated. Tracks which roles have been shown before. |
| `jobs.json` | Auto-generated. Last run's matching roles as JSON. |
| `report.html` | Auto-generated. The human-readable report. |
| `tests/test_jobradar.py` | 13 unit tests. No network calls. |
| `.github/workflows/radar.yml` | Daily GitHub Actions run. |

---

## Contributing

Pull requests are welcome. A few ground rules:

- All 13 tests must still pass: `python -m unittest -v`
- Don't add third-party dependencies — the zero-install design is intentional.
- If you add a new board provider, add fixture tests for it in the same style as the Greenhouse / Lever / Ashby tests.
- Region classification is intentionally conservative. If you think a role should be shown to Lagos-based candidates, the right fix is a test that proves the new rule is correct.

---

## Supported job board APIs

| Provider | API base | Slug source |
|----------|----------|-------------|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs` | URL after `job-boards.greenhouse.io/` |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` | URL after `jobs.lever.co/` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{slug}` | URL after `jobs.ashbyhq.com/` |

All three are public, unauthenticated, and officially documented by their providers.

---

## License

MIT. Do whatever you want with it. If you find a job through it, that'd be nice to hear.
