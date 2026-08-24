import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import monitor as M

SNAPS = os.path.join(os.path.dirname(__file__), "..", "fixtures", "snapshots")
RUNS = {r.day: r for r in M.run(SNAPS)}

class ItNamesTheFailureInsteadOfSayingNoData(unittest.TestCase):
    """A scraper that reports zero rows on the day it broke is not monitoring anything."""

    def test_markup_change_is_reported_on_the_day_it_changed(self):
        self.assertEqual(RUNS["2026-03-03"].status, M.DRIFT)
        self.assertEqual(RUNS["2026-03-02"].status, M.OK)
        self.assertIn("gen1 -> gen2", RUNS["2026-03-03"].note)

    def test_drift_still_returns_the_data(self):
        """Drift is an alert, not an outage - the fallback selector kept the rows."""
        self.assertEqual(len(RUNS["2026-03-03"].rows), 4)

    def test_soft_block_is_not_reported_as_an_empty_source(self):
        r = RUNS["2026-03-05"]
        self.assertEqual(r.status, M.SOFT_BLOCK)
        self.assertEqual(len(r.rows), 0)
        self.assertNotEqual(r.status, M.EMPTY_SOURCE)

    def test_partial_collection_is_caught_against_a_baseline(self):
        r = RUNS["2026-03-06"]
        self.assertEqual(r.status, M.PARTIAL)
        self.assertIn("baseline of 4", r.note)

    def test_a_good_day_is_not_flagged(self):
        for d in ("2026-03-01", "2026-03-02", "2026-03-04", "2026-03-07"):
            self.assertEqual(RUNS[d].status, M.OK, d)

class ItComparesAgainstTheLastRunItTrusts(unittest.TestCase):
    """The bug this repo was rewritten to fix: diffing against a broken day both
    manufactures changes that never happened and hides the ones that did."""

    def test_the_real_price_move_survives_two_broken_days(self):
        moves = [c for c in RUNS["2026-03-07"].changes if c[0] == "Cyan"]
        self.assertEqual(len(moves), 1)
        k, before, after, ratio = moves[0]
        self.assertEqual(before, "75.50")
        self.assertEqual(after, "755.00")
        self.assertAlmostEqual(ratio, 10.0, places=1)

    def test_the_partial_day_does_not_become_a_baseline(self):
        """3/6 had one row. If it were the comparison point, 3/7 would look like
        three new items appearing rather than one price moving."""
        appeared = [c for c in RUNS["2026-03-07"].changes if c[0] in ("Bravo", "Delta")]
        self.assertFalse(appeared)

    def test_no_phantom_changes_on_a_quiet_day(self):
        self.assertEqual(RUNS["2026-03-04"].changes, [])

class ItSeparatesEmptyFromBroken(unittest.TestCase):
    def test_zero_rows_with_a_baseline_is_a_failure_not_an_empty_source(self):
        self.assertEqual(RUNS["2026-03-05"].status, M.SOFT_BLOCK)

    def test_first_ever_run_with_no_rows_would_admit_it_cannot_tell(self):
        """With no history there is genuinely no way to distinguish the two, and the
        verdict says so rather than guessing."""
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as td:
            with open(_os.path.join(td, "2026-01-01.html"), "w") as fh:
                fh.write("<html><body><ul id='items'></ul></body></html>")
            r = M.run(td)[0]
        self.assertEqual(r.status, M.EMPTY_SOURCE)
        self.assertIn("cannot tell", r.note)

    def test_every_non_ok_day_carries_a_reason(self):
        for r in M.run(SNAPS):
            if r.status != M.OK:
                self.assertTrue(r.note.strip(), f"{r.day} flagged without a reason")

if __name__ == "__main__":
    unittest.main()
