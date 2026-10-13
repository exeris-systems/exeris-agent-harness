"""The Antigravity launcher: the client runs under the run's own home, or does not run.

The client reads its tool permissions and its MCP servers from files under `$HOME`, so the home it
runs under is a power of the run. The launcher is exercised here as the program it is — a shell
script, run with a stand-in `agy` first on the path — so what a case asserts is the environment
the client was actually started in.

* *The client sees the run's home.* `HOME` is `<run>/agy-home` for the pass and for the version
  probe alike.
* *A run without one is refused.* A run directory with no home of its own is a run whose powers
  would be the person's, and the launcher exits non-zero without starting the client.
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


class LauncherTest(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-agy-launch-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shutil.copyfile(support.FAKE_AGY, self.bin / "agy")
        (self.bin / "agy").chmod(0o755)
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.prompt = self.tmp / "task.txt"
        self.prompt.write_text("placeholder: the task\n", encoding="utf-8")
        self.person = self.tmp / "person"
        self.person.mkdir()

    def launch(self, *extra):
        environment = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.person),
            "GIT_CONFIG_GLOBAL": str(self.run_dir / "gitconfig"),
            "EXERIS_PROMPT_FILE": str(self.prompt),
            "EXERIS_MODEL_ID": support.AGY_MODEL,
        }
        return subprocess.run(["sh", str(LAUNCHER), *extra], env=environment,
                              capture_output=True, text=True, timeout=60)

    def calls(self):
        path = self.bin / "agy-calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_the_client_runs_under_the_run_s_home(self):
        agy_drive.write_home(str(self.run_dir), worktree=str(self.tmp / "wt"), server_config=None)
        done = self.launch("--add-dir", "/opt/placeholder")
        self.assertEqual(0, done.returncode, done.stderr)
        calls = self.calls()
        self.assertEqual(1, len(calls))
        self.assertEqual(str(self.run_dir / agy_drive.HOME), calls[0]["home"])
        self.assertIn("accept-edits", calls[0]["argv"])
        self.assertEqual(["--add-dir", "/opt/placeholder"], calls[0]["argv"][-2:])
        self.assertEqual("1.9.9\n", (self.run_dir / "agy.version").read_text(encoding="utf-8"))

    def test_a_run_without_its_own_home_is_refused_and_the_client_not_started(self):
        done = self.launch()
        self.assertNotEqual(0, done.returncode)
        self.assertIn(agy_drive.HOME, done.stderr)
        self.assertEqual([], self.calls())
        self.assertFalse((self.run_dir / "agy.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
