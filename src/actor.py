"""Apify Actor entry point. Each run checks the given URLs once; the per-URL baseline is kept
in the key-value store between runs, so "a broken day never becomes the baseline" holds across
scheduled runs exactly as it does across the replayed snapshots."""
import math, os, re, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(__file__))
import monitor as M
import billing
from storage import open_storage
from transport import Fetcher, UrllibTransport

STATE_KEY = "MONITOR_STATE"
FETCH_FAILED, ROBOTS_DISALLOWED = "FETCH_FAILED", "ROBOTS_DISALLOWED"
DEFAULTS = {"maxPages": 50, "requestIntervalMillis": 2000, "requestTimeoutSecs": 30,
            "userAgent": "scrape-monitor-gate/0.1"}

class InputError(ValueError):
    pass

def parse_input(raw):
    raw = dict(raw or {})
    urls, seen = [], set()
    for u in raw.get("startUrls") or []:
        u = (u.get("url") if isinstance(u, dict) else u) or ""
        u = u.strip()
        if not re.match(r"https?://", u):
            raise InputError(f"not an http(s) URL: {u!r}")
        if u not in seen:
            seen.add(u)
            urls.append(u)
    if not urls:
        raise InputError("startUrls is empty")

    selectors = M.SELECTORS
    if raw.get("selectors"):
        selectors = []
        for i, s in enumerate(raw["selectors"]):
            missing = [k for k in ("row", "key", "value") if not s.get(k)]
            if missing:
                raise InputError(f"selectors[{i}] is missing {', '.join(missing)}")
            for k in ("row", "key", "value"):
                try:
                    re.compile(s[k])
                except re.error as e:
                    raise InputError(f"selectors[{i}].{k} is not a valid regex: {e}") from e
            selectors.append({"name": s.get("name") or f"gen{i + 1}",
                              "row": s["row"], "f1": s["key"], "f2": s["value"]})

    cfg = {k: raw.get(k, v) if raw.get(k) is not None else v for k, v in DEFAULTS.items()}
    for k in ("maxPages", "requestIntervalMillis", "requestTimeoutSecs"):
        if not isinstance(cfg[k], (int, float)) or cfg[k] < (1 if k != "requestIntervalMillis" else 0):
            raise InputError(f"{k} must be a positive number")
    cfg.update(urls=urls[:int(cfg["maxPages"])], skipped=urls[int(cfg["maxPages"]):],
               selectors=selectors)
    return cfg

def _ratio(r):
    return r if r is not None and math.isfinite(r) else None

def check(url, fetcher, state, now, selectors):
    """One URL -> one dataset item. Only a page we actually received touches the baseline."""
    item = {"url": url, "checkedAt": now, "checked": False, "status": None, "rowCount": 0,
            "selector": None, "note": "", "changes": [], "rows": [],
            "httpStatus": None, "attempts": 0}
    got = fetcher.fetch(url)
    item["attempts"] = got.attempts
    item["httpStatus"] = got.response.status if got.response else None
    if got.robots_disallowed:
        item.update(status=ROBOTS_DISALLOWED, note=got.error)
        return item
    if not got.ok:
        item.update(status=FETCH_FAILED, note=got.error or "no response")
        return item
    r = M.step(state, now, got.response.text, selectors=selectors)
    item.update(checked=True, status=r.status, rowCount=len(r.rows), selector=r.selector or None,
                note=r.note, rows=[{"key": k, "value": v} for k, v in r.rows],
                changes=[{"key": k, "before": a, "after": b, "ratio": _ratio(x)}
                         for k, a, b, x in r.changes])
    return item

def run_actor(storage, transport=None, clock=None, sleep=None, now=None, log=print):
    storage.open()
    try:
        cfg = parse_input(storage.get_input())
    except InputError as e:
        storage.fail(f"input error: {e}")
        raise
    try:
        kw = {k: v for k, v in (("clock", clock), ("sleep", sleep)) if v is not None}
        fetcher = Fetcher(transport or UrllibTransport(), cfg["userAgent"],
                          interval=cfg["requestIntervalMillis"] / 1000.0,
                          timeout=float(cfg["requestTimeoutSecs"]), **kw)
        if cfg["skipped"]:
            log(f"maxPages={cfg['maxPages']}: not checking {len(cfg['skipped'])} more URL(s)")
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        states = storage.get_value(STATE_KEY, {}) or {}
        items = []
        for url in cfg["urls"]:
            state = M.Baseline.from_dict(states.get(url))
            item = check(url, fetcher, state, now, cfg["selectors"])
            storage.push_data(item)
            if item["checked"]:
                states[url] = state.to_dict()
                storage.set_value(STATE_KEY, states)
            for ev in billing.events_for(item):
                storage.charge(ev, 1)
            log(f"{item['status']:<18}{item['rowCount']:>5}  {url}  {item['note']}")
            items.append(item)
        return items
    finally:
        storage.close()

def main():
    try:
        run_actor(open_storage())
    except InputError as e:
        print(f"input error: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
