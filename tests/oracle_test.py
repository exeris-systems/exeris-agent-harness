"""The oracle seam: one place a label enters the harness, and one rule it obeys.

A run record's outcome and a human baseline's are the same question asked about the same work, so
they are asked in one place. What that place answers today is `UNKNOWN`, with the suite behind it
`not-run`, for every domain — no suite has been run as a suite, and an oracle whose pass has never
been contradicted by a known-broken input is unvalidated.

The cases here pin the two properties that have to survive the judge being filled in: a label is
never read apart from the calibration that makes it admissible, and a label the calibration cannot
support does not reach a row.
"""

import contextlib
import io
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

from harness import oracle  # noqa: E402


class OracleTest(unittest.TestCase):
    def test_every_domain_is_judged_unknown_by_a_suite_that_has_not_run(self):
        for domain in oracle.ORACLES:
            with self.subTest(domain=domain):
                judged = oracle.judge("/nowhere", domain)
                self.assertEqual("UNKNOWN", judged["outcome"])
                calibration = judged["calibration"]
                # Naming the suite is how a row says which pass it is waiting for; the result
                # repeats the status because the field holds a score as it was published.
                self.assertEqual(oracle.ORACLES[domain][1], calibration["suite"])
                self.assertEqual("not-run", calibration["status"])
                self.assertEqual("not-run", calibration["result"])

    def test_a_domain_the_register_does_not_carry_is_judged_no_differently(self):
        judged = oracle.judge("/nowhere", "not-a-domain")
        self.assertEqual("UNKNOWN", judged["outcome"])
        self.assertIsNone(judged["calibration"]["suite"])

    def test_a_label_an_uncalibrated_suite_cannot_support_is_not_one(self):
        for claimed in ("TRUE_DONE", "FALSE_DONE"):
            with self.subTest(outcome=claimed):
                self.assertEqual("UNKNOWN", oracle.outcome_of(
                    {"outcome": claimed, "calibration": {"status": "not-run"}}))
        # A run that never got far enough to be judged is that whatever the suite has done: it is a
        # statement about the run, not a label the oracle awarded.
        self.assertEqual("UNREACHABLE", oracle.outcome_of(
            {"outcome": "UNREACHABLE", "calibration": {"status": "not-run"}}))
        # And a suite that has passed is what lets a label through at all.
        self.assertEqual("TRUE_DONE", oracle.outcome_of(
            {"outcome": "TRUE_DONE", "calibration": {"status": "pass"}}))


class OracleOnTheRowTest(support.HarnessFixture):
    def test_the_row_carries_the_judge_s_answer_under_the_fail_closed_rule(self):
        # The judge is stubbed to claim the label its suite cannot support. The row is what the
        # rule is about: it says UNKNOWN, and the calibration beside it says why.
        from harness import oracle as seam

        def claiming(worktree, domain):
            # A score beside a status that is not a pass: the row has to carry the judge's own
            # calibration, and the case below reads it back to prove the judge was consulted.
            return {"outcome": "TRUE_DONE",
                    "calibration": {"suite": seam.suite(domain), "status": "not-run",
                                    "result": "7/8"}}

        original = seam.judge
        seam.judge = claiming
        self.addCleanup(lambda: setattr(seam, "judge", original))

        self.open_run()
        self.commit()
        self.place_session()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]))

        row = self.staged_row()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertEqual({"suite": "oracle-selftest", "status": "not-run", "result": "7/8"},
                         row["oracle"]["calibration"])


if __name__ == "__main__":
    unittest.main()
