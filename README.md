# scrape-monitor-gate

A scraper that **reports its own failures**. Zero rows means two completely different things —
the source is empty, or the collection broke — and a scraper that cannot tell them apart will
report "no data" on the day it stopped working.

```
make demo    # replays seven daily snapshots
make test    # 11 tests
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

- It replays **saved snapshots**. There is no fetch layer here: no rate limiting, no retry policy,
  no `robots.txt` handling, no headless browser. Point it at a directory of stored HTML.
- Nothing here evades a block. `SOFT_BLOCK` is a **report** — it tells you collection stopped so a
  person can decide whether to slow down, get an API, or stop. It does not rotate anything.
- Block detection is a short English phrase list. A different provider or language needs its own.
- The `PARTIAL` threshold is a median baseline and a ratio. A source that legitimately halves
  overnight would be flagged; that is the intended direction of the error.
- Extraction is regex over fixed markup, sufficient to demonstrate drift handling and nothing more.

## Layout

```
fixtures/snapshots/   seven days, four planted failure modes
src/monitor.py        selector generations, block detection, baseline, trusted-run diffing
src/cli.py            the daily table and out/report.md
tests/                11 tests - three assert the rewritten baseline rule holds
```
