"""The pull request's body: the arm writes it, the organisation's check passes it, then it is sent.

The cases pin what has to hold however the template or the clients change:

* *No body the gate would refuse is sent, and nothing is pushed for it.* A run with no body, or with
  one the check reports findings on, is refused before its branch leaves the machine.
* *The harness owns two lines and the arm owns the rest.* `Owner:` and `Exeris-Run:` are added to
  the arm's text, and an arm that writes its own `Owner:` is caught by the check, not repaired.
* *The check is asked what the gate asks.* As the execution identity's pull request, and with the
  ADR flag the gate's own path rule would set — whose `adr` label the harness then applies.
* *A driven run is asked for its body once the oracle passes it,* in the same session, with the
  check's findings sent back verbatim; the prompts are the instrument's and are not a person's.
"""

import contextlib
import io
import json
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path
import drive_test  # noqa: E402

from harness import drive, pr_body  # noqa: E402

#: A body the stand-in check refuses: one classification line still holds its placeholder.
UNANSWERED = support.BODY.replace("Scope class: test-tooling",
                                  "Scope class: <runtime hot path | runtime non-hot | test-tooling "
                                  "| docs-only>")


class CloseRunBodyTest(support.HarnessFixture):
    """What `close-run` sends, and what it refuses to."""

    keep_body = True

    def _close(self, *extra):
        said = io.StringIO()
        with contextlib.redirect_stderr(said), contextlib.redirect_stdout(io.StringIO()):
            code = self.cli(["close-run", "--run", self.run_id(), *extra])
        self.said = said.getvalue()
        return code

    def _opened(self, *, name="change.txt"):
        with contextlib.redirect_stdout(io.StringIO()):
            self.open_run()
        (self.worktree() / name).parent.mkdir(parents=True, exist_ok=True)
        self.commit(name=name)
        self.place_session()

    def _pushed(self):
        branch = self.manifest()["branch"]
        return self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
                        cwd=self.origin, check=False).returncode == 0

    def test_the_body_is_the_arm_s_text_with_the_two_lines_the_harness_owns(self):
        self._opened()
        self.write_body()
        self.assertEqual(0, self._close(), self.said)
        posted = self.posted_pulls()
        self.assertEqual(1, len(posted))
        self.assertEqual(pr_body.compose(support.BODY, support.OWNER_LOGIN, self.run_id()),
                         posted[0]["body"])
        self.assertTrue(posted[0]["body"].endswith(
            f"\n\nOwner: @{support.OWNER_LOGIN}\nExeris-Run: {self.run_id()}\n"))
        # Asked as the gate asks it: the execution identity's pull request, the composed body.
        checked = self.body_checks()
        self.assertEqual(1, len(checked))
        self.assertEqual(support.BOT_LOGIN, checked[0]["author"])
        self.assertEqual(posted[0]["body"], checked[0]["body"])
        self.assertFalse(checked[0]["adr_touched"])

    def test_a_run_with_no_body_is_refused_before_anything_is_pushed(self):
        self._opened()
        self.assertEqual(2, self._close())
        self.assertIn("no pull request body", self.said)
        self.assertFalse(self._pushed())
        self.assertEqual([], self.posted_pulls())

    def test_a_body_the_check_refuses_is_not_sent_and_nothing_is_pushed(self):
        self._opened()
        self.write_body(UNANSWERED)
        self.assertEqual(2, self._close())
        self.assertIn("'Scope class:' still holds the placeholder", self.said)
        self.assertFalse(self._pushed())
        self.assertEqual([], self.posted_pulls())

    def test_an_owner_line_the_arm_wrote_is_caught_rather_than_repaired(self):
        self._opened()
        self.write_body(support.BODY + "\nOwner: @someone-else\n")
        self.assertEqual(2, self._close())
        self.assertIn("2 'Owner:' lines", self.said)
        self.assertEqual([], self.posted_pulls())

    def test_a_change_to_an_adr_needs_its_refs_line_and_is_labelled(self):
        self._opened(name="docs/adr/ADR-001.link.md")
        self.write_body()
        self.assertEqual(2, self._close())
        self.assertIn("'Refs: ADR-NNN'", self.said)
        self.assertTrue(self.body_checks()[-1]["adr_touched"])

        self.write_body(support.BODY + "\nRefs: ADR-001\n")
        self.assertEqual(0, self._close(), self.said)
        labelled = [body for endpoint, method, body in self.gh_calls()
                    if method == "POST" and str(endpoint).endswith("/labels")]
        self.assertEqual([{"labels": ["adr"]}], labelled)

    def test_a_change_to_no_adr_is_not_labelled(self):
        self._opened()
        self.write_body()
        self.assertEqual(0, self._close(), self.said)
        self.assertFalse([endpoint for endpoint, _method, _body in self.gh_calls()
                          if str(endpoint).endswith("/labels")])

    def test_no_pr_asks_for_no_body(self):
        self._opened()
        self.assertEqual(0, self._close("--no-pr"), self.said)
        self.assertTrue(self._pushed())
        self.assertEqual([], self.body_checks())

    def test_without_a_configured_check_no_pull_request_is_opened(self):
        self.config_path.write_text(self.config_path.read_text().replace(
            f'body_check = "{self.body_check}"\n', ""))
        self._opened()
        self.write_body()
        self.assertEqual(2, self._close())
        self.assertIn("[pull_request] body_check", self.said)
        self.assertFalse(self._pushed())

    def test_a_configured_check_that_is_not_the_organisation_s_is_refused(self):
        other = self.body_check.parent / "another_check.py"
        other.write_text(self.body_check.read_text())
        self.config_path.write_text(self.config_path.read_text().replace(
            f'body_check = "{self.body_check}"', f'body_check = "{other}"'))
        self._opened()
        self.write_body()
        self.assertEqual(2, self._close())
        self.assertIn(pr_body.CHECKER, self.said)
        self.assertEqual([], self.posted_pulls())

    def test_the_run_is_told_where_its_body_goes(self):
        self._opened()
        exported = (self.run_dir() / "env").read_text(encoding="utf-8")
        self.assertIn(f"{pr_body.VARIABLE}=", exported)
        self.assertIn(str(self.run_dir() / pr_body.DIRECTORY / pr_body.FILE), exported)
        self.assertTrue((self.run_dir() / pr_body.DIRECTORY).is_dir())


class DrivenBodyTest(drive_test.DriveFixture):
    """The body rounds a driven run ends with."""

    keep_body = True

    def _body_record(self):
        return self._record().get("body")

    def test_true_done_asks_the_same_session_for_the_body_and_the_run_closes_with_it(self):
        self.script = [drive_test._judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        self.assertEqual(1, len(self.body_passes))
        asked = self.body_passes[0]
        body_path = self.run_dir() / pr_body.DIRECTORY / pr_body.FILE
        self.assertEqual(pr_body.request(str(body_path)), asked["prompt"])
        self.assertEqual(drive_test.SESSION_ONE, asked["argv"][asked["argv"].index("--resume") + 1])
        body = self._body_record()
        self.assertEqual("valid", body["stopped"])
        self.assertEqual(drive.digest(pr_body.request(str(body_path))),
                         body["rounds"][0]["prompt_sha256"])
        # The body prompt is the instrument's: it is named to the reader, and the row still counts
        # only the person's prompts.
        self.assertIn(body["rounds"][0]["prompt_sha256"], drive.oracle_prompts(self._record()))
        self._close()
        self.assertEqual(pr_body.compose(support.BODY, support.OWNER_LOGIN, self.run_id()),
                         self.posted_pulls()[0]["body"])
        self.assertEqual(2, self.staged_row()["execution"]["human_prompts"])

    def test_the_check_s_findings_are_sent_back_verbatim(self):
        self.bodies = [UNANSWERED, support.BODY]
        self.script = [drive_test._judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        body_path = str(self.run_dir() / pr_body.DIRECTORY / pr_body.FILE)
        self.assertEqual(2, len(self.body_passes))
        self.assertEqual(pr_body.feedback(body_path,
                                          ["'Scope class:' still holds the placeholder"]),
                         self.body_passes[1]["prompt"])
        body = self._body_record()
        self.assertEqual("valid", body["stopped"])
        self.assertEqual([1, 0], [entry["findings"] for entry in body["rounds"]])

    def test_a_body_never_written_exhausts_its_rounds_and_the_close_is_refused(self):
        self.bodies = [None]
        self.script = [drive_test._judgement("TRUE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        body_path = str(self.run_dir() / pr_body.DIRECTORY / pr_body.FILE)
        # The request, then the default two rounds of findings.
        self.assertEqual(3, len(self.body_passes))
        self.assertEqual(pr_body.feedback(body_path, [drive.BODY_ABSENT.format(path=body_path)]),
                         self.body_passes[1]["prompt"])
        self.assertEqual("rounds-exhausted", self._body_record()["stopped"])
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, self.cli(["close-run", "--run", self.run_id()]))
        self.assertEqual([], self.posted_pulls())

    def test_a_run_that_is_not_true_done_is_not_asked_for_a_body(self):
        self.script = [drive_test._judgement("FALSE_DONE")]
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        self.assertEqual([], self.body_passes)
        self.assertIsNone(self._body_record())

    def test_a_tree_the_body_round_moved_is_judged_again(self):
        self.script = [drive_test._judgement("TRUE_DONE"), drive_test._judgement("FALSE_DONE")]
        original = self._body_pass

        def moving(argv, env, prompt):
            self.commit(message="docs: an edit the body round was told not to make",
                        name="moved.txt")
            return original(argv, env, prompt)
        self._body_pass = moving
        self.open_driven()
        self.assertEqual(0, self.drive(0), self.said)
        driven = self._record()
        self.assertTrue(driven["body"]["tree_moved"])
        self.assertEqual("FALSE_DONE", driven["stopped"])


if __name__ == "__main__":
    unittest.main()
