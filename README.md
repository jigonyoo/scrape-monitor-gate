# scrape-monitor-gate

A scraper that **reports its own failures**. Zero rows means two completely different things —
the source is empty, or the collection broke — and a scraper that cannot tell them apart will
report "no data" on the day it stopped working.

```
make demo    # replays seven daily snapshots
make test    # 50 tests, no network
```

## The measured run

Seven consecutive daily snapshots of one listing page.

| day | rows | status | note |
|---|---:|---|---|
| 2026-03-01 | 4 | `OK` | |
| 2026-03-02 | 4 | `OK` | |
| 2026-03-03 | 4 | **`SCHEMA_DRIFT`** | markup changed: selector moved gen1 → gen2 |
| 2026-03-04 | 4 | `OK` | |
| 2026-03-05 | 0 | **`SOFT_BLOCK`** | page returned content, but it is an interstitial |
| 2026-03-06 | 1 | **`PARTIAL`** | 1 row against a baseline of 4 |
| 2026-03-07 | 4 | `OK` | — and a **10x price move** on one item |

A naive scraper reports "no data" on **one** of those days and says nothing at all about the
other three. This one raises a **specific, actionable failure on three** and still delivers the
rows on the drift day, because the fallback selector caught them.

## The four failures, and why they are not one failure

**SCHEMA_DRIFT** — the site renamed its classes. The old selector returns nothing. Because every
selector generation is kept and tried, the rows still arrive *and* an alert fires naming the
change. Drift is a warning, not an outage. Note the direction: drift is measured against **what
worked last time**, not against a favourite selector.

**SOFT_BLOCK** — HTTP 200, real HTML, no listing. An interstitial. A status-code check passes here
and a row-count check reports an empty source. Neither is true.

**PARTIAL** — pagination broke and one row came back instead of four. This is the quietest failure
of the four: the data looks valid, it is just most of it is missing.

**A real change** — Cyan moved 75.50 → 755.00 on a structurally sound page. That is the only one
of the four that should reach a human as *business* news rather than an engineering alert.

## The bug this was rewritten to fix

The first version compared each day against the previous day. That is wrong in a way that is easy
to miss: **3/6 returned one row, so diffing 3/7 against it made three items look like they had
just appeared, and buried the one price that actually moved.**

The fix is one line of policy — compare against the last run whose status was trustworthy — and
two tests hold it in place:

```
test_the_real_price_move_survives_two_broken_days
test_the_partial_day_does_not_become_a_baseline
```

A broken day must never become the baseline. Diffing against one manufactures changes that never
happened and hides the ones that did.

## Honest limits

- `make demo` replays **saved snapshots**. The Actor (below) has a standard-library fetch layer
  with `robots.txt`, per-domain spacing, retries and timeouts, but **no headless browser**: pages
  that only render with JavaScript will look empty or blocked. The fetch layer is tested only
  against a fake transport; it has not been run against a live site from this repo.
- Nothing here evades a block. `SOFT_BLOCK` is a **report** — it tells you collection stopped so a
  person can decide whether to slow down, get an API, or stop. It does not rotate anything.
- Block detection is a short English phrase list. A different provider or language needs its own.
- The `PARTIAL` threshold is a median baseline and a ratio. A source that legitimately halves
  overnight would be flagged; that is the intended direction of the error.
- Extraction is regex over fixed markup, sufficient to demonstrate drift handling and nothing more.

## Apify Store

The same rules packaged as an Apify Actor (`.actor/`, `Dockerfile`, `src/actor.py`). Each run
checks every URL once and writes one dataset item per URL. The per-URL baseline is stored in the
default key-value store under `MONITOR_STATE`, so on a **scheduled** Actor the "broken day never
becomes the baseline" rule holds across runs exactly as it does across the replayed snapshots:
only `OK` and `SCHEMA_DRIFT` runs become the comparison point.

Locally, `make actor` reads `storage/key_value_stores/default/INPUT.json` and writes
`storage/datasets/default/000000001.json`, ... (the Apify local storage layout). On the platform
(`APIFY_IS_AT_HOME=1`) the same calls go through the Apify SDK, which is imported only there.
Note that `make actor` makes real requests to the URLs in your input.

**Input example**

```json
{
  "startUrls": [{"url": "https://shop.example/listing"}],
  "selectors": [
    {"name": "gen2", "row": "<li class=\"listing\">(.*?)</li>",
     "key": "<span class=\"title\">(.*?)</span>", "value": "<span class=\"amount\">(.*?)</span>"},
    {"name": "gen1", "row": "<li class=\"row\">(.*?)</li>",
     "key": "<span class=\"name\">(.*?)</span>", "value": "<span class=\"price\">(.*?)</span>"}
  ],
  "maxPages": 50,
  "requestIntervalMillis": 2000,
  "requestTimeoutSecs": 30,
  "userAgent": "scrape-monitor-gate/0.1"
}
```

Selectors are regular expressions (first group = the match), tried in order. `key` identifies an
item between runs, `value` is what is watched. Keep old generations in the list — that is what
lets a markup change arrive as `SCHEMA_DRIFT` with the rows still delivered.

**Output example** — the 2026-03-07 fixture served as the seventh scheduled run (from the test
suite, not a live site):

```json
{
  "url": "https://shop.example/listing",
  "checkedAt": "2026-03-07T06:00:00+00:00",
  "checked": true,
  "status": "OK",
  "rowCount": 4,
  "selector": "gen2",
  "note": "",
  "changes": [{"key": "Cyan", "before": "75.50", "after": "755.00", "ratio": 10.0}],
  "rows": [{"key": "Alpha", "value": "100.00"}, {"key": "Bravo", "value": "205.00"},
           {"key": "Cyan", "value": "755.00"}, {"key": "Delta", "value": "318.00"}],
  "httpStatus": 200,
  "attempts": 1
}
```

`status` is one of `OK`, `SCHEMA_DRIFT`, `SOFT_BLOCK`, `PARTIAL`, `EMPTY_SOURCE` for a page that
was received, or `FETCH_FAILED` / `ROBOTS_DISALLOWED` (`checked: false`) for one that was not. A
page that was never received is reported but does not touch the baseline. If `robots.txt` cannot
be read at all (network error or 5xx), the page is treated as disallowed.

**Pay-per-event charges** (prices are set in Apify Console and are not decided here):

| event | charged when |
|---|---|
| `page-checked` | once per URL that was fetched and classified, whatever its status |
| `anomaly-detected` | additionally, once per checked URL whose status is `SCHEMA_DRIFT`, `SOFT_BLOCK` or `PARTIAL` |

`FETCH_FAILED` and `ROBOTS_DISALLOWED` are not charged. `maxPages` caps how many URLs one run
checks, and so caps the `page-checked` charges of a run.

**You are responsible** for complying with the terms of service and `robots.txt` of every site you
point this Actor at, and for having the right to collect what you collect. The Actor honours
`robots.txt` and spaces its requests, but that does not make a given use permitted.

The `Dockerfile` names the Apify Python base image (`apify/actor-python:3.13`); it has not been
built from this repo.

## Layout

```
fixtures/snapshots/   seven days, four planted failure modes
src/monitor.py        selector generations, block detection, baseline, trusted-run diffing
src/cli.py            the daily table and out/report.md
src/actor.py          Apify Actor entry point: input, one item per URL, persisted baseline
src/transport.py      Transport interface, urllib transport, fake transport, polite Fetcher
src/storage.py        local Apify storage layout / Apify SDK adapter (lazy import)
src/billing.py        pay-per-event names
.actor/               actor.json, input_schema.json
tests/                50 tests - the baseline rule is asserted on the replay and across Actor runs
```
