import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import monitor as M

def main():
    snaps = os.environ.get("SNAPS", "fixtures/snapshots")
    out = os.environ.get("OUT", "out")
    os.makedirs(out, exist_ok=True)
    runs = M.run(snaps)
    print(f"{len(runs)} daily snapshots\n")
    print(f"{'day':<12}{'rows':>5}  {'status':<14}{'selector':<8}note")
    for r in runs:
        print(f"{r.day:<12}{len(r.rows):>5}  {r.status:<14}{(r.selector or '-'):<8}{r.note}")
    bad = [r for r in runs if r.status != M.OK]
    print(f"\ndays a naive scraper would have reported as 'no data': "
          f"{sum(1 for r in runs if not r.rows)}")
    print(f"days this one raises a specific failure:              "
          f"{sum(1 for r in runs if r.status in (M.SOFT_BLOCK, M.PARTIAL, M.DRIFT))}")
    moves = [(r.day, c) for r in runs for c in r.changes if c[3] and (c[3] >= 2 or c[3] <= 0.5)]
    print(f"\nlarge value moves on a structurally sound page:")
    for day, (k, a, b, ratio) in moves:
        print(f"  {day}  {k}: {a} -> {b}  ({ratio:.1f}x)")

    L = ["# Monitoring report", "", "| day | rows | status | selector | note |", "|---|---:|---|---|---|"]
    for r in runs:
        L.append(f"| {r.day} | {len(r.rows)} | `{r.status}` | {r.selector or '-'} | {r.note} |")
    L += ["", "## Alerts", ""]
    for r in bad:
        L.append(f"- **{r.day} — {r.status}**: {r.note}")
    for day, (k, a, b, ratio) in moves:
        L.append(f"- **{day} — value move**: {k} {a} to {b} ({ratio:.1f}x), page structure was sound")
    with open(f"{out}/report.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"\nwrote {out}/report.md")

if __name__ == "__main__":
    main()
