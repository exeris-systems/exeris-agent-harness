"""The fixture the closing half of the harness is exercised against.

A run that can be closed needs more than a temporary repository: it needs the run itself, the
session log the client would have written beside it, the two clones a flush carries records into,
and a `gh` that answers. All four are built here, in a temporary directory, so that a case can say
what it is about and nothing else.

Three things are deliberately real rather than mocked. **git** runs against temporary repositories,
because a program that answers the same way about one as about a clone on a maintainer's machine is
not worth replacing with a guess about what it would have said. **The contract** — the schemas, the
inbox validator, the inbox's own identity and the fence register — is copied from the repository
that publishes it, so a case checks a row against the contract it will actually be judged by rather
than against this repository's memory of it. The register is the one copy that is also added to:
the published one names no harness, and a producer's first row is what registering a fence
precedes. **The session log** is a fixture written by hand, carrying placeholder
text only: a real session line is content, and content does not enter this repository in any form.

Only `gh` is substituted, and it is substituted as a program on the path rather than inside the
harness, so what a case asserts is the request that left the process.
"""

import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SESSION = FIXTURES / "session.jsonl"
#: The stream the other client writes: one NDJSON event per line, into a file of the run's own.
AGY_STREAM = FIXTURES / "agy-stream.jsonl"
FAKE_GH = FIXTURES / "fake_gh.py"
FIXTURE_CWD = "/tmp/placeholder/wt"

CLIENT_ID = "Iv23liTESTclientid"
INSTALLATION_ID = 87654321
BOT_USER_ID = 330540144
BOT_LOGIN = "exeris-agent[bot]"
ORG = "exeris-systems"
OWNER_LOGIN = "a-maintainer"
SCOPE = "capture"
#: The arm the fixture opens its runs under: the vendor the row names, and the ledger the run is
#: billed to. Both are the provider table's, which is where a row's `agent.provider` and
#: `accounting.mode` come from.
PROVIDER = "anthropic"
DOMAIN = "construction"
DOCUMENTATION_DOMAIN = "docs-sweep"
CREDENTIAL = "subscription"
TASK = "reg:T-0001"
FAKE_TOKEN = "ghs_TESTtokenTESTtokenTESTtoken0123"
FAKE_EXPIRY = "2027-01-01T00:00:00Z"

#: The bundle the checkout pins, which is the rules a run was subject to. The manifest's own schema
#: version sits beside it and is deliberately not a three-part version: a derivation that read the
#: wrong line would find something here rather than nothing, which is the failure worth catching.
BUNDLE_VERSION = "1.4.0"
MANIFEST_YAML = f"""\
version: 2
repository: exeris-agent-harness

imports:
  - bundle: exeris-agents
    version: {BUNDLE_VERSION}
    ref: 0000000000000000000000000000000000000000
    sha256: sha256:{'0' * 64}
"""

#: The agent file the record hashes, read at the commit a run started from.
AGENTS_FILE = "AGENTS.md"
AGENTS_MD = "# Agent instructions\n\nplaceholder: what an agent working here is subject to\n"

#: The client version the fixture session was written under, and the fence the register is seeded
#: with for it. A row's fence is resolved in the register rather than minted from the run's date,
#: so a fixture that seeded none would be a fixture in which no run can be recorded.
CLIENT_VERSION = "2.1.278"
FENCE = f"2026-09-19-harness-claude-cc-{CLIENT_VERSION.replace('.', '-')}"

#: The other arm: its client, the version the fixture stream was produced under, and its own fence.
#: One producer at one client version is one fence, so a second adapter is a second entry and never
#: a second spelling of the first.
AGY_PROVIDER = "google"
AGY_MODEL = "placeholder-model-1"
AGY_CLIENT = "antigravity"
AGY_VERSION = "1.9.9"
AGY_FENCE = f"2026-09-19-harness-antigravity-cc-{AGY_VERSION.replace('.', '-')}"


def _entry(fence, producer):
    return (f"| `{fence}` | 2026-09-19 | harness, {producer} | placeholder: the producer these "
            f"tests run as, entered so its rows have an id to resolve | placeholder entry |\n")


REGISTER_ENTRIES = (_entry(FENCE, "claude"), _entry(AGY_FENCE, "antigravity"))

#: Where the row contract is read from. A checkout that does not have it skips these cases rather
#: than checking a row against a copy: a second copy of a contract is a second answer.
CONTRACT = pathlib.Path(os.environ.get(
    "EXERIS_EXECUTION_REPO",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "exeris-ai-execution")))


def contract_or_skip(case):
    """The contract repository, or a skip naming what is missing."""
    for needed in ("schemas", "tools/inbox_validate.py", "inbox/inbox.json", "docs/fences.md"):
        if not (CONTRACT / needed).exists():
            case.skipTest(f"no row contract at {CONTRACT} (missing {needed}); set "
                          f"EXERIS_EXECUTION_REPO to a checkout that has it")
    return CONTRACT


def _register(published) -> str:
    """The published fence register with this fixture's own producer entered in it.

    Copied rather than invented, so the id is read back through the grammar the register actually
    publishes; seeded, because the register the contract ships names no harness and a row whose
    fence resolves to nothing is the state under test elsewhere rather than the state every case
    starts from.
    """
    lines = published.read_text(encoding="utf-8").splitlines(keepends=True)
    last = max(index for index, line in enumerate(lines) if line.startswith("| `"))
    for offset, entry in enumerate(REGISTER_ENTRIES):
        lines.insert(last + 1 + offset, entry)
    return "".join(lines)


class _Token(dict):
    """The minted token, readable as a mapping or as an object."""

    @property
    def token(self):
        return self["token"]

    @property
    def expires_at(self):
        return self["expires_at"]


class HarnessFixture(unittest.TestCase):
    """A repository, a configuration, an opened run, and a `gh` that answers."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-close-run-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.origin = self.tmp / "exeris-agent-harness.git"
        self.clone = self.tmp / "clone"
        self.state_root = self.tmp / "state"
        self.state_root.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()

        self._build_origin()
        self._build_key()
        self._build_gh()
        self.execution = self._build_contract_clone()
        self.streams = self._build_streams_clone()
        self._build_config()

        for name, value in {
            "EXERIS_AGENT_CONFIG": str(self.config_path),
            "EXERIS_AGENT_STATE": str(self.state_root),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
        }.items():
            self.set_env(name, value)

    # ---- environment -------------------------------------------------------------

    def set_env(self, name, value):
        previous = os.environ.get(name)
        os.environ[name] = value
        self.addCleanup(lambda n=name, p=previous: os.environ.__setitem__(n, p)
                        if p is not None else os.environ.pop(n, None))

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

    def git(self, *args, cwd=None, check=True):
        done = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                              env=self._fixture_env(), capture_output=True, text=True)
        if check and done.returncode != 0:
            self.fail(f"git {' '.join(args)} failed: {done.stdout}{done.stderr}")
        return done

    # ---- what the fixture is made of ---------------------------------------------

    def _build_origin(self):
        seed = self.tmp / "seed"
        self.git("init", "--bare", "-b", "main", str(self.origin))
        self.git("init", "-b", "main", str(seed))
        (seed / "README.md").write_text("a repository a run can be opened against\n")
        (seed / AGENTS_FILE).write_text(AGENTS_MD)
        (seed / ".agents").mkdir()
        (seed / ".agents" / "manifest.yaml").write_text(MANIFEST_YAML)
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-m", "seed", cwd=seed)
        self.git("remote", "add", "origin", str(self.origin), cwd=seed)
        self.git("push", "origin", "main", cwd=seed)
        self.git("clone", str(self.origin), str(self.clone))

    def _build_key(self):
        self.key_path = self.tmp / "throwaway.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                        "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(self.key_path)],
                       check=True, capture_output=True)
        self.key_path.chmod(0o600)

    def _build_gh(self):
        """A `gh` on the path. Its state lives beside it, inside the temporary directory."""
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shutil.copyfile(FAKE_GH, self.bin / "gh")
        (self.bin / "gh").chmod(0o755)
        self.gh_state = self.bin / "gh-state"

    def _clone_of(self, name, seed):
        """A working clone of a fresh bare origin, seeded by `seed(directory)`."""
        origin = self.tmp / f"{name}.git"
        working = self.tmp / name
        self.git("init", "--bare", "-b", "main", str(origin))
        self.git("init", "-b", "main", str(working))
        seed(working)
        self.git("add", "-A", cwd=working)
        self.git("commit", "-m", f"seed {name}", cwd=working)
        self.git("remote", "add", "origin", str(origin), cwd=working)
        self.git("push", "origin", "main", cwd=working)
        self.git("remote", "set-head", "origin", "--auto", cwd=working)
        # The person's own identity commits here, which is what a flush runs under.
        self.git("config", "user.name", "A Maintainer", cwd=working)
        self.git("config", "user.email", "maintainer@example.invalid", cwd=working)
        return working

    def _build_contract_clone(self):
        contract = contract_or_skip(self)

        def seed(directory):
            shutil.copytree(contract / "schemas", directory / "schemas")
            (directory / "tools").mkdir()
            shutil.copyfile(contract / "tools" / "inbox_validate.py",
                            directory / "tools" / "inbox_validate.py")
            (directory / "inbox").mkdir()
            shutil.copyfile(contract / "inbox" / "inbox.json", directory / "inbox" / "inbox.json")
            (directory / "docs").mkdir()
            (directory / "docs" / "fences.md").write_text(
                _register(contract / "docs" / "fences.md"), encoding="utf-8")

        return self._clone_of("exeris-ai-execution", seed)

    def _build_streams_clone(self):
        def seed(directory):
            (directory / "index.json").write_text("[]\n")

        return self._clone_of("exeris-ai-execution-streams", seed)

    def _build_config(self):
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
            f'execution_repo = "{self.execution}"\n'
            f'streams_repo = "{self.streams}"\n'
            "\n"
            '[repos."exeris-agent-harness"]\n'
            f'domain = "{DOMAIN}"\n'
            f'scope = ["{SCOPE}"]\n'
            "\n"
            # The arm, not the repository: one repository is worked by more than one arm, and the
            # vendor and the ledger are properties of the arm that did the work.
            "[providers.claude]\n"
            f'provider = "{PROVIDER}"\n'
            f'credential = "{CREDENTIAL}"\n'
            "\n"
            "[providers.antigravity]\n"
            f'provider = "{AGY_PROVIDER}"\n'
            f'model_id = "{AGY_MODEL}"\n'
            f'credential = "{CREDENTIAL}"\n'
            'adapter = "antigravity"\n'
        )

    # ---- the harness under test --------------------------------------------------

    def cli(self, argv):
        """One subcommand in process, with the network call that mints a token replaced."""
        import harness.cli
        import harness.token

        saved = []
        for module in (harness.token, harness.cli):
            if hasattr(module, "mint"):
                saved.append((module, module.mint))
                module.mint = lambda *a, **k: _Token(token=FAKE_TOKEN, expires_at=FAKE_EXPIRY)
        self.addCleanup(lambda: [setattr(m, "mint", fn) for m, fn in saved])

        try:
            code = harness.cli.main(list(argv))
        except SystemExit as stop:
            code = stop.code
        return 0 if code is None else int(code)

    def open_run(self, *extra):
        code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "claude",
                         "--task", TASK, "--scope", SCOPE, *extra])
        self.assertEqual(0, code, f"open-run refused: exit {code}")
        return self.run_dir()

    def run_dir(self, kind="model"):
        """The directory of the run a case means.

        A machine holds more than one when a group has both its arms: a human baseline is a run
        too, and it is the one kind of run that mints nothing and writes no row. `kind` is which of
        them is being asked about, and the newest of that kind is the answer — run directories are
        named by a ULID, so directory order is the order they were opened in.
        """
        found = [path.parent for path in sorted(self.state_root.rglob("manifest.json"))
                 if (json.loads(path.read_text()).get("kind") or "model") == kind]
        self.assertTrue(found, f"no {kind} run under {self.state_root}")
        return found[-1]

    def manifest(self, kind="model"):
        return json.loads((self.run_dir(kind) / "manifest.json").read_text())

    def worktree(self, kind="model"):
        return pathlib.Path(self.manifest(kind)["worktree"])

    def run_id(self, kind="model"):
        return self.manifest(kind)["run_id"]

    def commit(self, message="a change", name="change.txt"):
        """One commit inside the run's tree, stamped by the run's own hook."""
        tree = self.worktree()
        (tree / name).write_text("a line\n")
        env = dict(os.environ)
        env.update({
            "GIT_CONFIG_GLOBAL": str(self.run_dir() / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
        })
        for args in (["add", name], ["commit", "-m", message]):
            done = subprocess.run(["git", "-C", str(tree), *args],
                                  env=env, capture_output=True, text=True)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                              env=env, capture_output=True, text=True).stdout.strip()

    # ---- the session the client would have written -------------------------------

    def place_session(self, *, model=None, version=None,
                      name="00000000-0000-4000-8000-0000000000aa.jsonl",
                      source=SESSION, tree=None):
        """The session log, filed where the client would have filed it for this run.

        The fixture's own tree and timestamps are rewritten to this run's, because the adapter
        finds a log by the tree it ran in and the window it opened in — which is the rule under
        test everywhere else, and here is the one place it has to be satisfied rather than checked.
        """
        tree = str(tree or self.worktree())
        text = pathlib.Path(source).read_text(encoding="utf-8")
        text = text.replace(FIXTURE_CWD, tree)
        now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = re.sub(r'"timestamp": "[^"]*"', f'"timestamp": "{now}"', text)
        if model:
            text = re.sub(r'"model": "[^"]*"', f'"model": "{model}"', text)
        if version:
            text = re.sub(r'"version": "[^"]*"', f'"version": "{version}"', text)
        directory = (self.home / ".claude" / "projects"
                     / tree.replace("/", "-").replace(".", "-"))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def place_agy_stream(self, *, source=AGY_STREAM, text=None, version=AGY_VERSION):
        """The stream the other client's launcher would have redirected into the run's directory.

        Beside it, the version the launcher recorded when it started: the version that ran is the
        version that was installed when it ran, and the one installed on the machine these tests
        run on is neither.
        """
        run_dir = self.run_dir()
        stream = run_dir / "agy.jsonl"
        stream.write_text(text if text is not None
                          else pathlib.Path(source).read_text(encoding="utf-8"), encoding="utf-8")
        (run_dir / "agy.version").write_text(f"{version}\n", encoding="utf-8")
        return stream

    def open_agy_run(self, *extra):
        """A run of the other arm. Its adapter takes the task on the command line, so a prompt
        file is what the harness has to pass it and what the row's digest is taken from."""
        prompt = self.tmp / "task.txt"
        prompt.write_text("placeholder: the task this run was given\n", encoding="utf-8")
        code = self.cli(["open-run", "--repo", str(self.clone), "--provider", "antigravity",
                         "--task", TASK, "--scope", SCOPE, "--prompt-file", str(prompt), *extra])
        self.assertEqual(0, code, f"open-run refused: exit {code}")
        return self.run_dir()

    # ---- what the fake `gh` recorded ---------------------------------------------

    def gh_calls(self):
        """Every `gh` invocation, as `(endpoint, method, body)`."""
        path = self.gh_state / "calls.jsonl"
        if not path.is_file():
            return []
        sys.path.insert(0, str(FIXTURES))
        try:
            import fake_gh
        finally:
            sys.path.pop(0)
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            call = json.loads(line)
            endpoint, method, _fields = fake_gh._parse(call["argv"])
            body = json.loads(call["stdin"]) if call.get("stdin") else None
            out.append((endpoint, (method or ("POST" if body else "GET")).upper(), body))
        return out

    def posted_pulls(self):
        return [body for endpoint, method, body in self.gh_calls()
                if method == "POST" and str(endpoint).endswith("/pulls")]

    # ---- staging -----------------------------------------------------------------

    def staged_rows(self, visibility="public"):
        base = self.run_dir() / "staging" / visibility / "inbox"
        return sorted(base.rglob("runs/*.json"))

    def staged_row(self, visibility="public"):
        rows = self.staged_rows(visibility)
        self.assertEqual(1, len(rows), f"expected one {visibility} row: {rows}")
        return json.loads(rows[0].read_text())

    def staged_streams(self):
        return sorted((self.run_dir() / "staging" / "streams").rglob("*.jsonl"))
