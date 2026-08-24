"""A scraper that reports its own failures.

Zero rows means two completely different things - the source is empty, or the collection
broke - and a scraper that cannot tell them apart will report 'no data' on the day it
stopped working. Everything here exists to keep those two apart."""
import os, re, statistics
from dataclasses import dataclass, field

# Every selector generation we have ever seen work. Order does not imply "correct" -
# drift is measured against what worked *last time*, not against a favourite.
SELECTORS = [
    {"name": "gen2", "row": r'<li class="listing">(.*?)</li>',
     "f1": r'<span class="title">(.*?)</span>', "f2": r'<span class="amount">(.*?)</span>'},
    {"name": "gen1", "row": r'<li class="row">(.*?)</li>',
     "f1": r'<span class="name">(.*?)</span>', "f2": r'<span class="price">(.*?)</span>'},
]
BLOCK_MARKERS = ("checking your browser", "verify you are human", "access denied",
                 "unusual traffic", "enable javascript to continue")

OK, DRIFT, SOFT_BLOCK, PARTIAL, EMPTY_SOURCE = "OK", "SCHEMA_DRIFT", "SOFT_BLOCK", "PARTIAL", "EMPTY_SOURCE"

@dataclass
class Run:
    day: str
    status: str
    rows: list
    selector: str
    note: str
    changes: list = field(default_factory=list)

def extract(html, sel):
    out = []
    for chunk in re.findall(sel["row"], html, re.S):
        a = re.search(sel["f1"], chunk, re.S)
        b = re.search(sel["f2"], chunk, re.S)
        if a and b:
            out.append((a.group(1).strip(), b.group(1).strip()))
    return out

def parse(html):
    """Try every selector generation. Report which one worked - that is the drift signal."""
    for sel in SELECTORS:
        rows = extract(html, sel)
        if rows:
            return rows, sel["name"]
    return [], ""

def looks_blocked(html):
    low = re.sub(r"<[^>]+>", " ", html).lower()
    return any(m in low for m in BLOCK_MARKERS)

def diff(prev, cur):
    p, c = dict(prev), dict(cur)
    out = []
    for k in sorted(set(p) & set(c)):
        if p[k] != c[k]:
            try:
                a, b = float(p[k]), float(c[k])
                ratio = b / a if a else float("inf")
            except ValueError:
                ratio = None
            out.append((k, p[k], c[k], ratio))
    return out

def run(snapshot_dir, drop_ratio=0.5):
    days = sorted(f[:-5] for f in os.listdir(snapshot_dir) if f.endswith(".html"))
    history, runs = [], []
    last_good, last_selector = None, None
    for d in days:
        with open(os.path.join(snapshot_dir, f"{d}.html"), encoding="utf-8") as fh:
            html = fh.read()
        rows, sel = parse(html)
        baseline = int(statistics.median(history)) if history else None

        if not rows and looks_blocked(html):
            st, note = SOFT_BLOCK, "page returned content but it is an interstitial, not the listing"
        elif not rows and baseline:
            st, note = SOFT_BLOCK, "no rows and no known selector matched; collection failure, not an empty source"
        elif not rows:
            st, note = EMPTY_SOURCE, "no rows and no baseline yet - cannot tell empty from broken"
        elif baseline and len(rows) < baseline * drop_ratio:
            st, note = PARTIAL, f"{len(rows)} rows against a baseline of {baseline}"
        elif last_selector and sel != last_selector:
            st, note = DRIFT, f"markup changed: selector moved {last_selector} -> {sel}"
        else:
            st, note = OK, ""

        # Compare against the last run we trust. Diffing against a broken day
        # manufactures changes that never happened, and hides the ones that did.
        changes = diff(last_good.rows, rows) if last_good and rows else []
        runs.append(Run(d, st, rows, sel, note, changes))
        if rows:
            history.append(len(rows))
            last_selector = sel
        if st in (OK, DRIFT):
            last_good = runs[-1]
    return runs
