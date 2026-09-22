"""Flushing: carrying what a run staged into the repositories that keep it.

`flush` is the one command that runs as the person rather than as the execution identity. That is
not a fallback credential — the hands are deliberately not installed on the repositories that hold
records — it is a sign-off: a human vouches for a batch of rows exactly as a human merges them.

The cases here are the ones that decide whether the batch is trustworthy.

* *The validator runs before the pull request, not after it.* A red validator commits nothing and
  asks for nothing, because a defect is fixed at the producer rather than quarantined in the
  dataset.
* *The pending reference is resolved once, in staging.* A row's reference to its stream cannot name
  a commit that does not exist yet; staging is the producer's own, and the rule that a record is
  appended and never rewritten begins where records are kept.
* *One visibility per inbox.* A row that does not match stays where it is and is counted; filing it
  would publish it by the act of filing.
* *A second flush is not a second pull request.* A day's batch is appended to.
"""

import contextlib
import datetime
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


class FlushTest(support.HarnessFixture):
    def _staged_run(self, **kwargs):
        self.open_run()
        self.head = self.commit()
        self.session = self.place_session(**kwargs)
        self.assertEqual(0, self.cli(["close-run", "--run", self.run_id()]), "close-run failed")

    def _flush(self):
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed), contextlib.redirect_stdout(io.StringIO()) as out:
            code = self.cli(["flush"])
        self.said = out.getvalue() + noticed.getvalue()
        return code

    def _on_branch(self, branch=None):
        """The rows the pushed inbox branch holds, read from the origin rather than the clone.

        The origin is where a batch has to have arrived: flush leaves the person's own clone on the
        branch it started on, so what is in that working tree afterwards says nothing.
        """
        origin = self.tmp / "exeris-ai-execution.git"
        shown = self.git("ls-tree", "-r", "--name-only",
                         f"refs/heads/{branch or f'inbox/harness/{self._today()}'}",
                         cwd=origin, check=False)
        return sorted(line for line in shown.stdout.splitlines()
                      if line.startswith("inbox/") and "/runs/" in line)

    def _today(self):
        return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")

    # ---- the batch ---------------------------------------------------------------

    def test_the_rows_reach_the_inbox_under_one_pull_request(self):
        self._staged_run()
        row_id = self.run_id()
        date = self.staged_row()["started_at"][:10]
        self.assertEqual(0, self._flush())

        branch = f"inbox/harness/{self._today()}"
        self.assertIn(f"inbox/{date}/runs/{row_id}.json", self._on_branch(branch))

        posted = [body for endpoint, method, body in self.gh_calls()
                  if method == "POST" and str(endpoint).endswith("/pulls")
                  and body.get("head") == branch]
        self.assertEqual(1, len(posted), posted)
        body = posted[0]
        self.assertEqual("main", body["base"])
        self.assertIn("harness rows", body["title"])
        for heading in ("Motivation:", "Modification:", "Result:", "## Classification",
                        "## Verification"):
            self.assertIn(heading, body["body"])
        # The line is required on a pull request the execution identity authored and forbidden on
        # every other. This one is the person's.
        self.assertNotIn("Owner: @", body["body"])
        self.assertNotIn("<runtime hot path", body["body"],
                         "a pull request opened under a person's own identity left the "
                         "classification unanswered")

    def test_the_pending_reference_is_resolved_to_the_commit_that_carries_the_stream(self):
        self._staged_run()
        self.assertIn("@pending", self.staged_rows()[0].read_text(),
                      "the reference named a commit before the stream was committed")

        self.assertEqual(0, self._flush())

        sha = self.git("rev-parse", "HEAD", cwd=self.streams).stdout.strip()
        ref = self.staged_row()["execution"]["event_stream"]["ref"]
        self.assertTrue(ref.endswith(f"@{sha}"), f"{ref} does not name {sha}")

        carried = sorted(p.relative_to(self.streams).as_posix()
                         for p in (self.streams / "streams").rglob("*.jsonl"))
        # The reference is a path inside the repository it names, so the two are the same string.
        self.assertEqual([f"streams/{ref.split('/streams/', 1)[1].split('@')[0]}"], carried)
        self.assertEqual(self.session.read_bytes(),
                         (self.streams / carried[0]).read_bytes())

        index = json.loads((self.streams / "index.json").read_text())
        self.assertEqual(1, len(index), index)
        entry = index[0]
        self.assertEqual(self.run_id(), entry["run_id"])
        self.assertEqual("harness", entry["producer"])
        self.assertEqual(carried[0], entry["path"])
        self.assertEqual(self.staged_row()["execution"]["event_stream"]["sha256"],
                         entry["sha256"])
        self.assertEqual(self.staged_row()["execution"]["event_stream"]["event_count"],
                         entry["event_count"])

    def test_a_second_flush_adds_to_the_standing_batch_rather_than_opening_another(self):
        self._staged_run()
        self.assertEqual(0, self._flush())

        # The next day, with the first batch still unmerged. A branch cut by date would carry the
        # same rows a second time, and two open pull requests would propose the same files.
        import harness.cli
        tomorrow = (datetime.date.fromisoformat(self._today())
                    + datetime.timedelta(days=1)).isoformat()
        self.addCleanup(setattr, harness.cli, "_today", harness.cli._today)
        harness.cli._today = lambda: tomorrow
        self.assertEqual(0, self._flush())

        posted = [body for endpoint, method, body in self.gh_calls()
                  if method == "POST" and str(endpoint).endswith("/pulls")
                  and str(body.get("head", "")).startswith("inbox/")]
        self.assertEqual(1, len(posted), "a second flush asked for a second pull request")
        self.assertEqual(f"inbox/harness/{self._today()}", posted[0]["head"])
        self.assertEqual([], self._on_branch(f"inbox/harness/{tomorrow}"),
                         "a second branch was cut while the first batch was still open")
        # And the day's rows are still exactly the day's rows.
        self.assertEqual(1, len(self._on_branch()))

    def test_the_batch_is_committed_as_the_person_and_not_as_the_identity(self):
        # The terminal a run was worked in is where the next command is typed, and `open-run`'s
        # whole handover is `source <run>/env`. A flush that inherited it would commit the rows and
        # open their pull request as the identity whose runs the batch describes.
        self._staged_run()
        for name, value in {
            "GH_TOKEN": support.FAKE_TOKEN,
            "GH_CONFIG_DIR": str(self.run_dir() / "gh"),
            "GIT_CONFIG_GLOBAL": str(self.run_dir() / "gitconfig"),
            "GIT_AUTHOR_NAME": support.BOT_LOGIN,
            "GIT_AUTHOR_EMAIL": f"{support.BOT_USER_ID}+{support.BOT_LOGIN}@users.noreply."
                                f"github.com",
            "GIT_COMMITTER_NAME": support.BOT_LOGIN,
            "GIT_COMMITTER_EMAIL": f"{support.BOT_USER_ID}+{support.BOT_LOGIN}@users.noreply."
                                   f"github.com",
        }.items():
            self.set_env(name, value)

        self.assertEqual(0, self._flush())

        origin = self.tmp / "exeris-ai-execution.git"
        author = self.git("log", "-1", "--format=%an <%ae>",
                          f"refs/heads/inbox/harness/{self._today()}", cwd=origin).stdout.strip()
        self.assertEqual("A Maintainer <maintainer@example.invalid>", author)
        self.assertNotIn(support.BOT_LOGIN, author)

    def test_every_variable_a_run_binds_is_one_the_person_s_flush_drops(self):
        # The list flush subtracts is stated once; a variable added to a run's environment and not
        # to it would reach the person's own git and `gh` unnoticed.
        from harness import runstate
        bound = runstate.env_values(str(self.tmp), run_id="01M00000000000000000000000",
                                    token=support.FAKE_TOKEN, user_name=support.BOT_LOGIN,
                                    user_email="bot@example.invalid")
        self.assertEqual(set(), set(bound) - set(runstate.RUN_ENV_KEYS))

    # ---- the refusals ------------------------------------------------------------

    def test_a_red_validator_commits_nothing_and_opens_no_pull_request(self):
        self._staged_run()
        broken = self.staged_rows()[0]
        row = json.loads(broken.read_text())
        # A row the schema requires an oracle of, without one: the validator reports it as a
        # producer defect, which is what "reported back to the producer" means operationally.
        row.pop("oracle")
        broken.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")

        self.assertEqual(1, self._flush(), "a red validator let the batch through")
        self.assertEqual([], self._on_branch(), "a rejected batch reached the inbox")
        self.assertEqual([], [body for endpoint, method, body in self.gh_calls()
                              if method == "POST" and str(endpoint).endswith("/pulls")
                              and str(body.get("head", "")).startswith("inbox/")])
        self.assertIn("oracle", self.said)

    def test_an_enterprise_private_row_stays_in_staging_and_is_counted(self):
        self.gh_state.mkdir(parents=True, exist_ok=True)
        (self.gh_state / "visibility").write_text("private")
        self._staged_run()
        self.assertEqual(1, len(self.staged_rows("enterprise-private")))

        self.assertEqual(0, self._flush())
        self.assertEqual([], self._on_branch(),
                         "a private row was filed in the public inbox")
        self.assertEqual(1, len(self.staged_rows("enterprise-private")),
                         "the private row left staging")
        self.assertIn("enterprise-private", self.said)
        self.assertEqual([], [body for endpoint, method, body in self.gh_calls()
                              if method == "POST" and str(endpoint).endswith("/pulls")
                              and str(body.get("head", "")).startswith("inbox/")])

    def test_a_shell_still_bound_to_a_run_is_refused(self):
        self._staged_run()
        self.set_env("EXERIS_RUN", self.run_id())
        self.assertEqual(2, self._flush(), "a shell holding a run's identity signed off a batch")
        self.assertEqual([], self._on_branch())
        self.assertEqual([], [body for endpoint, method, body in self.gh_calls()
                              if method == "POST" and str(endpoint).endswith("/pulls")
                              and str(body.get("head", "")).startswith("inbox/")])

    def test_a_row_the_target_inbox_does_not_declare_stays_in_staging(self):
        # Which inbox this is, the inbox declares. A public row carried into an inbox that says
        # enterprise-private is filed under the wrong visibility, and rule 1 would have been
        # checked against a value the producer invented.
        self._staged_run()
        (self.execution / "inbox" / "inbox.json").write_text(
            json.dumps({"visibility": "enterprise-private"}, indent=2) + "\n")
        self.git("add", "-A", cwd=self.execution)
        self.git("commit", "-m", "chore: the inbox says which inbox it is", cwd=self.execution)

        self.assertEqual(0, self._flush())
        self.assertEqual([], self._on_branch(), "a public row was filed in an inbox that declares "
                                                "enterprise-private")
        self.assertEqual(1, len(self.staged_rows()), "the row left staging")
        self.assertIn("enterprise-private", self.said)
        self.assertEqual([], [body for endpoint, method, body in self.gh_calls()
                              if method == "POST" and str(endpoint).endswith("/pulls")
                              and str(body.get("head", "")).startswith("inbox/")])

    def test_a_row_whose_stream_is_not_in_the_repository_keeps_its_pending_mark(self):
        # The reference is what makes the row's counts checkable against the stream they were
        # taken from. Resolved to a commit the path does not exist under, it names nothing.
        self._staged_run()
        self.staged_streams()[0].unlink()

        self.assertEqual(0, self._flush())
        self.assertIn("@pending", self.staged_rows()[0].read_text(),
                      "a row was resolved to a commit that does not carry its stream")
        self.assertEqual([], self._on_branch(), "a row naming no stream reached the inbox")
        self.assertEqual([], [body for endpoint, method, body in self.gh_calls()
                              if method == "POST" and str(endpoint).endswith("/pulls")
                              and str(body.get("head", "")).startswith("inbox/")])

    def test_a_clone_with_uncommitted_work_is_refused(self):
        self._staged_run()
        (self.execution / "inbox" / "a-file-somebody-was-editing.json").write_text("{}\n")
        self.assertEqual(2, self._flush(), "flush committed into a tree it did not own")
        self.assertEqual([], self._on_branch())


if __name__ == "__main__":
    unittest.main()
