"""The human arm: the measurement a paired comparison cannot be read without.

A model arm's row can carry a cost, a count of turns and an outcome and still say nothing about
whether the work was worth doing at that price. That reading needs a human reference point,
measured on the same task, under the same oracle — and measured first, so that the human has not
seen a model's output and no row has to be rewritten once the baseline exists.

The cases here are the ones that make that protocol observable rather than remembered.

* *The tree is the person's.* No token is minted, no identity is written, no run environment is
  exported: the commits are theirs, authored as themselves. What the harness adds is the hook that
  times them and the trailer that joins them to this measurement.
* *What it measured is what a row carries.* `wall_time_ms`, the oracle's outcome, and the changes
  the diff reports — the shape the row contract names, so that the object printed here is the
  object pasted into the group record and the object every arm of the group then carries.
* *A model arm of a human-baselined group does not open before the baseline exists.* That is the
  protocol, enforced where it can be: at the moment the arm would have started.
"""

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

GROUP = "G-0001"


class BaselineTest(support.HarnessFixture):
    def _baseline(self, *extra):
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed), contextlib.redirect_stdout(io.StringIO()) as out:
            code = self.cli(["baseline", *extra])
        self.said = out.getvalue() + noticed.getvalue()
        self.printed = out.getvalue()
        return code

    def _open_baseline(self, *extra):
        self.assertEqual(0, self._baseline("--repo", str(self.clone), "--task", support.TASK,
                                           "--group", GROUP, "--scope", support.SCOPE, *extra),
                         f"baseline refused: {self.said}")
        return self.run_dir("baseline")

    def _person_commit(self, name="a-change.md", body="a line the person wrote\n"):
        """One commit in the baseline's tree, made the way the person makes one: their own git."""
        tree = self.worktree("baseline")
        (tree / name).write_text(body)
        for args in (["add", name], ["commit", "-m", "docs: the person's own change"]):
            done = subprocess.run(["git", "-C", str(tree), *args], env=self._fixture_env(),
                                  capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                              env=self._fixture_env(), capture_output=True,
                              text=True).stdout.strip()

    # ---- what a baseline is ------------------------------------------------------

    def test_the_tree_holds_no_identity_and_no_credential_of_the_run_s(self):
        run_dir = self._open_baseline()
        manifest = self.manifest("baseline")
        self.assertEqual("baseline", manifest["kind"])
        self.assertEqual(GROUP, manifest["group"])
        self.assertEqual(support.TASK, manifest["task"])

        # Nothing was minted and nothing is exported: a human arm acts as the human, so there is no
        # identity for it to be bound to and no credential for it to spend.
        self.assertFalse((run_dir / "token").exists(), "a human baseline minted a token")
        self.assertFalse((run_dir / "env").exists(), "a human baseline exported a run environment")
        self.assertFalse((run_dir / "gitconfig").exists())

        tree = self.worktree("baseline")
        bound = subprocess.run(["git", "-C", str(tree), "config", "--worktree", "--list"],
                               env=self._fixture_env(), capture_output=True, text=True).stdout
        self.assertIn("core.hookspath", bound.lower())
        self.assertNotIn("user.email", bound.lower(),
                         "a human baseline bound an identity to the person's own tree")
        self.assertNotIn("credential.helper", bound.lower())

    def test_the_commits_are_the_person_s_and_carry_the_run_trailer(self):
        self._open_baseline()
        head = self._person_commit()
        tree = self.worktree("baseline")
        shown = subprocess.run(["git", "-C", str(tree), "log", "-1", "--format=%an%n%ae%n%B",
                                head], env=self._fixture_env(), capture_output=True,
                               text=True).stdout
        self.assertIn("Fixture", shown, "the commit was not authored by the person")
        self.assertIn(f"Exeris-Run: {self.run_id('baseline')}", shown)
        # The other hook this tree binds: when each commit was made, beside the run and in no
        # record.
        self.assertIn(head, (self.run_dir("baseline") / "timing.log").read_text())

    def test_what_it_measured_is_the_object_a_row_carries(self):
        self._open_baseline()
        self._person_commit(body="one line\n")
        self.assertEqual(0, self._baseline("--close", "--run", self.run_id("baseline")),
                         self.said)

        staged = json.loads((self.run_dir("baseline") / "staging" / "baseline.json").read_text())
        self.assertEqual(sorted(("wall_time_ms", "outcome", "changes")), sorted(staged))
        self.assertIsInstance(staged["wall_time_ms"], int)
        # The oracle's suite has not been run as a suite, so the only admissible label is UNKNOWN.
        self.assertEqual("UNKNOWN", staged["outcome"])
        self.assertEqual({"files_changed": 1, "insertions": 1, "deletions": 0},
                         staged["changes"])
        # Printed exactly as it is staged: the registry is another repository's, so the person
        # pastes this, and a re-typed copy is a group whose rows no longer agree.
        self.assertEqual(staged, json.loads(self.printed))

    def test_closing_a_baseline_twice_is_refused(self):
        self._open_baseline()
        self._person_commit()
        self.assertEqual(0, self._baseline("--close", "--run", self.run_id("baseline")))
        self.assertEqual(2, self._baseline("--close", "--run", self.run_id("baseline")),
                         "a second close would measure the time since the first")

    def test_a_baseline_is_not_closed_as_a_model_arm(self):
        self._open_baseline()
        code = self.cli(["close-run", "--run", self.run_id("baseline")])
        self.assertEqual(2, code, "a human baseline was pushed as a model arm")

    def test_a_baseline_measures_a_planned_task(self):
        # A baseline is carried onto the rows of a group, and a group is a plan. An unplanned task
        # is an arm of nothing.
        self.assertEqual(2, self._baseline("--repo", str(self.clone), "--task", "adhoc",
                                           "--group", GROUP))

    # ---- what a model arm does with it -------------------------------------------

    def _arm(self, *extra):
        return self.cli(["open-run", "--repo", str(self.clone), "--provider", "claude",
                         "--task", support.TASK, "--scope", support.SCOPE,
                         "--group", GROUP, "--arm", "a", "--arms-planned", "1",
                         "--baseline", "human", *extra])

    def _measured(self):
        """A closed human baseline of this group, staged on this machine."""
        self._open_baseline()
        self._person_commit()
        self.assertEqual(0, self._baseline("--close", "--run", self.run_id("baseline")))
        return json.loads((self.run_dir("baseline") / "staging" / "baseline.json").read_text())

    def test_a_model_arm_does_not_open_before_the_human_arm_has_run(self):
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            code = self._arm()
        self.assertEqual(2, code, "a model arm opened ahead of the human arm of its group")
        self.assertIn("human", noticed.getvalue())
        self.assertEqual([], list(self.state_root.rglob("manifest.json")))

    def test_a_model_arm_carries_the_baseline_this_machine_measured(self):
        measured = self._measured()
        self.assertEqual(0, self._arm())
        self.assertEqual(measured, self.manifest()["human_baseline"])

        self.commit()
        self.place_session()
        self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]))
        row = self.staged_row()
        # Every row of a human-baselined group carries this object identically: the arms are
        # comparable with each other and with the human only if all of them carry the same one.
        self.assertEqual(measured, row["human_baseline"])
        self.assertEqual("human", row["pairing"]["baseline"])

    def test_a_group_record_is_read_where_the_registry_holds_the_baseline(self):
        # The registry is another repository's, so the group's record reaches the harness as a file
        # a person points at.
        record = self.tmp / "group.json"
        record.write_text(json.dumps({
            "group_id": GROUP,
            "human_baseline": {"wall_time_ms": 420000, "outcome": "UNKNOWN",
                               "changes": {"files_changed": 2, "insertions": 9, "deletions": 1}},
        }))
        self.assertEqual(0, self._arm("--group-file", str(record)))
        self.assertEqual(420000, self.manifest()["human_baseline"]["wall_time_ms"])

    def test_an_arm_opened_without_the_baseline_yields_no_row(self):
        # `--no-baseline-required` opens the arm; it does not invent a measurement. The work is
        # pushed and the row is refused, with the reason that says which.
        noticed = io.StringIO()
        self.assertEqual(0, self._arm("--no-baseline-required"))
        self.commit()
        self.place_session()
        with contextlib.redirect_stderr(noticed):
            self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]))
        self.assertIn("human-baseline-absent", noticed.getvalue())
        self.assertEqual([], self.staged_rows())
        # The branch still reached the origin: a run that cannot be recorded is not a run that
        # failed.
        self.assertIn(self.manifest()["branch"],
                      self.git("branch", "--list", cwd=self.tmp / "exeris-agent-harness.git",
                               check=False).stdout)

    def test_the_inbox_validator_accepts_a_paired_row_carrying_a_baseline(self):
        self._measured()
        self.assertEqual(0, self._arm())
        self.commit()
        self.place_session()
        self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]))

        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed), contextlib.redirect_stdout(io.StringIO()) as out:
            code = self.cli(["flush"])
        said = out.getvalue() + noticed.getvalue()
        self.assertEqual(0, code, said)
        self.assertNotIn("::error", said)


if __name__ == "__main__":
    unittest.main()
