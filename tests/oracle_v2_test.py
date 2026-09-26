"""The second generation of the documentation oracle: what it is asked, and under which calibration.

The second generation judges a checkout against the commit the run started from as well as the
tree, for the files a task says must keep their bodies, and reads the registry through the pinned
MCP server. The cases pin what has to hold however that oracle changes:

* *The task says what is preserved.* A registered run reads `oracle_inputs.preserve` from its task
  record when it opens, records it with its digest and the record's own, and the oracle is asked
  with those patterns and the run's base commit. An adhoc run has no task record and asks for none.
  A configured registry that cannot answer for a registered task refuses the run.
* *The calibration decides the generation.* The second suite's file wins where it is published and
  the first one's is the fallback only while it is not; under the first one's the second
  generation's arguments are not passed, and a second-generation calibration over a `judge` that
  cannot take them judges nothing.
* *The server is pinned.* Its version and its commit are recorded when the run opens.
* *Each generation is its own fence.* A row judged by the second one sits on a `-v2` producer.

The oracle is a stub that records what it was asked, written into the execution clone the fixture
builds, in the way `oracle_test` does it.
"""

import contextlib
import hashlib
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

from harness import oracle, record, registry  # noqa: E402
from harness.capture import NoRow  # noqa: E402

GATES = [{"check": "preserve_check", "result": "pass", "detail": "placeholder: what it checked"}]

#: The version of the server the stub oracle reports reading through.
REPORTED_BRIDGE = {"version": support.BRIDGE_VERSION, "commit": "0" * 40}

#: A stub with the second generation's signature. It writes down every keyword it was given, so a
#: case can tell an argument passed as empty from an argument not passed at all.
STUB_V2 = '''\
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


class _Answer:
    def __init__(self, found):
        self._found = found

    def as_dict(self):
        return dict(self._found)


def judge(checkout, *, guardrails=None, agents_tools=None, index=None, base=None, preserve=(),
          bridge=None, **unexpected):
    asked = {"checkout": checkout, "index": index}
    for name, value in (("base", base), ("preserve", preserve), ("bridge", bridge)):
        if value is not None and value != ():
            asked[name] = list(value) if isinstance(value, tuple) else value
    asked["unexpected"] = sorted(unexpected)
    with open(os.path.join(_HERE, "asked.json"), "w", encoding="utf-8") as handle:
        json.dump(asked, handle)
    with open(os.path.join(_HERE, "answer.json"), encoding="utf-8") as handle:
        return _Answer(json.load(handle))
'''

#: A stub with the first generation's signature, which takes none of the second one's arguments.
STUB_V1 = STUB_V2.replace(
    "index=None, base=None, preserve=(),\n          bridge=None, **unexpected):",
    "index=None):\n    base, preserve, bridge, unexpected = None, (), None, {}")


def _selftest(suite, status="pass", result="8/8"):
    return {"suite": suite, "status": status, "result": result}


V1_SUITE = "docs-mutation-v1"


class SecondGenerationFixture(support.HarnessFixture):
    """A documentation run whose execution clone publishes an oracle and its calibrations."""

    def setUp(self):
        super().setUp()
        self.configure_domain(support.DOCUMENTATION_DOMAIN)
        self.oracles = pathlib.Path(self.execution) / "oracles"
        (self.oracles / "docs-guardrails").mkdir(parents=True)
        (self.oracles / "__init__.py").write_text("", encoding="utf-8")
        self.stub(STUB_V2)
        (self.oracles / "answer.json").write_text(json.dumps({
            "oracle_id": "docs-guardrails", "oracle_version": "9.9.9", "gates": GATES,
            "outcome": "TRUE_DONE", "instrument": {"bridge": REPORTED_BRIDGE},
        }), encoding="utf-8")
        self.calibrate(oracle.DOCS_SUITE_V2)
        self.addCleanup(self._forget)
        self.register_fence(support.FENCE.replace("harness-claude-", "harness-claude-v2-"))

    def _forget(self):
        root = str(pathlib.Path(self.execution).resolve())
        for name in [n for n in list(sys.modules)
                     if n == oracle.DOCS_PACKAGE or n.startswith(oracle.DOCS_PACKAGE + ".")]:
            del sys.modules[name]
        while root in sys.path:
            sys.path.remove(root)

    def stub(self, text):
        (self.oracles / "docs_guardrails.py").write_text(text, encoding="utf-8")

    def calibrate(self, suite, **selftest):
        name = oracle.DOCS_SELFTEST_V2 if suite == oracle.DOCS_SUITE_V2 else oracle.DOCS_SELFTEST
        pathlib.Path(self.execution).joinpath(*name).write_text(
            json.dumps(_selftest(suite, **selftest)), encoding="utf-8")

    def asked(self):
        return json.loads((self.oracles / "asked.json").read_text(encoding="utf-8"))

    def close(self, *open_extra, task=support.TASK):
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "claude",
                             "--task", task, "--scope", support.SCOPE, *open_extra])
        self.assertEqual(0, code)
        self.commit()
        self.place_session()
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed), contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["close-run", "--run", self.run_id()])
        self.said = noticed.getvalue()
        self.assertEqual(0, code, self.said)

    def judgement(self):
        return json.loads((self.run_dir() / "staging" / "judgement.json").read_text())


class OracleInputsTest(SecondGenerationFixture):
    """What the task tells the oracle, from the registry to the call."""

    def test_preserve_is_read_from_the_task_and_passed_with_the_run_s_base(self):
        task_file = self.build_registry()
        self.close()
        manifest = self.manifest()
        self.assertEqual({"task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
                          "preserve": list(support.PRESERVE),
                          "preserve_sha256": registry.digest(support.PRESERVE)},
                         manifest["oracle_inputs"])
        asked = self.asked()
        self.assertEqual(list(support.PRESERVE), asked["preserve"])
        self.assertEqual(manifest["base_sha"], asked["base"])
        self.assertEqual([], asked["unexpected"])

    def test_the_task_as_it_stood_when_the_run_opened_is_what_the_run_is_judged_with(self):
        task_file = self.build_registry()
        with contextlib.redirect_stdout(io.StringIO()):
            self.open_run()
        task_file.write_text(json.dumps({"id": "T-0001",
                                         "oracle_inputs": {"preserve": ["other/**"]}}))
        self.commit()
        self.place_session()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]))
        self.assertEqual(list(support.PRESERVE), self.asked()["preserve"])

    def test_an_adhoc_run_asks_for_nothing_preserved(self):
        self.build_registry()
        self.close(task="adhoc")
        self.assertNotIn("oracle_inputs", self.manifest())
        asked = self.asked()
        self.assertNotIn("preserve", asked)
        self.assertNotIn("base", asked)

    def test_a_registered_task_whose_record_cannot_be_read_does_not_open(self):
        cases = {
            "absent": ("reg:T-0002", None),
            "not json": (support.TASK, "not json"),
            "another task's": (support.TASK, json.dumps({"id": "T-0009"})),
            "not a list": (support.TASK, json.dumps({"id": "T-0001",
                                                      "oracle_inputs": {"preserve": "*.md"}})),
            "not a task id": ("reg:../T-0001", None),
        }
        for name, (task, body) in cases.items():
            with self.subTest(case=name):
                self.build_registry(body=body)
                said = io.StringIO()
                with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
                    code = self.cli(["open-run", "--repo", str(self.clone), "--provider",
                                     "claude", "--task", task, "--scope", support.SCOPE])
                self.assertEqual(2, code, said.getvalue())
                self.assertIn("[registry] path is configured", said.getvalue())
                self.assertEqual([], list(self.state_root.rglob("manifest.json")))

    def test_without_a_registry_a_registered_run_asks_for_nothing_preserved(self):
        self.close()
        self.assertNotIn("oracle_inputs", self.manifest())
        self.assertNotIn("preserve", self.asked())


class CalibrationGenerationTest(SecondGenerationFixture):
    """Which suite calibrates the judgement, and what that decides about the call."""

    def test_the_second_suite_is_preferred_where_both_are_published(self):
        self.calibrate(V1_SUITE)
        self.calibrate(oracle.DOCS_SUITE_V2, status="fail", result="7/8")
        self.close()
        row = self.staged_row()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertEqual({"suite": oracle.DOCS_SUITE_V2, "status": "fail", "result": "7/8"},
                         row["oracle"]["calibration"])
        self.assertEqual(oracle.DOCS_SUITE_V2, self.judgement()["calibration"]["suite"])

    def test_a_passed_second_suite_admits_the_label_on_its_own_fence(self):
        self.build_registry()
        self.build_bridge()
        self.close()
        row = self.staged_row()
        self.assertEqual("TRUE_DONE", row["outcome"])
        self.assertEqual(oracle.DOCS_SUITE_V2, row["oracle"]["calibration"]["suite"])
        self.assertEqual(support.FENCE.replace("harness-claude-", "harness-claude-v2-"),
                         row["instrument"]["fence"])

    def test_the_second_suite_without_a_bridge_admits_no_label(self):
        self.build_registry()
        self.close()
        row = self.staged_row()
        self.assertEqual("UNKNOWN", row["outcome"])
        self.assertEqual(oracle.DOCS_SUITE_V2, row["oracle"]["calibration"]["suite"])
        self.assertIn(oracle.NO_BRIDGE, self.judgement()["reason"])

    def test_the_first_suite_is_the_fallback_and_its_oracle_is_asked_nothing_new(self):
        pathlib.Path(self.execution).joinpath(*oracle.DOCS_SELFTEST_V2).unlink()
        self.calibrate(V1_SUITE)
        self.build_registry()
        self.build_bridge()
        for name, text in (("first-generation judge", STUB_V1),
                           ("second-generation judge", STUB_V2)):
            with self.subTest(judge=name):
                self.stub(text)
                self._forget()
                self.close()
                asked = self.asked()
                for dropped in ("preserve", "base", "bridge"):
                    self.assertNotIn(dropped, asked)
                row = self.staged_row()
                self.assertEqual(V1_SUITE, row["oracle"]["calibration"]["suite"])
                self.assertEqual("TRUE_DONE", row["outcome"])
                self.assertEqual(support.FENCE, row["instrument"]["fence"])
                # What the task says is recorded all the same: it is the run's, whichever oracle
                # was able to use it.
                self.assertEqual(list(support.PRESERVE),
                                 self.manifest()["oracle_inputs"]["preserve"])

    def test_a_second_suite_over_a_judge_that_cannot_take_its_arguments_judges_nothing(self):
        self.stub(STUB_V1)
        self.build_registry()
        self.close()
        self.assertEqual("UNKNOWN", self.judgement()["outcome"])
        self.assertIn("does not accept base, preserve", self.judgement()["reason"])


class BridgePinTest(SecondGenerationFixture):
    """The server the oracle reads through, pinned when the run opens and passed to the oracle."""

    def test_the_bridge_s_version_and_commit_are_recorded_and_the_oracle_is_given_it(self):
        commit = self.build_bridge()
        self.close()
        pinned = self.manifest()["bridge"]
        self.assertEqual({"path": str(self.bridge.resolve()), "version": support.BRIDGE_VERSION,
                          "commit": commit}, pinned)
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(str(self.bridge.resolve()), self.asked()["bridge"])
        # What the oracle reported reading through is kept with the run's judgement.
        self.assertEqual(REPORTED_BRIDGE, self.judgement()["bridge"])

    def test_a_bridge_that_is_no_checkout_of_its_own_has_no_commit(self):
        self.build_bridge(checkout=False)
        self.close()
        self.assertIsNone(self.manifest()["bridge"]["commit"])

    def test_a_bridge_that_is_not_a_built_server_does_not_open_a_run(self):
        self.build_bridge()
        self.config_path.write_text(self.config_path.read_text().replace(
            str(self.bridge), str(self.bridge.parent.parent / "package.json")))
        said = io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "claude",
                             "--task", support.TASK, "--scope", support.SCOPE])
        self.assertEqual(2, code)
        self.assertIn("dist/server.js", said.getvalue())


class ProducerSegmentTest(unittest.TestCase):
    """The producer segment's grammar: loop, generation, context, in that order."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="exeris-producer-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "docs").mkdir()
        (self.root / record.FENCES).write_text(
            "| `2026-09-24-harness-claude-cc-2-1-281` | |\n"
            "| `2026-09-24-harness-claude-oracle3-v2-mcp-cc-2-1-281` | |\n"
            "| `2026-09-24-harness-antigravity-v2-cc-1-2-9` | |\n", encoding="utf-8")

    def test_each_segment_is_present_only_where_it_is_true_and_in_its_order(self):
        self.assertEqual("harness-claude", record.producer("claude"))
        self.assertEqual("harness-claude-v2", record.producer("claude", oracle_v2=True))
        self.assertEqual("harness-claude-mcp", record.producer("claude", mcp=True))
        self.assertEqual("harness-claude-oracle3-v2-mcp",
                         record.producer("claude", 3, oracle_v2=True, mcp=True))

    def test_each_instrument_state_resolves_its_own_fence(self):
        self.assertEqual("2026-09-24-harness-claude-oracle3-v2-mcp-cc-2-1-281",
                         record.fence(str(self.root), "claude", "2.1.281", oracle_rounds=3,
                                      oracle_v2=True, mcp=True))
        self.assertEqual("2026-09-24-harness-antigravity-v2-cc-1-2-9",
                         record.fence(str(self.root), "antigravity", "1.2.9", oracle_v2=True))
        self.assertEqual("2026-09-24-harness-claude-cc-2-1-281",
                         record.fence(str(self.root), "claude", "2.1.281"))
        # A state nothing registered writes no row, and is never read as the nearest one that is.
        for asked in ({"oracle_v2": True}, {"mcp": True}, {"oracle_rounds": 3, "mcp": True}):
            with self.subTest(asked=asked), self.assertRaises(NoRow) as refusal:
                record.fence(str(self.root), "claude", "2.1.281", **asked)
            self.assertEqual("fence-unregistered", refusal.exception.reason)

    def test_the_generation_is_read_from_the_suite_that_calibrated_the_judgement(self):
        self.assertTrue(oracle.second_generation(
            {"calibration": {"suite": oracle.DOCS_SUITE_V2, "status": "not-run"}}))
        self.assertFalse(oracle.second_generation({"calibration": {"suite": V1_SUITE}}))
        self.assertFalse(oracle.second_generation({}))


if __name__ == "__main__":
    unittest.main()
