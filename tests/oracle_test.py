"""The oracle seam: one place a label enters the harness, and one rule it obeys.

A run record's outcome and a human baseline's are the same question asked about the same work, so
they are asked in one place. For a documentation run that place imports the oracle the execution
repository publishes and reads the calibration that oracle's own suite published beside it; for a
construction run it still answers `UNKNOWN` at `not-run`, because that suite has not been run as a
suite.

The cases here pin the properties that have to hold however the oracle behind the seam changes:

* a label is never read apart from the calibration that makes it admissible, and a label the
  calibration cannot support does not reach a row — whatever the gates found;
* the oracle is the published one, asked about the tree the run produced and the registry the
  configuration names, and never a copy living here;
* the gates stay with the run as its own evidence and never become columns on the row;
* the human arm is judged by all of the above, identically, because outcomes measured by two
  instruments compare the instruments.
"""

import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

from harness import oracle  # noqa: E402

GROUP = "G-0001"

#: The version the stub oracle reports as the rules it applied. Deliberately not the pin the
#: fixture checkout carries, so that a case can tell the two fields apart: one says what the work
#: was subject to, the other says what judged it.
JUDGED_UNDER = "9.9.9"

#: What the stub answers with, in the shape the published oracle's `as_dict()` has.
GATES = [
    {"check": "frontmatter_check", "result": "pass", "detail": "placeholder: what it checked"},
    {"check": "registry_check", "result": "pass", "detail": "placeholder: what it checked"},
]

STUB = '''\
"""A stand-in for the published oracle, answering what the case wrote beside it.

It stubs the seam and not the gates. These cases are about what the harness does with a judgement
and with the calibration beside it; an oracle that ran the real checkers would make that answer
depend on a corpus, which is the calibration suite's question and not this file's.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


class _Answer:
    def __init__(self, found):
        self._found = found

    def as_dict(self):
        return dict(self._found)


def judge(checkout, *, guardrails=None, agents_tools=None, index=None):
    with open(os.path.join(_HERE, "asked.json"), "w", encoding="utf-8") as handle:
        json.dump({"checkout": checkout, "index": index}, handle)
    with open(os.path.join(_HERE, "answer.json"), encoding="utf-8") as handle:
        return _Answer(json.load(handle))
'''

RUN_AT = "2026-09-22T21:57:38Z"


def _selftest(status="pass", result="8/8", suite="docs-mutation-v1"):
    return {"suite": suite, "status": status, "result": result, "run_at": RUN_AT}


class OracleTest(unittest.TestCase):
    """The seam on its own, with no execution repository behind it."""

    def test_every_domain_is_judged_unknown_where_no_oracle_can_be_reached(self):
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

    def _published(self, document):
        root = pathlib.Path(tempfile.mkdtemp(prefix="exeris-calibration-test-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        path = root.joinpath(*oracle.DOCS_SELFTEST)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(document), encoding="utf-8")
        return oracle.calibration(oracle.DOCS_DOMAIN, str(root))

    def test_a_result_published_by_another_suite_is_not_this_oracle_s_calibration(self):
        # A pass is the pass of a named suite. Reading another suite's would admit labels on the
        # strength of a run that was never about these gates.
        state, reason = self._published(_selftest(suite="some-other-suite"))
        self.assertEqual("not-run", state["status"])
        self.assertIn("some-other-suite", reason)

    def test_a_calibration_without_a_status_and_a_score_is_not_one(self):
        for document in ({"suite": "docs-mutation-v1", "status": "green", "result": "8/8"},
                         {"suite": "docs-mutation-v1", "status": "pass"},
                         {"suite": "docs-mutation-v1", "status": "pass", "result": ""}):
            with self.subTest(document=document):
                state, reason = self._published(document)
                self.assertEqual({"suite": "docs-mutation-v1", "status": "not-run",
                                  "result": "not-run"}, state)
                self.assertTrue(reason)


class DocumentationOracleTest(support.HarnessFixture):
    """A documentation run, judged by the oracle the configured execution repository publishes."""

    def setUp(self):
        super().setUp()
        self.configure_domain(support.DOCUMENTATION_DOMAIN)
        self.oracles = pathlib.Path(self.execution) / "oracles"
        (self.oracles / "docs-guardrails").mkdir(parents=True)
        (self.oracles / "__init__.py").write_text("", encoding="utf-8")
        (self.oracles / "docs_guardrails.py").write_text(STUB, encoding="utf-8")
        self.publish()
        # The package is loaded out of a directory this case owns, so it is taken back out of the
        # interpreter when the case ends: a module left behind would answer for the next case from
        # a path that no longer exists.
        self.addCleanup(self._forget)

    def _forget(self):
        root = str(pathlib.Path(self.execution).resolve())
        for name in [n for n in list(sys.modules)
                     if n == oracle.DOCS_PACKAGE or n.startswith(oracle.DOCS_PACKAGE + ".")]:
            del sys.modules[name]
        while root in sys.path:
            sys.path.remove(root)

    # ---- what the execution repository publishes ---------------------------------

    def publish(self, *, outcome="TRUE_DONE", gates=None, **selftest):
        """The oracle's answer and the state its suite published, as this case wants them."""
        (self.oracles / "answer.json").write_text(json.dumps({
            "oracle_id": "docs-guardrails", "oracle_version": JUDGED_UNDER,
            "gates": GATES if gates is None else gates, "outcome": outcome,
        }), encoding="utf-8")
        self.calibrate(**selftest)

    def calibrate(self, **selftest):
        (self.oracles / "docs-guardrails" / "oracle-selftest.json").write_text(
            json.dumps(_selftest(**selftest)), encoding="utf-8")

    def asked(self):
        return json.loads((self.oracles / "asked.json").read_text(encoding="utf-8"))

    # ---- the run -----------------------------------------------------------------

    def close(self):
        self.open_run()
        self.commit()
        self.place_session()
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed), contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["close-run", "--run", self.run_id()])
        self.said = noticed.getvalue()
        self.assertEqual(0, code, self.said)
        return self.staged_row()

    def judgement(self, kind="model"):
        return json.loads((self.run_dir(kind) / "staging" / "judgement.json")
                          .read_text(encoding="utf-8"))

    def baseline(self):
        """A human arm on this machine, opened, worked in and closed."""
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.cli(["baseline", "--repo", str(self.clone), "--task",
                                          support.TASK, "--group", GROUP, "--scope",
                                          support.SCOPE]), noticed.getvalue())
            tree = self.worktree("baseline")
            (tree / "a-change.md").write_text("a line the person wrote\n")
            for argv in (["add", "a-change.md"], ["commit", "-m", "docs: the person's change"]):
                self.git("-C", str(tree), *argv)
            self.assertEqual(0, self.cli(["baseline", "--close", "--run",
                                          self.run_id("baseline")]), noticed.getvalue())
        self.said = noticed.getvalue()
        return json.loads((self.run_dir("baseline") / "staging" / "baseline.json")
                          .read_text(encoding="utf-8"))

    # ---- what reaches the row ----------------------------------------------------

    def test_a_calibrated_pass_is_the_row_s_outcome_at_the_version_that_judged_it(self):
        row = self.close()
        self.assertEqual("TRUE_DONE", row["outcome"])
        self.assertEqual({"id": "docs-guardrails", "version": JUDGED_UNDER,
                          "calibration": {"suite": "docs-mutation-v1", "status": "pass",
                                          "result": "8/8", "run_at": RUN_AT}}, row["oracle"])
        # The pin the checkout carries is the other fact and keeps its own field: one says what the
        # work was subject to, the other says what judged it.
        self.assertEqual(support.BUNDLE_VERSION, row["repository_state"]["bundle_version"])

    def _admits_nothing(self, status, claimed):
        self.publish(outcome=claimed, status=status, result="7/8")
        row = self.close()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertEqual(status, row["oracle"]["calibration"]["status"])
        # What the oracle found is not lost — it is kept where a person can read it, beside the
        # run, and the row says only what it may.
        self.assertEqual(claimed, self.judgement()["outcome"])
        self.assertEqual("UNKNOWN", self.judgement()["admitted"])

    def test_a_suite_at_fail_admits_nothing_the_oracle_said(self):
        self._admits_nothing("fail", "TRUE_DONE")

    def test_a_suite_that_has_not_run_admits_nothing_the_oracle_said(self):
        self._admits_nothing("not-run", "FALSE_DONE")

    def test_a_calibration_that_is_not_on_disk_is_unknown_and_says_why(self):
        (self.oracles / "docs-guardrails" / "oracle-selftest.json").unlink()
        row = self.close()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertEqual({"suite": "docs-mutation-v1", "status": "not-run", "result": "not-run"},
                         row["oracle"]["calibration"])
        # The reason is printed rather than swallowed: `UNKNOWN` because no calibration was there
        # and `UNKNOWN` because the gates found nothing to judge are the same word on the row, and
        # the difference is a person's to act on.
        self.assertIn("oracle-selftest.json", self.said)

    def test_an_execution_repository_with_no_oracle_judges_nothing(self):
        # The harness carries no copy to fall back on, and that is the point: a copy is a second
        # implementation of the gates, free to drift from the one the suite was run against.
        for name in ("docs_guardrails.py", "__init__.py"):
            (self.oracles / name).unlink()
        row = self.close()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertEqual([], self.judgement()["gates"])
        self.assertIn("no oracle under", self.said)

    def test_an_oracle_that_reaches_no_verdict_leaves_the_run_unjudged(self):
        (self.oracles / "answer.json").unlink()
        row = self.close()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertIn("no verdict", self.judgement()["reason"])

    # ---- what the oracle is asked ------------------------------------------------

    def test_the_oracle_is_asked_about_the_tree_the_run_produced_and_the_named_registry(self):
        index = pathlib.Path(self.tmp) / "exeris-docs" / "adr-index.md"
        index.parent.mkdir()
        index.write_text("| ADR | placeholder |\n", encoding="utf-8")
        self.config_path.write_text(
            self.config_path.read_text() + f'\n[oracle]\ndocs_index = "{index}"\n')

        self.close()
        asked = self.asked()
        self.assertEqual(str(self.worktree()), asked["checkout"])
        self.assertEqual(str(index), asked["index"])

    def test_a_configuration_naming_no_registry_asks_for_none(self):
        self.close()
        self.assertIsNone(self.asked()["index"])

    # ---- where the gates go ------------------------------------------------------

    def test_the_gates_are_the_run_s_own_evidence_and_never_the_row_s_columns(self):
        row = self.close()
        self.assertEqual(sorted(("id", "version", "calibration")), sorted(row["oracle"]))
        self.assertNotIn("registry_check", json.dumps(row))

        judged = self.judgement()
        self.assertEqual(GATES, judged["gates"])
        self.assertEqual(self.run_id(), judged["run_id"])
        self.assertEqual(support.DOCUMENTATION_DOMAIN, judged["domain"])
        self.assertEqual("docs-guardrails", judged["oracle_id"])
        self.assertEqual(JUDGED_UNDER, judged["oracle_version"])
        self.assertEqual("TRUE_DONE", judged["admitted"])

    # ---- the human arm -----------------------------------------------------------

    def test_the_human_arm_is_judged_by_the_same_oracle_over_its_own_tree(self):
        staged = self.baseline()
        self.assertEqual("TRUE_DONE", staged["outcome"])
        self.assertEqual(str(self.worktree("baseline")), self.asked()["checkout"])
        self.assertEqual(GATES, self.judgement("baseline")["gates"])

    def test_the_human_arm_carries_no_label_its_suite_cannot_support(self):
        self.calibrate(status="fail", result="7/8")
        self.assertEqual("UNKNOWN", self.baseline()["outcome"])


class OracleOnTheRowTest(support.HarnessFixture):
    def test_the_row_carries_the_judge_s_answer_under_the_fail_closed_rule(self):
        # The judge is stubbed to claim the label its suite cannot support. The row is what the
        # rule is about: it says UNKNOWN, and the calibration beside it says why.
        from harness import oracle as seam

        def claiming(worktree, domain, **asked):
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
