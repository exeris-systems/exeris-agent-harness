"""Closing a run: what the identity does on GitHub, and what the record is allowed to say.

`close-run` is where a run becomes two things a person can later join: a draft pull request the
organisation's execution identity authored, and a row describing the run that produced it. The
cases here are the ones that decide whether either is honest.

* *The pull request is a draft and names its owner.* Accountability does not disappear because the
  author is an application; it moves to the `Owner:` line, and a human marks the work ready.
* *Every value on the row is observed or declared, and nothing is defaulted.* A run whose bundle
  pin cannot be read, whose session names a publisher as the model, or whose scope was never
  declared yields no row at all — a convenient default is indistinguishable from a measurement
  once it is in the column.
* *Visibility is recorded at capture time, fail-closed.* A repository whose visibility the producer
  could not establish is `enterprise-private`, because a row filed under the wrong visibility is
  published by the act of filing it.
* *The reference to the stream is pending until a flush resolves it.* The stream has not been
  committed when the row is written, so there is no commit for it to name.
"""

import contextlib
import hashlib
import io
import json
import os
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path


def _first_prompt(path):
    """The text of the fixture's first prompt, read rather than copied."""
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str) and content:
            return content
    raise AssertionError(f"{path} carries no prompt")


def _component(text):
    return (text[:-1] if text.endswith("\n") else text) + "\n"


class CloseRunTest(support.HarnessFixture):
    def _close(self, *extra):
        return self.cli(["close-run", "--run", self.run_id(), *extra])

    def _contract_version(self, value):
        """The version the contract clone publishes, committed as the clone's own state.

        Committed rather than left in the working tree because a flush refuses a clone with
        uncommitted work in it, and a fixture that made one command's case impossible for another
        would be pinning the tests rather than the harness.
        """
        (self.execution / "schemas" / "VERSION").write_text(f"{value}\n")
        self.git("add", "-A", cwd=self.execution)
        self.git("commit", "-m", "chore: the contract version this case reads", cwd=self.execution)

    def _closed_run(self, *extra, model=None, version=None):
        self.open_run()
        self.head = self.commit()
        self.session = self.place_session(model=model, version=version)
        self.assertEqual(0, self._close(*extra), "close-run failed")

    # ---- the record --------------------------------------------------------------

    def test_the_row_says_what_the_run_did_and_nothing_it_did_not(self):
        self._contract_version("9.9.9")
        self._closed_run()
        row = self.staged_row()
        manifest = self.manifest()

        self.assertEqual(manifest["run_id"], row["run_id"])
        self.assertEqual(manifest["started_at"], row["started_at"])
        self.assertEqual({"fingerprint": support.TASK, "domain": support.DOMAIN,
                          "scope": support.SCOPE}, row["workload"])

        # The runtime exposes no dated snapshot for the model that took the turns, so the row
        # carries the alias marked. The bare alias would claim a precision no row has.
        agent = row["agent"]
        # The vendor whose model took the turns, from the provider table — never the name of the
        # client that ran it, which `agent.harness` carries a field below.
        self.assertEqual(support.PROVIDER, agent["provider"])
        self.assertEqual("claude-sonnet-5", agent["model_id"])
        self.assertEqual("unresolved:claude-sonnet-5", agent["model_snapshot"])
        self.assertEqual({"client": "claude-code", "version": "2.1.278"}, agent["harness"])

        state = row["repository_state"]
        self.assertEqual(manifest["repo"], state["repository"])
        self.assertEqual("public", state["visibility"])
        self.assertEqual(manifest["base_sha"], state["commit"])
        self.assertEqual(support.BUNDLE_VERSION, state["bundle_version"])
        self.assertFalse(state["dirty"])

        execution = row["execution"]
        self.assertEqual(4, execution["turns"])
        self.assertEqual(5, execution["tool_calls"])
        self.assertEqual(2, execution["human_prompts"])
        self.assertEqual(1, execution["permission_denials"])
        self.assertEqual([self.head], execution["result_commits"])
        self.assertEqual("full", execution["capture_level"])
        self.assertEqual({"kind": "app", "login": support.BOT_LOGIN}, execution["principal"])
        self.assertGreaterEqual(execution["wall_time_ms"], 0)

        stream = execution["event_stream"]
        self.assertTrue(stream["ref"].endswith(f"/{row['run_id']}.jsonl@pending"), stream["ref"])
        self.assertIn(f"{support.ORG}/exeris-ai-execution-streams/streams/", stream["ref"])
        self.assertEqual(hashlib.sha256(self.session.read_bytes()).hexdigest(), stream["sha256"])
        self.assertEqual(len([line for line in self.session.read_text().splitlines()
                              if line.strip()]), stream["event_count"])

        # Token counts, and no currency figure in any mode: under a subscription there is no
        # per-run price and an imputed one is indistinguishable from a reported one.
        self.assertEqual(support.CREDENTIAL, row["accounting"]["mode"])
        self.assertEqual({"input_tokens": 1500, "output_tokens": 150,
                          "cache_read_tokens": 15000, "cache_write_tokens": 350},
                         row["accounting"]["usage"])
        self.assertNotIn("provider_reported_cost", row["accounting"])

        # A construction run is judged by the benchmark, whose suite has not been run as a suite —
        # so the calibration is `not-run` and the outcome can only be UNKNOWN.
        self.assertEqual({"id": "scb", "version": "1.3",
                          "calibration": {"suite": "oracle-selftest", "status": "not-run",
                                          "result": "not-run"}}, row["oracle"])
        self.assertEqual("UNKNOWN", row["outcome"])

        # The contract's own version, not this producer's: the clone carries a value no producer
        # would have as a constant, so a row repeating it is a row that read it.
        self.assertEqual("9.9.9", row["instrument"]["capture_version"])
        # And the fence is the id the register resolves for this producer at this client version,
        # not one minted from the day the run happened.
        self.assertEqual(support.FENCE, row["instrument"]["fence"])

    def test_the_system_prompt_hash_is_over_the_three_things_that_instructed_the_run(self):
        self._closed_run()
        prompt = _first_prompt(self.session)
        expected = hashlib.sha256(b"".join(_component(part).encode("utf-8") for part in (
            hashlib.sha256(prompt.encode("utf-8")).hexdigest(),  # the prompt, hashed where read
            "",                                                  # no routine is configured
            support.AGENTS_MD,                                   # the agent file at the base commit
        ))).hexdigest()
        self.assertEqual(expected, self.staged_row()["agent"]["system_prompt_sha256"])

        # The prompt's own text is nowhere in the record: a count is metadata, the thing counted
        # is not.
        self.assertNotIn(prompt.strip(),
                         self.staged_rows()[0].read_text(encoding="utf-8"))

    def test_the_stream_is_staged_beside_the_row_rather_than_referenced_where_it_lies(self):
        self._closed_run()
        staged = self.staged_streams()
        self.assertEqual(1, len(staged), staged)
        self.assertEqual(self.session.read_bytes(), staged[0].read_bytes())
        self.assertIn(f"/{self.run_id()}.jsonl", str(staged[0]))

    def test_a_documentation_run_names_the_oracle_that_judges_documentation(self):
        # Which oracle judged a run is looked up by the domain the configuration declares, in the
        # contract's own spelling. `docs-guardrails` is versioned by the bundle in force, which is
        # the rules the run was subject to — the same value the row carries for the checkout.
        self.configure_domain(support.DOCUMENTATION_DOMAIN)
        self._closed_run()
        row = self.staged_row()

        self.assertEqual(support.DOCUMENTATION_DOMAIN, row["workload"]["domain"])
        self.assertEqual({"id": "docs-guardrails", "version": support.BUNDLE_VERSION,
                          "calibration": {"suite": "docs-mutation-v1", "status": "not-run",
                                          "result": "not-run"}}, row["oracle"])

    def test_the_bundle_pin_is_the_one_the_run_found_and_not_the_one_it_left(self):
        # The pin is what the run was subject to. A run that edited the manifest wrote something
        # the row would otherwise report as the rules it was judged by.
        self.open_run()
        self.head = self.commit(name=".agents/manifest.yaml",
                                message="chore: what this run wrote over the pin")
        self.place_session()
        self.assertEqual(0, self._close())
        self.assertEqual(support.BUNDLE_VERSION,
                         self.staged_row()["repository_state"]["bundle_version"])

    # ---- what the identity does on GitHub ----------------------------------------

    def test_the_pull_request_is_a_draft_that_names_its_owner_and_its_run(self):
        self._closed_run()
        posted = self.posted_pulls()
        self.assertEqual(1, len(posted), posted)
        body = posted[0]

        self.assertTrue(body["draft"], "a run opened a pull request that was ready for review")
        self.assertEqual(self.manifest()["branch"], body["head"])
        self.assertEqual("main", body["base"])
        self.assertIn(f"Owner: @{support.OWNER_LOGIN}", body["body"])
        self.assertIn(f"Exeris-Run: {self.run_id()}", body["body"])
        for heading in ("Motivation:", "Modification:", "Result:", "## Classification",
                        "## Verification"):
            self.assertIn(heading, body["body"])
        self.assertIn("Scope class:", body["body"])

    def test_the_branch_reaches_the_origin(self):
        self._closed_run()
        branch = self.manifest()["branch"]
        pushed = self.git("rev-parse", f"refs/heads/{branch}", cwd=self.origin).stdout.strip()
        self.assertEqual(self.head, pushed, "the run's branch is not on the origin")

    def test_no_pr_pushes_and_asks_for_nothing(self):
        self.open_run()
        self.commit()
        self.place_session()
        self.assertEqual(0, self._close("--no-pr"))
        self.assertEqual([], self.posted_pulls())

    def test_a_commit_the_hook_did_not_stamp_is_counted_unattributed(self):
        self.open_run()
        tree = self.worktree()
        (tree / "unstamped.txt").write_text("a line\n")
        env = dict(os.environ)
        env["GIT_CONFIG_GLOBAL"] = str(self.run_dir() / "gitconfig")
        self.git("add", "unstamped.txt", cwd=tree)
        # `--no-verify` is how a commit arrives without the trailer the record claims it by.
        self.git("-c", f"core.hooksPath={self.tmp / 'no-hooks'}", "commit", "--no-verify",
                 "-m", "chore: a commit nothing stamped", cwd=tree)
        self.place_session()

        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self.assertEqual(0, self._close())
        self.assertIn("unattributed-commit", noticed.getvalue())

    # ---- the refusals ------------------------------------------------------------

    def test_a_publisher_is_never_recorded_as_the_model(self):
        # The bot is the pen, never the agent: a row naming one of the organisation's writing
        # identities in `agent.*` puts an identity into the column a comparison groups models by.
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self._closed_run(model="exeris-agent")
        self.assertEqual([], self.staged_rows())
        self.assertIn("publisher-named-as-agent", noticed.getvalue())
        # The run still closed: its branch is pushed and its pull request is open. What is missing
        # is the observation, not the work.
        self.assertEqual(1, len(self.posted_pulls()))

    def test_a_checkout_whose_base_commit_pins_no_bundle_yields_no_row(self):
        # No pin is a property of the commit the run started from, so that is where it is made:
        # a manifest removed inside the run would be what the run wrote.
        self.git("rm", "-q", ".agents/manifest.yaml", cwd=self.clone)
        self.git("commit", "-m", "chore: a checkout that pins no bundle", cwd=self.clone)
        self.git("push", "origin", "main", cwd=self.clone)

        self.open_run()
        self.commit()
        self.place_session()
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self.assertEqual(0, self._close())
        self.assertEqual([], self.staged_rows())
        self.assertIn("bundle-pin-absent", noticed.getvalue())

    def test_an_agent_file_in_the_tree_that_cannot_be_read_yields_no_row(self):
        # Absent from the tree at the base commit is a state the hash records; present and
        # unrecoverable is a component the producer cannot recover exactly, and a hash over the
        # part it could recover would be a digest of something the run was never subject to.
        self.open_run()
        base = self.manifest()["base_sha"]
        blob = self.git("rev-parse", f"{base}:{support.AGENTS_FILE}",
                        cwd=self.clone).stdout.strip()
        loose = self.clone / ".git" / "objects" / blob[:2] / blob[2:]
        if not loose.is_file():
            self.skipTest(f"{blob} is not a loose object; there is nothing to take away")
        self.commit()
        self.place_session()
        loose.unlink()

        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self.assertEqual(0, self._close())
        self.assertEqual([], self.staged_rows())
        self.assertIn("agents-file-unreadable", noticed.getvalue())

    def test_a_domain_no_oracle_is_published_for_yields_no_row(self):
        # `docs` is a domain no reader can look an oracle up for. A producer that translated it to
        # the contract's spelling would be deciding what the row was judged by.
        self.configure_domain("docs")
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self._closed_run()
        self.assertEqual([], self.staged_rows())
        self.assertIn("oracle-unmappable", noticed.getvalue())

    def test_a_contract_version_the_producer_cannot_read_yields_no_row(self):
        # `instrument.capture_version` is the contract's own version, read from the contract. A
        # value that is not one is not a version this row could have been written against.
        self._contract_version("not-a-version")
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self._closed_run()
        self.assertEqual([], self.staged_rows())
        self.assertIn("capture-version-unreadable", noticed.getvalue())

    def test_a_client_version_no_fence_is_registered_for_yields_no_row(self):
        # A fence id is resolvable or it is nothing. A client version the register does not carry
        # is registered before its first row, not after it.
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self._closed_run(version="9.9.9")
        self.assertEqual([], self.staged_rows())
        self.assertIn("fence-unregistered", noticed.getvalue())

    def test_a_run_with_no_session_of_its_own_yields_no_row(self):
        self.open_run()
        self.commit()
        noticed = io.StringIO()
        with contextlib.redirect_stderr(noticed):
            self.assertEqual(0, self._close())
        self.assertEqual([], self.staged_rows())
        self.assertIn("session-not-found", noticed.getvalue())

    def test_a_visibility_the_producer_cannot_establish_is_enterprise_private(self):
        self._refusing_gh()
        self._closed_run()
        self.assertEqual([], self.staged_rows("public"))
        self.assertEqual("enterprise-private",
                         self.staged_row("enterprise-private")["repository_state"]["visibility"])

    def test_a_private_repository_stages_a_private_row(self):
        self.gh_state.mkdir(parents=True, exist_ok=True)
        (self.gh_state / "visibility").write_text("private")
        self._closed_run()
        self.assertEqual([], self.staged_rows("public"))
        self.assertEqual("enterprise-private",
                         self.staged_row("enterprise-private")["repository_state"]["visibility"])

    def test_closing_a_run_a_second_time_is_refused(self):
        self._closed_run()
        self.assertEqual(2, self._close(), "a run was closed twice")
        self.assertEqual(1, len(self.posted_pulls()), "a second close asked for a second pull "
                                                      "request")

    def test_the_worktree_is_given_back_when_asked_and_the_staging_survives_it(self):
        self.open_run()
        self.commit()
        self.place_session()
        tree = self.worktree()
        self.assertEqual(0, self._close("--remove-worktree"))
        self.assertFalse(tree.exists(), "the run's tree was kept")
        self.assertEqual(1, len(self.staged_rows()))

    def _refusing_gh(self):
        """A `gh` that answers every call but the one asking what a repository is."""
        (self.bin / "gh").write_text(
            "#!/usr/bin/env python3\n"
            "import re, sys\n"
            f"sys.path.insert(0, {str(support.FIXTURES)!r})\n"
            "import fake_gh\n"
            f"fake_gh.STATE = {str(self.gh_state)!r}\n"
            "argv = sys.argv[1:]\n"
            "endpoint, method, fields = fake_gh._parse(argv)\n"
            "if re.fullmatch(r'repos/[^/]+/[^/]+', (endpoint or '').split('?')[0]):\n"
            "    sys.stderr.write('gh: Resource not accessible by integration (HTTP 403)\\n')\n"
            "    sys.exit(1)\n"
            "sys.exit(fake_gh.main(argv))\n")
        (self.bin / "gh").chmod(0o755)


if __name__ == "__main__":
    unittest.main()
