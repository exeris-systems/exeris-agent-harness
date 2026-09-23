"""The second arm's stream: what one file of events says about one run.

This client writes its events to standard output, so the file the launcher redirected them into is
both the session log and the stream the row references. The cases here are the ones that decide
whether the counts taken from it mean what a column of them would be read to mean.

* *A step is counted once, however often it is reported.* The client reports a step as its state
  moves, so a count taken per event would report the client's chattiness rather than the run's work.
* *What the stream does not carry is absent, never zero.* This client's state vocabulary is
  unmeasured beyond the states a completed step passes through; a `0` for refusals would report a
  gate nobody has seen fire as a gate that did not fire, and `capture_level` says which.
* *The turns are the client's own count, and so is the time.* Both are on the closing event; a
  stream without one is a session this adapter cannot count.
* *The one name the counts rest on is checked, not assumed.* Both counts are defined against the
  prompt step's type, so a stream that never names it is refused — counting it as tool work would
  make a run nobody steered out of a run whose steering nobody could see.
* *No text leaves the adapter.* The prompt reaches this client on its command line and the stream
  carries none of it, so the digest of what instructed the run comes from the file the harness
  passed and from nowhere else.
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

from adapters.antigravity import session  # noqa: E402
from harness.capture import NoRow  # noqa: E402


def _lines(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _rendered(entries):
    return "".join(json.dumps(entry) + "\n" for entry in entries)


class AgyStreamTest(unittest.TestCase):
    """The derivation, over a stream written by hand and holding placeholder text only."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-agy-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.entries = _lines(support.AGY_STREAM.read_text(encoding="utf-8"))

    def _stream(self, entries=None, version=support.AGY_VERSION):
        path = self.tmp / session.STREAM
        path.write_text(_rendered(entries if entries is not None else self.entries),
                        encoding="utf-8")
        if version is not None:
            (self.tmp / session.VERSION_FILE).write_text(f"agy {version}\n", encoding="utf-8")
        return path

    # ---- the derivation ----------------------------------------------------------

    def test_the_stream_answers_every_count_the_row_has_a_field_for(self):
        path = self._stream()
        facts = session.read(str(path))

        self.assertEqual(support.AGY_MODEL, facts["model_id"])
        self.assertEqual("antigravity", facts["client"])
        self.assertEqual(support.AGY_VERSION, facts["version"])
        # The client's own count of the turns it took, from the closing event — not a count of the
        # steps it wrote, which is a different unit.
        self.assertEqual(3, facts["turns"])
        # Two tool steps: every step whose type is neither what a person submitted nor what the
        # model answered.
        self.assertEqual(2, facts["tool_calls"])
        # Two prompts, of which the first is what started the run; what is counted is steering.
        self.assertEqual(1, facts["human_prompts"])
        self.assertEqual({"input_tokens": 1500, "output_tokens": 150,
                          "cache_read_tokens": 15000}, facts["usage"])
        self.assertEqual(12500, facts["wall_time_ms"])
        self.assertEqual(len(self.entries), facts["event_count"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), facts["sha256"])

    def test_a_count_the_contract_has_no_field_for_is_not_carried(self):
        # The client reports a thinking count and a total beside the four the contract names. The
        # first is not one of them and the second is a sum a reader can take.
        facts = session.read(str(self._stream()))
        self.assertNotIn("thinking_tokens", facts["usage"])
        self.assertNotIn("total_tokens", facts["usage"])
        # A cache write count is not reported at all, so it is absent rather than zero.
        self.assertNotIn("cache_write_tokens", facts["usage"])

    def test_a_step_reported_twice_is_one_step(self):
        doubled = list(self.entries)
        for entry in self.entries:
            if entry.get("event") == "step_update":
                doubled.append(entry)
        facts = session.read(str(self._stream(doubled)))
        self.assertEqual(2, facts["tool_calls"], "a step was counted once per update")
        self.assertEqual(1, facts["human_prompts"])

    def test_a_refusal_nothing_names_is_absent_and_says_so(self):
        facts = session.read(str(self._stream()))
        self.assertIsNone(facts["permission_denials"])
        self.assertEqual("counts-only", facts["capture_level"])

    def test_a_refusal_the_stream_names_is_counted(self):
        entries = list(self.entries)
        entries.insert(-1, {"event": "step_update",
                            "step_update": {"step_index": 5, "state": "TOOL_DENIED",
                                            "step_type": "tool_call"}})
        facts = session.read(str(self._stream(entries)))
        self.assertEqual(1, facts["permission_denials"])
        self.assertEqual("full", facts["capture_level"])

    def test_the_step_types_the_stream_used_are_printed_and_recorded_nowhere_else(self):
        # The set of tool step types is unmeasured, so the names are printed beside the run and
        # left out of the row: a count whose meaning is unchecked does not belong in a column.
        facts = session.read(str(self._stream()))
        notes = "\n".join(facts["notes"])
        self.assertIn("tool_call=2", notes)
        self.assertIn("user_input=2", notes)
        self.assertIn("DONE", notes)

    def test_no_text_of_the_session_leaves_the_adapter(self):
        facts = session.read(str(self._stream()))
        self.assertIsNone(facts["system_prompt_sha256"],
                          "the stream carries no prompt, so the adapter must claim none")
        self.assertNotIn("placeholder answer", json.dumps(facts))

    # ---- what the adapter refuses ------------------------------------------------

    def test_a_stream_naming_no_prompt_step_is_refused_rather_than_counted(self):
        # The client's step-type vocabulary is unmeasured. Under another spelling of the prompt
        # step, every prompt falls into the tool count and the steering count reads `0` — a row
        # asserting that nobody steered the run, over a column nothing measured.
        renamed = json.loads(json.dumps(self.entries).replace('"user_input"', '"user_turn"'))
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            with self.assertRaises(NoRow) as refusal:
                session.read(str(self._stream(renamed)))
        self.assertEqual("session-unreadable", refusal.exception.reason)
        self.assertIn(session.PROMPT_STEP, refusal.exception.detail)
        # What makes the refusal answerable: the names the stream actually used.
        self.assertIn("user_turn=2", noticed.getvalue())

    def test_a_stream_that_never_closed_is_refused(self):
        entries = [entry for entry in self.entries if entry.get("event") != "result"]
        with self.assertRaises(NoRow) as refusal:
            session.read(str(self._stream(entries)))
        self.assertEqual("session-unreadable", refusal.exception.reason)

    def test_two_models_in_one_stream_are_two_sessions(self):
        entries = list(self.entries)
        entries.insert(1, {"event": "init", "init": {"model": "placeholder-model-2"}})
        with self.assertRaises(NoRow) as refusal:
            session.read(str(self._stream(entries)))
        self.assertEqual("multi-model", refusal.exception.reason)

    def test_a_stream_naming_no_model_is_refused(self):
        entries = [entry for entry in self.entries if entry.get("event") != "init"]
        with self.assertRaises(NoRow) as refusal:
            session.read(str(self._stream(entries)))
        self.assertEqual("model-absent", refusal.exception.reason)

    def test_the_version_is_the_one_the_launcher_recorded(self):
        # The version that ran is the version that was installed when it ran; the one installed now
        # is a different fact, and a client upgraded in between would otherwise be reported as the
        # one that did the work.
        self.assertEqual("3.2.1", session.version(str(self._stream(version="3.2.1").parent)))

    def test_the_stream_is_found_beside_the_run_and_nowhere_else(self):
        manifest = {"worktree": str(self.tmp / "wt")}
        with self.assertRaises(NoRow) as refusal:
            session.locate(manifest)
        self.assertEqual("session-not-found", refusal.exception.reason)
        self._stream()
        self.assertEqual(str(self.tmp / session.STREAM), session.locate(manifest))


class AgyRowTest(support.HarnessFixture):
    """One run of this arm, from `open-run` to the row `close-run` stages."""

    def test_the_row_carries_what_this_arm_measured_and_no_more(self):
        self.open_agy_run()
        self.commit()
        stream = self.place_agy_stream()
        self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]), "close-run failed")

        row = self.staged_row()
        agent = row["agent"]
        self.assertEqual(support.AGY_PROVIDER, agent["provider"])
        self.assertEqual(support.AGY_MODEL, agent["model_id"])
        self.assertEqual(f"unresolved:{support.AGY_MODEL}", agent["model_snapshot"])
        self.assertEqual({"client": support.AGY_CLIENT, "version": support.AGY_VERSION},
                         agent["harness"])

        execution = row["execution"]
        self.assertEqual(3, execution["turns"])
        self.assertEqual(2, execution["tool_calls"])
        self.assertEqual(1, execution["human_prompts"])
        # Absent rather than zero, with the level beside it saying why.
        self.assertNotIn("permission_denials", execution)
        self.assertEqual("counts-only", execution["capture_level"])
        # The runtime's own figure for the work, not the harness's for the command: between the
        # two lie the harness's start-up, the push and the pull request.
        self.assertEqual(12500, execution["wall_time_ms"])
        self.assertEqual(hashlib.sha256(stream.read_bytes()).hexdigest(),
                         execution["event_stream"]["sha256"])

        # One producer at one client version is one fence, so this arm resolves its own.
        self.assertEqual(support.AGY_FENCE, row["instrument"]["fence"])
        # The stream is what goes to the streams repository, because it is the session log.
        self.assertEqual([stream.read_text()],
                         [path.read_text() for path in self.staged_streams()])


if __name__ == "__main__":
    unittest.main()
