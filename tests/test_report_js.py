"""The web report is a page with a script. These tests run that script (in Node, with a stand-in page) so a mistake in it
fails here, not in somebody's browser. Skipped when Node is not installed."""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobradar as jr  # noqa: E402

NODE = shutil.which("node")
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)

HARNESS = r"""
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));   // {js, json: {id: text}, elements: [ids]}
const created = {};
const listeners = {};   // selector -> [{type, fn}]
const btns = {};
// The buttons the script finds with querySelectorAll are rebuilt from the markup it wrote, like a browser would.
function buttonsFor(sel, attr) {
  const host = created[sel.split(' ')[0]];
  const html = host ? String(host.innerHTML) : '';
  const keys = [...html.matchAll(new RegExp(attr + '="([^"]+)"', 'g'))].map(m => m[1]);
  return keys.map(k => {
    const id = sel + '|' + k;
    if (!btns[id]) { const n = {textContent: ''}; btns[id] = {dataset: {[attr.slice(5)]: k}, n, hidden: false, disabled: false, pressed: '', querySelector: () => n, setAttribute(a, v) { if (a === 'aria-pressed') btns[id].pressed = v; }}; }
    return btns[id];
  });
}
function stub(name) {
  if (created[name]) return created[name];
  const store = {innerHTML: '', textContent: '', value: '', hidden: false, checked: false, disabled: false, dataset: {}, style: {}};
  const el = new Proxy(function () {}, {
    get(t, p) {
      if (p in store) return store[p];
      if (p === 'classList') return {toggle() {}, add() {}, remove() {}, contains() { return false; }};
      if (p === 'addEventListener') return (type, fn) => { (listeners[name] = listeners[name] || []).push({type, fn}); };
      if (p === 'querySelector') return () => stub(name + ' >');
      if (p === 'querySelectorAll') return () => [];
      if (p === 'closest') return () => null;
      if (p === 'getBoundingClientRect') return () => ({top: 0, height: 0});
      if (p === Symbol.toPrimitive) return () => name;
      return () => undefined;
    },
    set(t, p, v) { store[p] = v; return true; },
    apply() { return undefined; },
  });
  created[name] = el;
  return el;
}
// Like a browser: getElementById returns the FIRST element with that id in the document, whatever it is.
const document = {
  getElementById(id) {
    if (spec.firstKind[id] === 'json') return {textContent: spec.json[id]};
    return spec.firstKind[id] === 'element' ? stub('#' + id) : null;
  },
  querySelector: (sel) => stub(sel),
  querySelectorAll: (sel) => sel === '#regions button' ? buttonsFor(sel, 'data-r') : sel === '#sources button' ? buttonsFor(sel, 'data-s') : [],
  addEventListener() {},
  documentElement: {style: {setProperty() {}}},
  activeElement: null,
};
const window = {scrollTo() {}, scrollY: 0};
const localStorage = {getItem() { return null; }, setItem() {}};
let error = null;
const snapshots = [];
function snap() {
  const state = (sel, attr) => Object.fromEntries(buttonsFor(sel, attr).map(b => [b.dataset[attr.slice(5)], {n: b.n.textContent, hidden: b.hidden, pressed: b.pressed}]));
  return {
    out: String(created['#out'] ? created['#out'].innerHTML : ''), count: String(created['#count'] ? created['#count'].innerHTML : ''),
    regions: state('#regions button', 'data-r'), sources: state('#sources button', 'data-s'),
  };
}
try {
  new Function('document', 'window', 'localStorage', spec.js)(document, window, localStorage);
  snapshots.push(snap());
  for (const a of spec.actions || []) {          // press a button: {sel: '#sources', dataset: {s: 'lever'}}
    for (const l of listeners[a.sel] || []) if (l.type === (a.type || 'click')) l.fn({target: {closest: () => ({dataset: a.dataset, disabled: false})}});
    snapshots.push(snap());
  }
} catch (e) { error = String(e && e.message || e); }
const last = snapshots.length ? snapshots[snapshots.length - 1] : {out: '', count: '', regions: {}, sources: {}};
process.stdout.write(JSON.stringify({
  error, out: last.out, count: last.count, regionButtons: last.regions, sourceButtons: last.sources, snapshots,
  buttons: String(created['#regions'] ? created['#regions'].innerHTML : ''), sources: String(created['#sources'] ? created['#sources'].innerHTML : ''),
}));
"""


def run_page(page, actions=()):
    """Run a report page's script against a stand-in document, then press any buttons named in actions
    ({'sel': '#sources', 'dataset': {'s': 'lever'}}). Returns what the page showed after the last one."""
    js = page.split("<script>")[-1].split("</script>")[0]
    first_kind, blobs = {}, {}
    for m in re.finditer(r"<(\w+)([^>]*)\bid=\"([^\"]+)\"([^>]*)>", page):
        tag, before, ident, after = m.groups()
        if ident in first_kind:
            continue  # a browser would hand back the first one
        if tag == "script" and "application/json" in before + after:
            first_kind[ident] = "json"
            blobs[ident] = page[m.end():page.index("</script>", m.end())].replace("<\\/", "</")
        else:
            first_kind[ident] = "element"
    with tempfile.TemporaryDirectory() as d:
        spec, harness = Path(d) / "spec.json", Path(d) / "run.js"
        spec.write_text(json.dumps({"js": js, "json": blobs, "firstKind": first_kind, "actions": list(actions)}))
        harness.write_text(HARNESS)
        done = subprocess.run([NODE, str(harness), str(spec)], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-500:])
    return json.loads(done.stdout)


def sample_page(extra_jobs=()):
    jobs = []
    boards = ["greenhouse", "lever", "ashby", "hackernews"]
    for i, (loc, region) in enumerate([("Remote - Worldwide", "worldwide"), ("Remote, Nigeria", "africa"), ("Remote - EMEA", "emea"),
                                       ("Remote, Germany", "europe"), ("Remote (CET)", "my-timezone"), ("Remote", "unclear"),
                                       ("Accra, Ghana", "nearby")] + list(extra_jobs)):
        j = jr.Job(f"Co{i}", f"Backend Engineer {i}", f"https://x/{i}", loc, True, "2026-11-20", "python", boards[i % len(boards)], str(i))
        j.region, j.region_reason, j.score, j.new = region, "why", 5, i % 2 == 0
        jobs.append(j)
    return jr.render(jobs, [], {"boards": 3, "dead": 1, "fetched": 10}, 30, NOW), jobs


@unittest.skipUnless(NODE, "Node is not installed")
class ReportScriptTests(unittest.TestCase):
    def test_the_script_runs_and_draws_every_role(self):
        page, jobs = sample_page()
        result = run_page(page)
        self.assertIsNone(result["error"])
        self.assertEqual(result["out"].count('class="job"'), len(jobs))

    def test_the_filter_buttons_are_built_from_the_region_table(self):
        page, _ = sample_page()
        result = run_page(page)
        self.assertIsNone(result["error"])
        for key, _label, _style, _button in jr.REGION_META:
            self.assertIn(f'data-r="{key}"', result["buttons"])
        self.assertIn('data-r="all"', result["buttons"])

    def test_every_badge_label_reaches_the_page(self):
        page, jobs = sample_page()
        out = run_page(page)["out"]
        for label in ("Worldwide", "Africa", "EMEA", "Europe", "My time zone", "Check location", "On-site, included"):
            self.assertIn(label, out)

    def test_an_empty_report_still_runs(self):
        page = jr.render([], [], {"boards": 0, "dead": 0}, 30, NOW)
        self.assertIsNone(run_page(page)["error"])

    def test_text_that_looks_like_markup_does_not_break_the_page(self):
        page, _ = sample_page([("Remote </script><b>x</b>", "unclear"), ("Remote <!-- <script>", "unclear")])
        result = run_page(page)
        self.assertIsNone(result["error"])
        self.assertNotIn("<b>x</b>", result["out"])  # shown as text, never as markup

    def test_a_data_block_with_an_id_already_used_by_an_element_is_caught(self):
        """The bug this file exists for: two things with the same id, and getElementById returned the wrong one."""
        page, _ = sample_page()
        broken = page.replace('id="region-meta"', 'id="regions"')
        broken = broken.replace("getElementById('region-meta')", "getElementById('regions')")
        result = run_page(broken)
        self.assertIsNotNone(result["error"])
        self.assertIn("JSON", result["error"])

    # roles by board in sample_page: i % 4 -> greenhouse 0,4  lever 1,5  ashby 2,6  hackernews 3
    def test_a_button_for_every_board_in_the_results_and_none_for_the_others(self):
        page, _ = sample_page()
        result = run_page(page)
        self.assertIsNone(result["error"])
        for key, label in (("greenhouse", "Greenhouse"), ("lever", "Lever"), ("ashby", "Ashby"), ("hackernews", "Hacker News")):
            self.assertIn(f'data-s="{key}"', result["sources"])
            self.assertIn(label, result["sources"])
        for key in ("remotive", "arbeitnow", "jobicy", "himalayas", "remoteok"):
            self.assertNotIn(f'data-s="{key}"', result["sources"])
        self.assertIn('data-s="all"', result["sources"])

    def test_the_board_buttons_count_what_each_board_has(self):
        page, jobs = sample_page()
        buttons = run_page(page)["sourceButtons"]
        self.assertEqual({k: v["n"] for k, v in buttons.items()}, {"all": "7", "greenhouse": "2", "lever": "2", "ashby": "2", "hackernews": "1"})
        self.assertEqual(buttons["all"]["pressed"], "true")

    def test_pressing_a_board_shows_only_its_roles(self):
        page, _ = sample_page()
        result = run_page(page, [{"sel": "#sources", "dataset": {"s": "lever"}}])
        self.assertIsNone(result["error"])
        self.assertEqual(result["out"].count('class="job"'), 2)
        self.assertEqual(result["sourceButtons"]["lever"]["pressed"], "true")
        self.assertEqual(result["sourceButtons"]["all"]["pressed"], "false")
        for title in ("Backend Engineer 1", "Backend Engineer 5"):
            self.assertIn(title, result["out"])
        self.assertNotIn("Backend Engineer 0", result["out"])
        self.assertIn("2</b> of <b>2", result["count"])

    def test_pressing_all_puts_everything_back(self):
        page, jobs = sample_page()
        result = run_page(page, [{"sel": "#sources", "dataset": {"s": "ashby"}}, {"sel": "#sources", "dataset": {"s": "all"}}])
        self.assertEqual(result["out"].count('class="job"'), len(jobs))

    def test_the_board_and_region_filters_combine_and_each_counts_the_other(self):
        page, _ = sample_page()
        # Lever has Africa (i=1) and unclear (i=5). Ticking the Lever board leaves one role per kind it has.
        result = run_page(page, [{"sel": "#sources", "dataset": {"s": "lever"}}])
        regions = result["regionButtons"]
        self.assertEqual(regions["all"]["n"], "2")
        self.assertEqual((regions["africa"]["n"], regions["unclear"]["n"], regions["worldwide"]["n"]), ("1", "1", "0"))
        self.assertTrue(regions["worldwide"]["hidden"])      # nothing worldwide on Lever: no button to press
        self.assertFalse(regions["africa"]["hidden"])
        # now also pick the Africa kind: one role, and the board counts follow the region
        result = run_page(page, [{"sel": "#sources", "dataset": {"s": "lever"}}, {"sel": "#regions", "dataset": {"r": "africa"}}])
        self.assertEqual(result["out"].count('class="job"'), 1)
        self.assertIn("Backend Engineer 1", result["out"])
        self.assertEqual(result["sourceButtons"]["lever"]["n"], "1")
        self.assertEqual(result["sourceButtons"]["greenhouse"]["n"], "0")
        self.assertTrue(result["sourceButtons"]["greenhouse"]["hidden"])

    def test_picking_a_region_updates_the_board_counts(self):
        page, _ = sample_page()
        result = run_page(page, [{"sel": "#regions", "dataset": {"r": "africa"}}])   # one Africa role, on Lever
        self.assertEqual({k: v["n"] for k, v in result["sourceButtons"].items()}, {"all": "1", "greenhouse": "0", "lever": "1", "ashby": "0", "hackernews": "0"})

    def test_a_board_with_no_matches_left_still_shows_if_it_is_the_one_selected(self):
        page, _ = sample_page()
        result = run_page(page, [{"sel": "#sources", "dataset": {"s": "lever"}}, {"sel": "#regions", "dataset": {"r": "worldwide"}}])
        self.assertEqual(result["out"].count('class="job"'), 0)           # Lever has nothing worldwide
        self.assertFalse(result["sourceButtons"]["lever"]["hidden"])      # but its button stays, so it can be undone

    def test_every_card_says_which_board_it_came_from(self):
        page, _ = sample_page()
        out = run_page(page)["out"]
        for label in ("Greenhouse", "Lever", "Ashby", "Hacker News"):
            self.assertIn(f"· {label}", out)

    def test_an_unknown_board_still_works_and_is_shown_under_its_own_name(self):
        j = jr.Job("Co", "Engineer", "https://x", "Remote", True, "2026-11-20", "d", "newboard", "1")
        j.region, j.region_reason, j.score = "worldwide", "r", 1
        page = jr.render([j], [], {"boards": 1, "dead": 0}, 30, NOW)
        result = run_page(page, [{"sel": "#sources", "dataset": {"s": "newboard"}}])
        self.assertIsNone(result["error"])
        self.assertIn("Newboard", result["sources"])
        self.assertEqual(result["out"].count('class="job"'), 1)

    def test_the_data_carries_each_roles_board(self):
        page, _ = sample_page()
        data = json.loads(page.split('id="jobs">')[1].split("</script>")[0].replace("<\\/", "</"))
        self.assertEqual({d["v"] for d in data}, {"greenhouse", "lever", "ashby", "hackernews"})

    def test_no_id_appears_twice_in_the_page(self):
        page, _ = sample_page()
        ids = re.findall(r"\bid=\"([^\"]+)\"", page)
        self.assertEqual(sorted(i for i in set(ids) if ids.count(i) > 1), [])


if __name__ == "__main__":
    unittest.main()
