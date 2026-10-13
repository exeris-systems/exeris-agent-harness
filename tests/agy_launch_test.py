"""The Antigravity launcher: the client runs confined, under the run's own home, or does not run.

The client reads its tool permissions and its MCP servers from files under `$HOME`, so the home it
runs under is a power of the run; and its model reads through commands granted by name, so what
those commands can reach is bounded by the sandbox it runs in, or by nothing. The launcher is
exercised here as the program it is — a shell script, run with a stand-in `agy` and a stand-in
`bwrap` first on the path — so what a case asserts is the command the client was actually started
with.

* *The client runs inside the sandbox.* The launcher hands `bwrap` the client's command after the
  plan, `HOME` set to `<run>/agy-home`, and the confined program's output still lands in the run's
  stream.
* *Without `bwrap` the client is not started.* An arm that cannot be confined does not run.
* *A run without its own home is refused.* A run directory with no home of its own is a run whose
  powers would be the person's.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
for _path in (str(_HERE.parent), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import support  # noqa: E402  — the fixture, which puts the checkout on the path

from adapters.antigravity import drive as agy_drive  # noqa: E402

LAUNCHER = _HERE.parent / "adapters" / "antigravity" / "launch.sh"
FAKE_BWRAP = _HERE / "fixtures" / "fake_bwrap.py"

#: The programs the launcher itself runs, linked into a path that holds no `bwrap`.
_TOOLS = ("dirname", "cat", "python3", "git")


def _triples(argv):
    return [argv[i:i + 3] for i in range(len(argv) - 2)]


class LauncherTest(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-agy-launch-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shutil.copyfile(support.FAKE_AGY, self.bin / "agy")
        (self.bin / "agy").chmod(0o755)
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        for tool in _TOOLS:
            found = shutil.which(tool)
            self.assertIsNotNone(found, f"{tool} is not on PATH")
            (self.tools / tool).symlink_to(found)
        # Laid out as `open-run` lays one out: a run's directory sits in a directory of runs.
        self.run_dir = self.tmp / "runs" / "run"
        self.run_dir.mkdir(parents=True)
        self.worktree = self.tmp / "wt"
        self.worktree.mkdir()
        self.prompt = self.tmp / "task.txt"
        self.prompt.write_text("placeholder: the task\n", encoding="utf-8")
        self.person = self.tmp / "person"
        self.person.mkdir()
        self.config = self.tmp / "config.toml"
        self.config.write_text('[github]\nclient_id = "placeholder"\ninstallation_id = 1\n'
                               'bot_user_id = 1\nprivate_key = "key.pem"\n', encoding="utf-8")

    def with_bwrap(self):
        shutil.copyfile(FAKE_BWRAP, self.bin / "bwrap")
        (self.bin / "bwrap").chmod(0o755)

    def launch(self, *extra):
        environment = {
            "PATH": f"{self.bin}{os.pathsep}{self.tools}",
            "HOME": str(self.person),
            "GIT_CONFIG_GLOBAL": str(self.run_dir / "gitconfig"),
            "EXERIS_PROMPT_FILE": str(self.prompt),
            "EXERIS_MODEL_ID": support.AGY_MODEL,
            "EXERIS_AGENT_CONFIG": str(self.config),
        }
        return subprocess.run(["/bin/sh", str(LAUNCHER), *extra], env=environment,
                              cwd=self.worktree, capture_output=True, text=True, timeout=60)

    def _records(self, name):
        path = self.bin / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def agy_calls(self):
        return self._records("agy-calls.jsonl")

    def bwrap_calls(self):
        return self._records("bwrap-calls.jsonl")

    def test_the_client_runs_confined_under_the_run_s_home(self):
        self.with_bwrap()
        agy_drive.write_home(str(self.run_dir), worktree=str(self.worktree), server_config=None)
        readable = self.tmp / "readable"
        readable.mkdir()
        done = self.launch("--add-dir", str(readable))
        self.assertEqual(0, done.returncode, done.stderr)
        calls = self.bwrap_calls()
        self.assertEqual(1, len(calls))
        argv = calls[0]["argv"]
        command = argv[argv.index("--") + 1:]
        self.assertEqual(["agy", "--output-format", "stream-json"], command[:3])
        self.assertIn("accept-edits", command)
        self.assertEqual(["--add-dir", str(readable)], command[-2:])
        agy_home = str(self.run_dir / agy_drive.HOME)
        self.assertIn(["--setenv", "HOME", agy_home], _triples(argv))
        self.assertIn(["--chdir", str(self.worktree)], [argv[i:i + 2] for i in range(len(argv))])
        self.assertIn(["--ro-bind", str(readable), str(readable)], _triples(argv))
        log = command[command.index("--log-file") + 1]
        self.assertTrue(log.startswith(agy_home + os.sep), log)
        self.assertEqual([], self.agy_calls(), "the client is started by bwrap, never directly")
        self.assertEqual('{"event": "placeholder"}\n',
                         (self.run_dir / "agy.jsonl").read_text(encoding="utf-8"))
        self.assertEqual("1.9.9\n", (self.run_dir / "agy.version").read_text(encoding="utf-8"))

    def test_without_bwrap_the_client_is_not_started(self):
        agy_drive.write_home(str(self.run_dir), worktree=str(self.worktree), server_config=None)
        done = self.launch()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("bwrap", done.stderr)
        self.assertEqual([], self.agy_calls())
        self.assertFalse((self.run_dir / "agy.jsonl").exists())
        self.assertFalse((self.run_dir / "agy.version").exists())

    def test_a_run_without_its_own_home_is_refused_and_the_client_not_started(self):
        self.with_bwrap()
        done = self.launch()
        self.assertNotEqual(0, done.returncode)
        self.assertIn(agy_drive.HOME, done.stderr)
        self.assertEqual([], self.agy_calls())
        self.assertEqual([], self.bwrap_calls())
        self.assertFalse((self.run_dir / "agy.jsonl").exists())

    def test_without_a_configuration_the_key_cannot_be_hidden_and_the_client_is_not_started(self):
        self.with_bwrap()
        agy_drive.write_home(str(self.run_dir), worktree=str(self.worktree), server_config=None)
        self.config.unlink()
        done = self.launch()
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("key", done.stderr)
        self.assertEqual([], self.agy_calls())
        self.assertEqual([], self.bwrap_calls())


if __name__ == "__main__":
    unittest.main()
