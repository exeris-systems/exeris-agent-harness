"""Opening a run: the worktree is the boundary, and the boundary is made of git configuration.

`open-run` is where the execution identity is established. Everything committed inside the
worktree it creates belongs to that run, so the properties asserted here are the ones that make
that sentence true rather than aspirational:

* the tree is a fresh branch on the remote's default, so the run's base is a commit that exists
  upstream and the run record can name it;
* the identity is worktree-scoped, not shell-scoped, so it cannot follow the agent out of the
  tree, and it is the App's noreply form, which is what GitHub attributes to the App;
* ssh is closed — rewritten to https and, behind that, given a program that cannot connect — so
  the one transport that bypasses the credential helper entirely is not available;
* the helper answers for the organisation's repositories and for nothing else, so a token minted
  for this run is not a general-purpose credential the run can spend elsewhere;
* the boundary is bound at worktree scope, which git reads after a repository's own config, so a
  clone that configures a credential helper, a hooks path or a signing key does not reach into the
  run through the one file the run's global config cannot displace;
* a credential the helper does not hold is a failure and never a question put to a person, which is
  the closure under the helper rather than a property of the shell a run was opened from;
* the run's environment drops the credentials of that shell before it adds the run's own, because
  a variable the harness never sets is a variable the harness never closed;
* every commit carries exactly one run trailer, including after an amend, because the trailer is
  the only link from the commit graph back to the record that observed it;
* an ad-hoc run cannot join a preregistered comparison, because a group whose arms were not
  planned in advance is a query dressed as a design.

The whole fixture is a temporary bare origin and a temporary clone; no network, no real
credential, and a throwaway key that exists for the length of one test.
"""

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CLIENT_ID = "Iv23liTESTclientid"
INSTALLATION_ID = 87654321
BOT_USER_ID = 330540144
BOT_LOGIN = "exeris-agent[bot]"
ORG = "exeris-systems"
OWNER_LOGIN = "a-maintainer"
SCOPES = ("identity", "capture")
FAKE_TOKEN = "ghs_TESTtokenTESTtokenTESTtoken0123"
FAKE_EXPIRY = "2026-01-01T00:00:00Z"
BOT_EMAIL = f"{BOT_USER_ID}+{BOT_LOGIN}@users.noreply.github.com"
# The branch carries eight characters of the run id. Which eight is the harness's to choose —
# the leading half sorts, the trailing half is the random one — so the case pins the shape and
# the length, and the assertion below pins that the characters come from the id.
CROCKFORD_8 = re.compile(r"^agent/[0-9A-HJKMNP-TV-Z]{8}$", re.IGNORECASE)


class _Token(dict):
    """The minted token, readable as a mapping or as an object."""

    @property
    def token(self):
        return self["token"]

    @property
    def expires_at(self):
        return self["expires_at"]


def _entry_point(module):
    for name in ("main", "run"):
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    raise AssertionError("harness.cli exposes no callable named main/run")


def _slug(path):
    """The per-worktree identity a session log is filed under: separators become dashes."""
    return re.sub(r"[/.]", "-", str(path))


def _principal(manifest):
    for holder in (manifest, manifest.get("execution") or {}):
        if isinstance(holder, dict) and isinstance(holder.get("principal"), dict):
            return holder["principal"]
    raise AssertionError(f"the manifest names no principal: {sorted(manifest)}")


def _pairing(manifest):
    for holder in (manifest, manifest.get("workload") or {}):
        if isinstance(holder, dict) and isinstance(holder.get("pairing"), dict):
            return holder["pairing"]
    raise AssertionError(f"the manifest records no pairing: {sorted(manifest)}")


class OpenRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-open-run-test-"))
        self.addCleanup(self._cleanup)

        # The origin is named for the repository, because the harness reads the clone's origin to
        # find the `[repos.<name>]` entry: an origin named anything else would look up a table
        # nobody configured, and the vocabulary check below would pass by never happening.
        self.origin = self.tmp / "exeris-agent-harness.git"
        self.clone = self.tmp / "clone"
        self.state_root = self.tmp / "state"
        self.state_root.mkdir()

        self._build_origin()

        self.key_path = self.tmp / "throwaway.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
             "-out", str(self.key_path)],
            check=True, capture_output=True,
        )
        self.key_path.chmod(0o600)

        self.config_path = self.tmp / "config.toml"
        self.config_path.write_text(
            "[github]\n"
            f'client_id = "{CLIENT_ID}"\n'
            f"installation_id = {INSTALLATION_ID}\n"
            f"bot_user_id = {BOT_USER_ID}\n"
            f'bot_login = "{BOT_LOGIN}"\n'
            f'private_key = "{self.key_path}"\n'
            f'owner_login = "{OWNER_LOGIN}"\n'
            f'org = "{ORG}"\n'
            f'execution_repo = "{ORG}/exeris-ai-execution"\n'
            f'streams_repo = "{ORG}/exeris-ai-execution-streams"\n'
            "\n"
            # The bare repository name is the address: it is what `--repo <owner/name>` and the
            # clone's own origin both reduce to, and a table the harness does not find is a
            # vocabulary that was configured and never checked.
            '[repos."exeris-agent-harness"]\n'
            'domain = "construction"\n'
            f"scope = {list(SCOPES)!r}\n"
            "\n"
            # The arm a run is opened under. The vendor the row names and the ledger it is billed
            # to are the arm's, which is why they are declared here and not beside the repository.
            "[providers.claude]\n"
            'provider = "anthropic"\n'
            'credential = "subscription"\n'
        )

        # The maintainer's own configuration directory is never read: the config path is given
        # explicitly, and HOME and the XDG roots move into the tempdir so that a lookup falling
        # back to a default path still cannot leave it.
        for name, value in {
            "EXERIS_AGENT_CONFIG": str(self.config_path),
            "EXERIS_AGENT_STATE": str(self.state_root),
            "HOME": str(self.tmp / "home"),
            "XDG_CONFIG_HOME": str(self.tmp / "home" / ".config"),
            "XDG_STATE_HOME": str(self.tmp / "home" / ".local" / "state"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }.items():
            self._set_env(name, value)
        (self.tmp / "home").mkdir(parents=True, exist_ok=True)

    def _cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _set_env(self, name, value):
        previous = os.environ.get(name)
        os.environ[name] = value
        self.addCleanup(
            lambda n=name, p=previous: os.environ.__setitem__(n, p)
            if p is not None
            else os.environ.pop(n, None)
        )

    # ---- fixture -----------------------------------------------------------------

    def _fixture_env(self):
        env = dict(os.environ)
        env.update({
            "GIT_CONFIG_GLOBAL": str(self.tmp / "no-such-gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        })
        return env

    def _git(self, *args, cwd=None, check=True):
        done = subprocess.run(
            ["git", *args], cwd=str(cwd) if cwd else None, env=self._fixture_env(),
            capture_output=True, text=True,
        )
        if check and done.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {done.stdout}{done.stderr}")
        return done

    def _build_origin(self):
        seed = self.tmp / "seed"
        self._git("init", "--bare", "-b", "main", str(self.origin))
        self._git("init", "-b", "main", str(seed))
        (seed / "README.md").write_text("a repository the harness can open a run against\n")
        self._git("add", "-A", cwd=seed)
        self._git("commit", "-m", "seed", cwd=seed)
        self._git("remote", "add", "origin", str(self.origin), cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        self._git("clone", str(self.origin), str(self.clone))
        self.origin_main = self._git(
            "rev-parse", "refs/heads/main", cwd=self.origin
        ).stdout.strip()

    # ---- the harness under test --------------------------------------------------

    def _fake_mint(self, *args, **kwargs):
        return _Token(token=FAKE_TOKEN, expires_at=FAKE_EXPIRY)

    def _cli(self, argv):
        """Run one subcommand in process, with the network call to GitHub replaced."""
        import harness.cli
        import harness.token

        saved = []
        for module in (harness.token, harness.cli):
            if hasattr(module, "mint"):
                saved.append((module, module.mint))
                module.mint = self._fake_mint
        self.addCleanup(lambda: [setattr(m, "mint", fn) for m, fn in saved])

        try:
            code = _entry_point(harness.cli)(list(argv))
        except SystemExit as stop:
            code = stop.code
        return 0 if code is None else int(code)

    def _open_run(self, *extra):
        code = self._cli([
            "open-run", "--repo", str(self.clone), "--provider", "claude", *extra,
        ])
        self.assertEqual(0, code, f"open-run refused: exit {code}")
        return self.run_dir()

    def run_dir(self):
        manifests = sorted(self.state_root.rglob("manifest.json"))
        self.assertEqual(1, len(manifests), f"expected one run under the state root: {manifests}")
        return manifests[0].parent

    def manifest(self):
        return json.loads((self.run_dir() / "manifest.json").read_text())

    def worktree(self):
        run = self.run_dir()
        if (run / "wt" / ".git").exists():
            return run / "wt"
        found = [p.parent for p in run.rglob(".git")]
        self.assertEqual(1, len(found), f"expected one worktree under {run}: {found}")
        return found[0]

    def run_id(self):
        return self.manifest().get("run_id") or self.run_dir().name

    def _in_run_env(self, command, cwd=None, timeout=90, carrying=None):
        """Run a command the way the printed instruction does: source the env, enter the tree.

        The environment handed in is the shell's, `carrying` and all: nothing here closes a door
        the run is supposed to close for itself, so a prompt the harness failed to disable is a
        prompt this fixture will hang on rather than one it quietly answered.
        """
        env_file = self.run_dir() / "env"
        self.assertTrue(env_file.is_file(), f"no environment file at {env_file}")
        script = (
            "set -a\n"
            f'. "{env_file}"\n'
            "set +a\n"
            f'cd "{cwd or self.worktree()}"\n'
            f"{command}\n"
        )
        base = dict(os.environ)
        base.update(carrying or {})
        try:
            return subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True, env=base, timeout=timeout,
            )
        except subprocess.TimeoutExpired as expired:
            return subprocess.CompletedProcess(
                expired.cmd, 124, expired.stdout or "", expired.stderr or "",
            )

    # ---- cases -------------------------------------------------------------------

    def test_worktree_is_a_fresh_branch_on_the_remote_default(self):
        self._open_run("--task", "adhoc")
        worktree = self.worktree()
        head = self._git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        self.assertEqual(self.origin_main, head, "the run does not start from origin/main")

        branch = self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=worktree).stdout.strip()
        self.assertRegex(branch, CROCKFORD_8)
        self.assertIn(
            branch[len("agent/"):].upper(), self.run_id(),
            f"branch {branch} names no part of run {self.run_id()}",
        )

    def test_the_identity_is_bound_to_the_worktree(self):
        self._open_run("--task", "adhoc")
        worktree = self.worktree()
        email = self._git("config", "--worktree", "--get", "user.email", cwd=worktree).stdout.strip()
        name = self._git("config", "--worktree", "--get", "user.name", cwd=worktree).stdout.strip()
        # The noreply form carries the App's *bot user* id, which is what GitHub resolves to the
        # App's avatar and account; the App id is a different number and resolves to nothing.
        self.assertEqual(BOT_EMAIL, email)
        self.assertEqual(BOT_LOGIN, name)

    def test_an_ssh_remote_resolves_to_https(self):
        self._open_run("--task", "adhoc")
        self._in_run_env(f'git remote set-url origin "git@github.com:{ORG}/a-repo.git"')
        resolved = self._in_run_env("git remote get-url origin")
        self.assertEqual(0, resolved.returncode, resolved.stderr)
        self.assertEqual(
            f"https://github.com/{ORG}/a-repo.git", resolved.stdout.strip(),
            "an ssh remote is not rewritten, so it would bypass the credential helper",
        )

    def test_a_push_over_ssh_fails(self):
        self._open_run("--task", "adhoc")
        pushed = self._in_run_env('git push "git@github.com:someone/elsewhere.git" HEAD 2>&1')
        self.assertNotEqual(
            0, pushed.returncode,
            "a run pushed over an ssh remote; the rewrite and the ssh program are both open",
        )
        # 124 is the fixture's timeout: a push that hangs is a push waiting for a person to type a
        # password, which is the fallback the harness has to make impossible rather than slow.
        self.assertNotEqual(124, pushed.returncode,
                            "the push stopped to ask for a credential instead of failing")

    def test_every_commit_carries_exactly_one_run_trailer(self):
        self._open_run("--task", "adhoc")
        run_id = self.run_id()

        made = self._in_run_env(
            'printf "a line\\n" > change.txt && git add change.txt && git commit -m "a change" 2>&1'
        )
        self.assertEqual(0, made.returncode, made.stdout + made.stderr)
        self.assertEqual([f"Exeris-Run: {run_id}"], self._trailers())

        amended = self._in_run_env("git commit --amend --no-edit 2>&1")
        self.assertEqual(0, amended.returncode, amended.stdout + amended.stderr)
        # The trailer is written by a hook that runs on the amend as well, so idempotence is the
        # property, not "the hook runs once".
        self.assertEqual([f"Exeris-Run: {run_id}"], self._trailers())

    def _trailers(self):
        shown = self._in_run_env("git log -1 --format=%B")
        self.assertEqual(0, shown.returncode, shown.stderr)
        return [line.strip() for line in shown.stdout.splitlines()
                if line.strip().startswith("Exeris-Run:")]

    def test_the_manifest_records_principal_task_and_worktree_slug(self):
        self._open_run("--task", "adhoc")
        manifest = self.manifest()

        # An ad-hoc run is named by its own id and by nothing else: the task text is never
        # written down, which is what makes the class non-joinable.
        self.assertEqual(f"adhoc:{self.run_id()}", manifest.get("task"))

        principal = _principal(manifest)
        self.assertEqual({"app", BOT_LOGIN}, set(principal.values()), principal)

        slug = manifest.get("cwd_slug")
        worktree = self.worktree()
        self.assertIn(slug, {_slug(worktree), _slug(worktree.resolve())}, slug)

    def test_the_minted_token_is_stored_owner_only(self):
        run = self._open_run("--task", "adhoc")
        token_file = run / "token"
        self.assertTrue(token_file.is_file(), f"no token at {token_file}")
        self.assertIn(FAKE_TOKEN, token_file.read_text())
        self.assertEqual(0o600, stat.S_IMODE(token_file.stat().st_mode))

    def test_an_adhoc_run_cannot_join_a_preregistered_group(self):
        code = self._cli([
            "open-run", "--repo", str(self.clone), "--provider", "claude",
            "--task", "adhoc", "--group", "G", "--arm", "a", "--arms-planned", "1",
            "--baseline", "none",
        ])
        self.assertEqual(2, code, "an ad-hoc run was admitted into a preregistered comparison")
        self.assertEqual([], list(self.state_root.rglob("manifest.json")),
                         "a refused run left state behind")

    def test_a_registered_task_records_the_group_it_was_planned_into(self):
        self._open_run(
            "--task", "reg:T1", "--group", "G", "--arm", "a",
            "--arms-planned", "1", "--baseline", "none",
        )
        manifest = self.manifest()
        self.assertEqual("reg:T1", manifest.get("task"))
        pairing = _pairing(manifest)
        # The four names are the record schema's own, and the manifest is what the record is
        # built from: a rename here would be a translation table nobody asked for, and the
        # schema requires all four whenever a row carries a pairing at all.
        self.assertEqual("G", pairing.get("group_id"), pairing)
        self.assertEqual("a", pairing.get("arm"), pairing)
        self.assertEqual(1, pairing.get("arms_planned"), pairing)
        self.assertEqual("none", pairing.get("baseline"), pairing)

    def test_a_baseline_that_is_neither_human_nor_none_is_refused(self):
        # A group either has a human arm, whose measurement every row of it carries, or it has
        # none and can never carry an economic claim. A third value is a row the contract refuses,
        # and refusing it here is refusing it before the work is done.
        code = self._cli([
            "open-run", "--repo", str(self.clone), "--provider", "claude",
            "--task", "reg:T1", "--group", "G", "--arm", "a", "--arms-planned", "1",
            "--baseline", "the-last-release",
        ])
        self.assertEqual(2, code)
        self.assertEqual([], list(self.state_root.rglob("manifest.json")))

    def test_the_credential_helper_answers_only_for_the_organisation(self):
        self._open_run("--task", "adhoc")
        secret = f"password={FAKE_TOKEN}"

        served = self._credential("github.com", f"{ORG}/exeris-agent-harness.git")
        self.assertIn(secret, served.stdout, "the helper does not serve the organisation")

        for host, path in (
            ("github.com", "someone-else/their-repo.git"),
            ("gitlab.com", f"{ORG}/exeris-agent-harness.git"),
        ):
            answered = self._credential(host, path)
            self.assertNotIn(
                FAKE_TOKEN, answered.stdout + answered.stderr,
                f"the run's token was handed to {host}/{path}",
            )

    def _credential(self, host, path, carrying=None):
        request = f"protocol=https\\nhost={host}\\npath={path}\\n\\n"
        return self._in_run_env(f'printf "{request}" | git credential fill 2>&1',
                                carrying=carrying)

    def test_a_helper_configured_in_the_clone_is_never_consulted(self):
        # `credential.helper` is multi-valued and a repository's own config is read after the
        # global one, so a reset written only in the run's global file is a reset the clone's
        # helper is appended after. This is the configuration `gh auth setup-git` leaves behind.
        self._git("config", "credential.helper",
                  "!f() { echo username=tripwire; echo password=TRIPWIRE-CREDENTIAL; }; f",
                  cwd=self.clone)
        self._open_run("--task", "adhoc")

        served = self._credential("github.com", f"{ORG}/exeris-agent-harness.git")
        self.assertIn(f"password={FAKE_TOKEN}", served.stdout,
                      "the run's own helper did not answer for the organisation")
        for host, path in (
            ("github.com", f"{ORG}/exeris-agent-harness.git"),
            ("github.com", "someone-else/their-repo.git"),
        ):
            answered = self._credential(host, path)
            self.assertNotIn(
                "TRIPWIRE-CREDENTIAL", answered.stdout + answered.stderr,
                f"the clone's own credential helper answered inside the run for {host}/{path}",
            )

    def test_a_clone_that_installs_hooks_and_signing_does_not_reach_into_the_run(self):
        # Both are single-valued, so a clone that sets either wins outright over the run's global
        # file: the first drops the trailer that is the only link from the commit graph back to
        # the record, the second signs an identity's commit with whatever key the person has.
        installed = self.tmp / "clone-hooks"
        installed.mkdir()
        hook = installed / "prepare-commit-msg"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        self._git("config", "core.hooksPath", str(installed), cwd=self.clone)
        self._git("config", "commit.gpgsign", "true", cwd=self.clone)

        self._open_run("--task", "adhoc")
        made = self._in_run_env(
            'printf "a line\\n" > change.txt && git add change.txt && git commit -m "a change" 2>&1'
        )
        self.assertEqual(0, made.returncode, made.stdout + made.stderr)
        self.assertEqual([f"Exeris-Run: {self.run_id()}"], self._trailers())

    def test_the_run_does_not_inherit_the_credentials_of_the_shell_it_was_opened_from(self):
        self._open_run("--task", "adhoc")
        carried = {
            "GITHUB_TOKEN": "A-PERSONAL-ACCESS-TOKEN",
            "PACKAGES_READ_TOKEN": "A-REGISTRY-TOKEN",
            "SSH_AUTH_SOCK": str(self.tmp / "an-agent.sock"),
            "GIT_ASKPASS": "/usr/bin/true",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": "!f() { echo password=AN-INJECTED-CREDENTIAL; }; f",
        }
        shown = self._in_run_env("env", carrying=carried)
        self.assertEqual(0, shown.returncode, shown.stderr)
        for name in ("A-PERSONAL-ACCESS-TOKEN", "A-REGISTRY-TOKEN", "an-agent.sock"):
            self.assertNotIn(name, shown.stdout,
                             f"{name} survived into the run's environment")
        self.assertIn("GIT_TERMINAL_PROMPT=0", shown.stdout,
                      "git may still put a password question on the person's terminal")
        self.assertIn("GIT_ASKPASS=/bin/false", shown.stdout,
                      "the run kept an askpass program that can answer for a person")

        # `GIT_CONFIG_COUNT` injects configuration that no file can displace, so the run's own
        # git config is only the whole story once it is gone.
        answered = self._credential("gitlab.com", "someone/elsewhere.git", carrying=carried)
        self.assertNotIn("AN-INJECTED-CREDENTIAL", answered.stdout + answered.stderr,
                         "a credential helper injected through the environment answered")

    def test_a_scope_outside_the_configured_vocabulary_is_refused(self):
        code = self._cli([
            "open-run", "--repo", str(self.clone), "--provider", "claude",
            "--task", "adhoc", "--scope", "a-scope-nobody-configured",
        ])
        self.assertEqual(2, code, "a scope outside the repository's vocabulary opened a run")
        self.assertEqual([], list(self.state_root.rglob("manifest.json")),
                         "a refused run left state behind")

    def test_a_scope_in_the_configured_vocabulary_is_recorded(self):
        self._open_run("--task", "adhoc", "--scope", SCOPES[0])
        self.assertEqual(SCOPES[0], self.manifest().get("scope"))

    def test_a_configuration_that_names_no_owner_opens_no_run(self):
        # Rule 3b wants exactly one `Owner:` on a pull request this run's work becomes, and the
        # manifest is where it comes from. Refused before the run exists, not discovered after it
        # has done its work.
        self.config_path.write_text(
            self.config_path.read_text().replace(f'owner_login = "{OWNER_LOGIN}"\n', "")
        )
        code = self._cli([
            "open-run", "--repo", str(self.clone), "--provider", "claude", "--task", "adhoc",
        ])
        self.assertEqual(2, code, "a run opened with nobody accountable for what it produces")
        self.assertEqual([], list(self.state_root.rglob("manifest.json")),
                         "a refused run left state behind")

    def _patch(self, module, name, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def test_a_failure_after_the_mint_leaves_no_run_and_no_live_token(self):
        # A run directory left behind by a half-opened run holds a credential valid for the rest
        # of the hour, belonging to a run that does not exist; `status` would list it as one.
        import harness.token
        import harness.worktree

        revoked = []
        self._patch(harness.token, "revoke",
                    lambda minted, **kwargs: bool(revoked.append(minted)) or True)

        def refuses(*args, **kwargs):
            raise harness.worktree.WorktreeError("the worktree could not be created")

        self._patch(harness.worktree, "add", refuses)

        code = self._cli([
            "open-run", "--repo", str(self.clone), "--provider", "claude", "--task", "adhoc",
        ])
        self.assertEqual(1, code, "a run that could not be opened reported success")
        self.assertEqual([], list((self.state_root / "runs").iterdir()),
                         "the run directory of a run that never opened is still on disk")
        self.assertEqual([FAKE_TOKEN], [minted["token"] for minted in revoked],
                         "the token minted for a run that never opened was not given back")

    def test_status_reports_a_run_whose_token_has_expired(self):
        self._open_run("--task", "adhoc")
        import contextlib
        import io

        import harness.cli

        listing = io.StringIO()
        with contextlib.redirect_stdout(listing):
            self.assertEqual(0, _entry_point(harness.cli)(["status"]))
        shown = listing.getvalue()
        self.assertIn("EXPIRES", shown, "the listing does not say when a run's token runs out")
        self.assertIn("expired", shown,
                      f"a run whose token expired at {FAKE_EXPIRY} is listed as usable: {shown}")


if __name__ == "__main__":
    unittest.main()
