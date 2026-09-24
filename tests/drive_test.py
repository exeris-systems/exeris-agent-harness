"""Driving an arm to `TRUE_DONE`: the loop, what it sends, and what the row says about it.

A driven run is passes of one session with the oracle judging the tree between them. The cases here
pin what has to hold however the clients behind the adapters change:

* *The loop continues on `FALSE_DONE` and on nothing else.* `TRUE_DONE` ends it, the rounds running
  out ends it, and `UNKNOWN` ends it at once — an instrument that could not judge has said nothing a
  pass could act on.
* *The feedback is the oracle's words.* A fixed template around each failing gate's check and
  detail, and nothing else.
* *Every round is the same session.* A later pass resumes the session the first one named, and a
  client that names none is refused a loop rather than given a new session.
* *The oracle's prompts are not a person's.* The steering count excludes them, a prompt the loop
  recorded and the session does not hold is a refusal, and a driven run sits on a fence of its own.

No pass reaches a model: `drive.run_pass` is replaced by a stand-in that writes what the client
would have written, and the oracle by one that answers what the case scripts.
"""

import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

from adapters.antigravity import drive as agy_drive  # noqa: E402
from adapters.antigravity import session as agy_session  # noqa: E402
from adapters.claude import drive as claude_drive  # noqa: E402
from adapters.claude import session as claude_session  # noqa: E402
from harness import cli, drive, oracle, pr_body, record  # noqa: E402
from harness.capture import NoRow  # noqa: E402

#: Session ids as the client writes them.
SESSION_ONE = "00000000-0000-4000-8000-0000000000c1"
SESSION_TWO = "00000000-0000-4000-8000-0000000000c2"

CALIBRATED = {"suite": "docs-mutation-v1", "status": "pass", "result": "8/8"}

#: What a failing tree's gates look like: one passing, one failing, one that did not run. Only the
#: failing one may reach the feedback.
GATES = [
    {"check": "frontmatter_check", "result": "pass", "detail": "placeholder: nothing wrong"},
    {"check": "registry_check", "result": "fail",
     "detail": "placeholder: an entry the registry does not carry\nand a second line"},
    {"check": "agents_render_check", "result": "not-run", "detail": "placeholder: not reached"},
]


def _judgement(outcome, gates=None, calibration=None):
    return {"outcome": outcome, "calibration": dict(calibration or CALIBRATED),
            "version": None, "gates": list(GATES if gates is None else gates), "reason": ""}


class DriveFixture(support.HarnessFixture):
    """A run opened with a task file, an oracle answering from a script, and a scripted client."""

    def setUp(self):
        super().setUp()
        self.configure_domain(support.DOCUMENTATION_DOMAIN)
        self.task = self.tmp / "task.txt"
        self.task.write_text("placeholder: the task this run was given\n", encoding="utf-8")
        self.passes = []
        self.script = []
        self.judged = 0
        self._stub(oracle, "judge", self._judge)
        self._stub(drive, "run_pass", self._pass)
        # What the stand-in client does on each pass; a case replaces it to leave something out.
        self.writes_feedback = True
        self.session_ids = None
        self.conversation = None
        # What the stand-in writes when it is asked for the pull request's body: one text per body
        # round, the last repeated, and nothing where the entry is `None`.
        self.bodies = [support.BODY]
        self.body_passes = []

    def _stub(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(lambda: setattr(module, name, original))

    def _judge(self, worktree, domain, **asked):
        answer = self.script[min(self.judged, len(self.script) - 1)]
        self.judged += 1
        return answer

    # ---- the stand-in client -------------------------------------------------------

    @staticmethod
    def _sent(env, prompt):
        """The text a pass was given, by whichever route its client reads it."""
        if prompt is not None:
            return prompt
        return pathlib.Path(env["EXERIS_PROMPT_FILE"]).read_text(encoding="utf-8")

    def _body_pass(self, argv, env, prompt):
        """A pass asked for the body: it writes the body and leaves the tree as it was."""
        text = self._sent(env, prompt)
        self.body_passes.append({"argv": list(argv), "env": dict(env), "prompt": text})
        written = self.bodies[min(len(self.body_passes), len(self.bodies)) - 1]
        where = pathlib.Path(env[pr_body.VARIABLE])
        if written is None:
            where.unlink(missing_ok=True)
        else:
            where.write_text(written, encoding="utf-8")
        number = len(self.passes) + len(self.body_passes)
        if "antigravity" in argv[0]:
            return self._agy_pass(number, env)
        self._append_turn(text, number)
        ids = self.session_ids or [SESSION_ONE]
        return 0, json.dumps({"type": "result", "session_id": ids[-1],
                              "result": "placeholder answer"})

    def _pass(self, argv, *, cwd, env, stderr_path, prompt=None):
        text = self._sent(env, prompt)
        if text.startswith((pr_body.REQUEST.split("{")[0], pr_body.FEEDBACK_HEAD.split("{")[0])):
            return self._body_pass(argv, env, prompt)
        number = len(self.passes) + 1
        self.passes.append({"argv": list(argv), "env": dict(env), "cwd": cwd, "prompt": prompt})
        self.commit(message=f"docs: round {number}", name=f"round-{number}.txt")
        if "antigravity" in argv[0]:
            return self._agy_pass(number, env)
        if number == 1:
            self.session = self.place_session()
        elif self.writes_feedback:
            self._append_turn(prompt, number)
        ids = self.session_ids or [SESSION_ONE]
        return 0, json.dumps({"type": "result", "session_id": ids[min(number, len(ids)) - 1],
                              "result": "placeholder answer"})

    def _append_turn(self, prompt, number):
        tree = str(self.worktree())
        lines = [
            {"type": "user", "isSidechain": False, "isMeta": False, "cwd": tree,
             "message": {"role": "user", "content": prompt},
             "uuid": f"00000000-0000-4000-8000-0000000001{number:02d}",
             "timestamp": "2026-09-20T09:01:00.000Z"},
            {"type": "assistant", "isSidechain": False, "cwd": tree,
             "message": {"model": "claude-sonnet-5", "id": f"msg_placeholder_drive_{number}",
                         "type": "message", "role": "assistant",
                         "content": [{"type": "text", "text": "placeholder assistant text"}],
                         "usage": {"input_tokens": 10, "output_tokens": 1}},
             "timestamp": "2026-09-20T09:01:05.000Z"},
        ]
        with open(self.session, "a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")

    def _agy_pass(self, number, env):
        conversation = self.conversation
        head = {"event": "init", "init": {"model": support.AGY_MODEL}}
        if conversation:
            head["conversation_id"] = conversation
        entries = [head,
                   {"event": "step_update", "step_update": {"step_index": number * 10,
                                                            "state": "DONE",
                                                            "step_type": "user_input"}},
                   {"event": "result", "result": {"num_turns": number,
                                                  "duration_seconds": 1.0,
                                                  "usage": {"input_tokens": 10 * number}}}]
        stream = self.run_dir() / "agy.jsonl"
        with open(stream, "a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry) + "\n")
        (self.run_dir() / "agy.version").write_text(f"{support.AGY_VERSION}\n")
        return 0, ""

    # ---- the harness ---------------------------------------------------------------

    def open_driven(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.open_run("--prompt-file", str(self.task), *extra)

    def drive(self, rounds):
        said, printed = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(printed):
            code = self.cli(["drive", "--run", self.run_id(), "--oracle-rounds", str(rounds)])
        self.said, self.printed = said.getvalue(), printed.getvalue()
        return code

    def _close(self):
        said, printed = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(printed):
            code = self.cli(["close-run", "--run", self.run_id()])
        self.said, self.printed = said.getvalue(), printed.getvalue()
        self.assertEqual(0, code, self.said)

    def _record(self):
        return json.loads((self.run_dir() / drive.RECORD).read_text(encoding="utf-8"))


class LoopTest(DriveFixture):
    """When the loop goes on, and when it stops."""

    def test_a_single_pass_is_one_round_that_resumes_nothing(self):
        self.script = [_judgement("FALSE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        self.assertEqual(1, len(self.passes))
        self.assertNotIn("--resume", self.passes[0]["argv"])
        driven = self._record()
        self.assertEqual("rounds-exhausted", driven["stopped"])
        self.assertEqual([hashlib.sha256(self.task.read_bytes()).hexdigest()],
                         [entry["prompt_sha256"] for entry in driven["rounds"]])

        # A single pass is the single-pass producer: its row sits where an undriven run's does and
        # counts what an undriven run's counts.
        self._close()
        row = self.staged_row()
        self.assertEqual(support.FENCE, row["instrument"]["fence"])
        self.assertEqual(2, row["execution"]["human_prompts"])
        self.assertEqual("FALSE_DONE", row["outcome"])

    def test_the_loop_stops_at_true_done(self):
        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(3), self.said)
        self.assertEqual(2, len(self.passes))
        driven = self._record()
        self.assertEqual("TRUE_DONE", driven["stopped"])
        self.assertEqual(["FALSE_DONE", "TRUE_DONE"],
                         [entry["outcome"] for entry in driven["rounds"]])

    def test_the_second_pass_resumes_the_session_the_first_one_named(self):
        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(1), self.said)
        argv = self.passes[1]["argv"]
        self.assertEqual(SESSION_ONE, argv[argv.index("--resume") + 1])

    def test_a_client_that_answers_a_resume_with_another_session_is_refused(self):
        self.script = [_judgement("FALSE_DONE")]
        self.session_ids = [SESSION_ONE, SESSION_TWO]
        self.open_driven()
        self.assertEqual(2, self.drive(3))
        self.assertEqual(2, len(self.passes))
        self.assertEqual("session-changed", self._record()["stopped"])

    def test_a_session_id_of_another_shape_is_not_resumed(self):
        self.script = [_judgement("FALSE_DONE")]
        self.session_ids = ["--settings=/placeholder/elsewhere.json"]
        self.open_driven()
        self.assertEqual(2, self.drive(2))
        self.assertEqual(1, len(self.passes), "a value that is not a session id was resumed")
        self.assertEqual("resume-unavailable", self._record()["stopped"])

    def test_a_worktree_outside_the_run_directory_is_not_driven(self):
        self.open_driven()
        manifest_path = self.run_dir() / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["worktree"] = str(self.tmp)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(2, self.drive(1))
        self.assertEqual([], self.passes)
        self.assertIn("not inside its run directory", self.said)

    def test_a_run_that_is_not_a_run_id_is_refused_where_it_is_parsed(self):
        self.open_driven()
        for given in ("../" + self.run_id(), self.run_id().lower(), self.run_id() + "0"):
            with self.subTest(run=given):
                said = io.StringIO()
                with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
                    code = self.cli(["drive", "--run", given])
                self.assertEqual(2, code)
                self.assertIn("is not a run id", said.getvalue())
        self.assertEqual([], self.passes)

    def test_the_launcher_is_one_the_checkout_ships(self):
        self.assertTrue(cli._launcher("claude").endswith("/adapters/claude/launch.sh"))
        for given in ("../adapters/claude", "claude/../claude", "absent"):
            with self.subTest(adapter=given), self.assertRaises(cli.Refused):
                cli._launcher(given)

    def test_unknown_stops_the_loop_at_once(self):
        # Once where the oracle could not judge, and once where it judged and its suite has not
        # passed: both are `UNKNOWN` by the time the loop decides, and neither is looped on.
        for judged in (_judgement("UNKNOWN", gates=[]),
                       _judgement("FALSE_DONE", calibration={"suite": "docs-mutation-v1",
                                                             "status": "not-run",
                                                             "result": "not-run"})):
            with self.subTest(oracle=judged["outcome"]):
                self.passes, self.judged, self.script = [], 0, [judged]
                self.open_driven()
                self.assertEqual(0, self.drive(3), self.said)
                self.assertEqual(1, len(self.passes))
                self.assertEqual("UNKNOWN", self._record()["stopped"])

    def test_a_persistent_false_done_exhausts_the_rounds_it_was_allowed(self):
        self.script = [_judgement("FALSE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(2), self.said)
        self.assertEqual(3, len(self.passes))
        driven = self._record()
        self.assertEqual("rounds-exhausted", driven["stopped"])
        self.assertEqual([1, 2, 3], [entry["round"] for entry in driven["rounds"]])

    def test_every_pass_reads_every_readable_directory(self):
        standards = [self.tmp / "standards-one", self.tmp / "standards-two"]
        for directory in standards:
            directory.mkdir()
        self.config_path.write_text(self.config_path.read_text().replace(
            f'scope = ["{support.SCOPE}"]\n',
            f'scope = ["{support.SCOPE}"]\nreadable = ["{standards[0]}", "{standards[1]}"]\n'))
        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(1), self.said)
        for made in self.passes:
            argv = made["argv"]
            given = [argv[i + 1] for i, word in enumerate(argv) if word == "--add-dir"]
            # The configured directories, and the one the body is written in: every pass is
            # allowed what the last one is.
            self.assertEqual([str(directory) for directory in standards]
                             + [str(self.run_dir() / pr_body.DIRECTORY)], given)

    def test_a_readable_directory_that_is_not_there_is_refused(self):
        self.config_path.write_text(self.config_path.read_text().replace(
            f'scope = ["{support.SCOPE}"]\n',
            f'scope = ["{support.SCOPE}"]\nreadable = ["{self.tmp / "absent"}"]\n'))
        self.open_driven()
        self.assertEqual(2, self.drive(1))
        self.assertEqual([], self.passes)

    def test_the_pass_is_given_its_powers_and_the_run_s_identity(self):
        self.script = [_judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        made = self.passes[0]
        argv = made["argv"]
        self.assertEqual(claude_drive.ALLOWED_TOOLS, argv[argv.index("--allowedTools") + 1])
        self.assertEqual("acceptEdits", argv[argv.index("--permission-mode") + 1])
        self.assertEqual("json", argv[argv.index("--output-format") + 1])
        # The task, on standard input as the file holds it, and nowhere on the command line.
        self.assertEqual(self.task.read_text(), made["prompt"])
        self.assertNotIn(self.task.read_text(), argv)
        self.assertEqual(str(self.worktree()), made["cwd"])
        # The run's token reaches the pass through its environment and never its command line.
        self.assertEqual(support.FAKE_TOKEN, made["env"]["GH_TOKEN"])
        self.assertNotIn(support.FAKE_TOKEN, " ".join(argv))

    def test_a_run_opened_without_a_task_file_is_not_driven(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.open_run()
        self.assertEqual(2, self.drive(1))
        self.assertEqual([], self.passes)

    def test_a_task_file_changed_since_the_run_opened_is_not_sent(self):
        self.open_driven()
        self.task.write_text("placeholder: another task\n", encoding="utf-8")
        self.assertEqual(2, self.drive(1))
        self.assertEqual([], self.passes)

    def test_a_run_is_driven_once(self):
        self.script = [_judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        self.assertEqual(2, self.drive(0))
        self.assertEqual(1, len(self.passes))


class FeedbackTest(DriveFixture):
    """What the oracle's prompt says, and that it says nothing else."""

    def test_the_feedback_is_the_template_and_the_failing_gates_verbatim(self):
        expected = (drive.FEEDBACK_HEAD
                    + "- registry_check: placeholder: an entry the registry does not carry\n"
                      "and a second line\n")
        self.assertEqual(expected, drive.feedback(GATES))

        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(1), self.said)
        self.assertEqual(expected, self.passes[1]["prompt"])
        self.assertNotIn(expected, self.passes[1]["argv"])
        # Kept beside the run, because it is instrument output and not the task.
        driven = self._record()
        self.assertEqual(hashlib.sha256(expected.encode("utf-8")).hexdigest(),
                         driven["rounds"][1]["prompt_sha256"])
        self.assertEqual(expected, (self.run_dir() / drive.DIRECTORY / "round-2.prompt")
                         .read_text(encoding="utf-8"))

    def test_a_false_done_with_no_failing_gate_sends_nothing(self):
        passing = [gate for gate in GATES if gate["result"] != "fail"]
        self.assertIsNone(drive.feedback(passing))
        self.script = [_judgement("FALSE_DONE", gates=passing)]
        self.open_driven()
        self.assertEqual(2, self.drive(2))
        self.assertEqual(1, len(self.passes))
        self.assertEqual("no-failing-gate", self._record()["stopped"])


class DrivenRowTest(DriveFixture):
    """What `close-run` makes of a driven run."""

    def _driven_and_closed(self):
        self.script = [_judgement("FALSE_DONE"), _judgement("FALSE_DONE"),
                       _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(support.ORACLE_ROUNDS), self.said)
        self._close()

    def test_the_oracle_s_prompts_are_not_a_person_s_and_a_person_s_still_are(self):
        self._driven_and_closed()
        row = self.staged_row()
        # The fixture session carries two prompts a person typed after the first; the two the loop
        # sent are in the session too, and are not counted.
        self.assertEqual(2, row["execution"]["human_prompts"])
        # The whole session is the run's: the turns the resumed passes took are counted with it,
        # the body's among them.
        self.assertEqual(4 + 2 + 1, row["execution"]["turns"])
        self.assertEqual("TRUE_DONE", row["outcome"])

    def test_a_driven_run_sits_on_the_fence_of_its_loop(self):
        self._driven_and_closed()
        self.assertEqual(support.ORACLE_FENCE, self.staged_row()["instrument"]["fence"])

    def test_the_rounds_used_are_staged_beside_the_row_and_printed(self):
        self._driven_and_closed()
        staged = json.loads((self.run_dir() / "staging" / drive.RECORD).read_text())
        self.assertEqual(3, staged["passes"])
        self.assertEqual(2, staged["feedback_rounds"])
        self.assertEqual(support.ORACLE_ROUNDS, staged["oracle_rounds"])
        self.assertEqual("TRUE_DONE", staged["stopped"])
        self.assertIn("2 of at most 2 oracle round(s)", self.printed)
        # And the row gains no field for them.
        self.assertNotIn("feedback_rounds", json.dumps(self.staged_row()))

    def test_a_recorded_oracle_prompt_the_session_does_not_hold_yields_no_row(self):
        self.writes_feedback = False
        self._driven_and_closed()
        self.assertEqual([], self.staged_rows())
        self.assertIn("oracle-prompt-unmatched", self.said)

    def test_a_drive_record_that_cannot_be_read_yields_no_row(self):
        self._driven_and_closed_without_closing()
        (self.run_dir() / drive.RECORD).write_text("not json", encoding="utf-8")
        self._close()
        self.assertEqual([], self.staged_rows())
        self.assertIn("drive-record-unreadable", self.said)

    def _driven_and_closed_without_closing(self):
        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(1), self.said)


class AntigravityDriveTest(DriveFixture):
    """The other client, whose session id is in its stream rather than on its answer."""

    def open_driven(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "antigravity",
                             "--task", support.TASK, "--scope", support.SCOPE,
                             "--prompt-file", str(self.task), *extra])
        self.assertEqual(0, code)

    def test_the_second_pass_continues_the_conversation_the_stream_named(self):
        self.conversation = "00000000-0000-4000-8000-00000000000a"
        self.script = [_judgement("FALSE_DONE"), _judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(1), self.said)
        self.assertEqual(2, len(self.passes))
        argv = self.passes[1]["argv"]
        self.assertEqual(self.conversation, argv[argv.index("--conversation") + 1])
        # The feedback reaches the launcher as the file it reads its prompt from.
        self.assertEqual(drive.feedback(GATES), pathlib.Path(
            self.passes[1]["env"]["EXERIS_PROMPT_FILE"]).read_text(encoding="utf-8"))
        self.assertEqual(str(self.task), self.passes[0]["env"]["EXERIS_PROMPT_FILE"])

    def test_a_conversation_id_of_another_shape_is_not_continued(self):
        self.conversation = "placeholder conversation"
        self.script = [_judgement("FALSE_DONE")]
        self.open_driven()
        self.assertEqual(2, self.drive(2))
        self.assertEqual(1, len(self.passes))
        self.assertEqual("resume-unavailable", self._record()["stopped"])

    def test_the_prompt_reaches_the_launcher_by_file_and_standard_input_is_closed(self):
        self.script = [_judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        self.assertIsNone(self.passes[0]["prompt"])
        self.assertNotIn(self.task.read_text(), self.passes[0]["argv"])

    def test_a_stream_that_names_no_conversation_refuses_the_loop(self):
        self.conversation = None
        self.script = [_judgement("FALSE_DONE")]
        self.open_driven()
        self.assertEqual(2, self.drive(2))
        self.assertEqual(1, len(self.passes), "a new session was started in place of a resume")
        self.assertEqual("resume-unavailable", self._record()["stopped"])
        self.assertIn("no session to resume", self.said)


class PassTest(unittest.TestCase):
    """One pass, started for real on a stand-in program: what reaches it, and what does not."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-drive-pass-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.stderr = str(self.tmp / "round-1.stderr")

    def _echo(self, prompt):
        argv = [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"]
        return drive.run_pass(argv, cwd=str(self.tmp), env={}, stderr_path=self.stderr,
                              prompt=prompt)

    def test_the_prompt_is_the_pass_s_standard_input_byte_for_byte(self):
        text = "placeholder: a prompt\nof two lines, 'quoted' and $(not run)\n"
        self.assertEqual((0, text), self._echo(text))

    def test_without_a_prompt_standard_input_is_closed(self):
        self.assertEqual((0, ""), self._echo(None))

    def test_a_command_line_word_with_a_control_character_is_not_started(self):
        marker = self.tmp / "started"
        for word in ("placeholder\nword", "placeholder\x00word", ""):
            with self.subTest(word=word):
                argv = [sys.executable, "-c", f"open({str(marker)!r}, 'w')", word]
                self.assertEqual((127, ""), drive.run_pass(argv, cwd=str(self.tmp), env={},
                                                           stderr_path=self.stderr))
                self.assertFalse(marker.exists())
                self.assertIn("was not started", pathlib.Path(self.stderr).read_text())

    def test_an_adapter_puts_no_resume_of_another_shape_on_a_command_line(self):
        for module in (claude_drive, agy_drive):
            with self.subTest(adapter=module.__name__), self.assertRaises(ValueError):
                module.arguments(readable=[], resume="--settings=/placeholder/elsewhere.json")
        self.assertEqual(["-p", "--output-format", "json", "--permission-mode", "acceptEdits",
                          "--allowedTools", claude_drive.ALLOWED_TOOLS, "--resume", SESSION_ONE],
                         claude_drive.arguments(readable=[], resume=SESSION_ONE))

    def test_the_claude_answer_yields_a_session_only_of_the_session_id_s_shape(self):
        answer = lambda named: drive.Answer(  # noqa: E731
            stdout=json.dumps({"session_id": named}), run_dir=str(self.tmp))
        self.assertEqual(SESSION_ONE, claude_drive.resume_id(answer(SESSION_ONE)))
        for named in ("sid-placeholder-1", SESSION_ONE + " --x", "", 7):
            with self.subTest(named=named):
                self.assertIsNone(claude_drive.resume_id(answer(named)))


class FenceTest(unittest.TestCase):
    """The producer segment, composed and resolved."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="exeris-fence-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        (self.root / "docs").mkdir()
        (self.root / record.FENCES).write_text(
            "| `2026-09-19-harness-claude-cc-2-1-278` | single pass |\n"
            "| `2026-09-19-harness-claude-oracle3-cc-2-1-278` | driven |\n", encoding="utf-8")

    def test_the_producer_names_the_loop_only_where_there_was_one(self):
        self.assertEqual("harness-claude", record.producer("claude"))
        self.assertEqual("harness-claude", record.producer("claude", 0))
        self.assertEqual("harness-claude-oracle3", record.producer("claude", 3))

    def test_each_producer_resolves_its_own_fence(self):
        self.assertEqual("2026-09-19-harness-claude-cc-2-1-278",
                         record.fence(str(self.root), "claude", "2.1.278"))
        self.assertEqual("2026-09-19-harness-claude-oracle3-cc-2-1-278",
                         record.fence(str(self.root), "claude", "2.1.278", oracle_rounds=3))
        # A loop allowed another number of rounds is another producer, and nothing registers it.
        with self.assertRaises(NoRow) as refusal:
            record.fence(str(self.root), "claude", "2.1.278", oracle_rounds=2)
        self.assertEqual("fence-unregistered", refusal.exception.reason)


class ReaderTest(unittest.TestCase):
    """The two session readers, told which prompts were the oracle's."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-drive-reader-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_a_prompt_the_oracle_sent_is_not_steering_in_the_claude_log(self):
        steering = hashlib.sha256(b"placeholder second prompt: steering\n").hexdigest()
        self.assertEqual(2, claude_session.read(str(support.SESSION))["human_prompts"])
        self.assertEqual(1, claude_session.read(str(support.SESSION),
                                                oracle_prompts=[steering])["human_prompts"])

    def test_an_oracle_prompt_the_claude_log_does_not_hold_is_refused(self):
        with self.assertRaises(NoRow) as refusal:
            claude_session.read(str(support.SESSION), oracle_prompts=["0" * 64])
        self.assertEqual("oracle-prompt-unmatched", refusal.exception.reason)

    def _agy(self, entries):
        path = self.tmp / agy_session.STREAM
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        (self.tmp / agy_session.VERSION_FILE).write_text(f"{support.AGY_VERSION}\n")
        return str(path)

    def _resumed(self, conversation="00000000-0000-4000-8000-00000000000a"):
        """The fixture stream and one resumed invocation of its conversation after it."""
        entries = [json.loads(line) for line in
                   support.AGY_STREAM.read_text(encoding="utf-8").splitlines() if line.strip()]
        step = lambda index, kind: {"event": "step_update", "step_update": {  # noqa: E731
            "conversation_id": conversation, "step_index": index, "state": "DONE",
            "step_type": kind}}
        return entries + [
            {"event": "init", "conversation_id": conversation,
             "init": {"model": support.AGY_MODEL}},
            step(5, "user_input"), step(6, "system_message"), step(7, "agent_response"),
            {"event": "result", "result": {"conversation_id": conversation, "num_turns": 4,
                                           "duration_seconds": 16.2,
                                           "usage": {"input_tokens": 2000,
                                                     "output_tokens": 190}}}]

    def test_a_resumed_conversation_is_one_session_and_its_opening_prompt_the_oracle_s(self):
        facts = agy_session.read(self._agy(self._resumed()), oracle_prompts=["0" * 64])
        # Three prompts: the task, a person's, and the one that opened the resumed invocation.
        self.assertEqual(1, facts["human_prompts"])
        # The client's own line into a resumed conversation is not tool work.
        self.assertEqual(2, facts["tool_calls"])
        # The last closing event carries the conversation's totals.
        self.assertEqual(4, facts["turns"])
        self.assertEqual({"input_tokens": 2000, "output_tokens": 190}, facts["usage"])
        # What a resumed invocation's duration covers is unmeasured, so none is stated.
        self.assertIsNone(facts["wall_time_ms"])

    def test_more_oracle_rounds_than_resumed_invocations_is_refused(self):
        stream = self._agy(self._resumed())
        with self.assertRaises(NoRow) as refusal:
            agy_session.read(stream, oracle_prompts=["0" * 64, "1" * 64])
        self.assertEqual("oracle-prompt-unmatched", refusal.exception.reason)

    def test_two_conversations_in_one_stream_are_two_sessions(self):
        stream = self._agy(self._resumed(conversation="00000000-0000-4000-8000-00000000000b"))
        with self.assertRaises(NoRow) as refusal:
            agy_session.read(stream)
        self.assertEqual("multiple-sessions", refusal.exception.reason)

    def test_the_conversation_to_resume_is_the_one_the_latest_init_names(self):
        self._agy(self._resumed())
        self.assertEqual("00000000-0000-4000-8000-00000000000a",
                         agy_drive.resume_id(drive.Answer(stdout="", run_dir=str(self.tmp))))
        self._agy([{"event": "init", "init": {"model": support.AGY_MODEL}}])
        self.assertIsNone(agy_drive.resume_id(drive.Answer(stdout="", run_dir=str(self.tmp))))


if __name__ == "__main__":
    unittest.main()
