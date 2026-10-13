"""The Antigravity sandbox: what the client's process can read is the run's tree, nothing more.

Two levels. The command line is checked on every machine: the empty filesystems come before every
bind beneath them, the paths the client writes are bound read-write, the paths it only reads are
bound read-only, and no part of the run directory that holds a secret is ever a bind source. Where
`bwrap` can build a namespace, the plan is also run: a program inside it reads the worktree and
writes to it, and reads neither of two canaries placed where the App's key and the installation
token live, and a commit made inside it is stamped by the run's hook. Every path is under a
temporary directory standing in for the home, so no real secret is involved.
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

import support  # noqa: E402,F401  — the fixture, which puts the checkout on the path

from adapters.antigravity import drive as agy_drive  # noqa: E402
from adapters.antigravity import sandbox  # noqa: E402
from harness import bridge  # noqa: E402

_CANARY = "placeholder-canary-must-not-be-read"


def _binds(argv, flags):
    """`(flag, source, destination, position)` for each bind of the given kinds."""
    out = []
    for index, arg in enumerate(argv):
        if arg in flags and index + 2 < len(argv):
            out.append((arg, argv[index + 1], argv[index + 2], index))
    return out


def _bwrap_works() -> str | None:
    """Nothing where `bwrap` builds a namespace here, or why it does not."""
    if not shutil.which("bwrap"):
        return "bwrap is not on PATH"
    try:
        done = subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"bwrap could not be run: {exc}"
    if done.returncode:
        return f"bwrap cannot build a namespace here: {done.stderr.strip()}"
    return None


class Layout:
    """A home, a run directory under it laid out as `open-run` lays one out, and a worktree."""

    def __init__(self, tmp: pathlib.Path):
        self.home = tmp / "home"
        self.run = self.home / ".local" / "state" / "exeris-agent" / "runs" / "RUN"
        self.worktree = self.run / "wt"
        self.worktree.mkdir(parents=True)
        self.key = self.home / ".config" / "exeris-agent" / "key.pem"
        self.key.parent.mkdir(parents=True)
        self.key.write_text(_CANARY + "-key\n", encoding="utf-8")
        self.token = self.run / "token"
        self.token.write_text(_CANARY + "-token\n", encoding="utf-8")
        (self.run / "env").write_text(_CANARY + "-env\n", encoding="utf-8")
        (self.run / "gitconfig").write_text("", encoding="utf-8")
        (self.run / "hooks").mkdir()
        (self.run / agy_drive.HOME).mkdir()
        self.readable = tmp / "readable"
        self.readable.mkdir()
        self.bridge = tmp / "bridge"
        self.common = tmp / "origin.git"


class ArgvTest(unittest.TestCase):

    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-agy-sandbox-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.at = Layout(tmp)
        self.argv = sandbox.argv(str(self.at.run), str(self.at.worktree), [str(self.at.readable)],
                                 ["agy", "--add-dir", str(self.at.readable)],
                                 home=str(self.at.home), uid=4242,
                                 bridge_dir=str(self.at.bridge),
                                 git_common_dir=str(self.at.common),
                                 program_dirs=[str(self.at.home / ".local" / "bin")])

    def test_home_and_tmp_are_emptied_before_any_bind_beneath_them(self):
        tmpfs = {self.argv[i + 1]: i for i, a in enumerate(self.argv) if a == "--tmpfs"}
        self.assertIn(str(self.at.home), tmpfs)
        self.assertIn("/tmp", tmpfs)
        self.assertLess(self.argv.index("--ro-bind"), min(tmpfs.values()))
        for flag, source, destination, index in _binds(self.argv, {"--bind", "--ro-bind",
                                                                    "--bind-try"}):
            if destination == "/":
                continue
            for top in (str(self.at.home), "/tmp"):
                if destination.startswith(top + os.sep):
                    self.assertGreater(index, tmpfs[top], f"{destination} is bound before {top}")

    def test_what_the_client_writes_is_bound_read_write(self):
        rw = {source for flag, source, destination, _ in _binds(self.argv, {"--bind"})
              if source == destination}
        self.assertEqual({str(self.at.worktree), str(self.at.run / agy_drive.HOME),
                          str(self.at.common)}, rw)
        # Of the user's runtime directory, only the session bus: it is emptied first, so an SSH or
        # GnuPG agent's socket beside the bus is not there to reach.
        self.assertEqual([("--bind-try", "/run/user/4242/bus", "/run/user/4242/bus")],
                         [b[:3] for b in _binds(self.argv, {"--bind-try"})])
        runtime = self.argv.index("/run/user/4242")
        self.assertEqual("--tmpfs", self.argv[runtime - 1])
        self.assertLess(runtime, self.argv.index("/run/user/4242/bus"))
        self.assertNotIn(("--bind-try", "/run/user/4242", "/run/user/4242"),
                         [b[:3] for b in _binds(self.argv, {"--bind-try"})])

    def test_what_the_client_only_reads_is_bound_read_only(self):
        ro = {source for flag, source, destination, _ in _binds(self.argv, {"--ro-bind"})
              if source == destination}
        self.assertEqual({"/", str(self.at.run / "gitconfig"), str(self.at.run / "hooks"),
                          str(self.at.readable), str(self.at.bridge),
                          str(self.at.home / ".local" / "bin")}, ro)

    def test_no_secret_and_neither_the_run_nor_the_home_is_a_bind_source(self):
        sources = {source for _, source, _, _ in _binds(self.argv, {"--bind", "--ro-bind",
                                                                     "--bind-try"})}
        for hidden in (self.at.token, self.at.run / "env", self.at.run / "gh",
                       self.at.run / "staging", self.at.run, self.at.home, self.at.key):
            self.assertNotIn(str(hidden), sources)

    def test_the_client_runs_in_the_worktree_under_its_own_home(self):
        self.assertIn(["--setenv", "HOME", str(self.at.run / agy_drive.HOME)],
                      [self.argv[i:i + 3] for i in range(len(self.argv) - 2)])
        self.assertEqual(str(self.at.worktree), self.argv[self.argv.index("--chdir") + 1])
        self.assertIn("--die-with-parent", self.argv)
        self.assertIn("--unshare-pid", self.argv)
        separator = self.argv.index("--")
        self.assertEqual(["agy", "--add-dir", str(self.at.readable)], self.argv[separator + 1:])

    def test_a_mount_that_holds_the_home_or_the_run_is_refused(self):
        for wide in (self.at.home, self.at.run, self.at.home.parent):
            with self.assertRaises(ValueError, msg=str(wide)):
                sandbox.argv(str(self.at.run), str(self.at.worktree), [str(wide)], ["agy"],
                             home=str(self.at.home), uid=4242)

    def test_readable_dirs_and_the_server_are_read_from_the_client_s_arguments_and_home(self):
        self.assertEqual(["/a", "/b"], sandbox.readable_dirs(["--add-dir", "/a", "--add-dir=/b",
                                                              "--model", "m"]))
        entry = self.at.bridge / bridge.BUILD / bridge.ENTRY
        entry.parent.mkdir(parents=True)
        entry.write_text("", encoding="utf-8")
        config = self.at.run / agy_drive.HOME
        for part in agy_drive.MCP_CONFIG[:-1]:
            config = config / part
        config.mkdir(parents=True)
        (config / agy_drive.MCP_CONFIG[-1]).write_text(json.dumps(
            bridge.server_config(str(entry), str(self.at.readable))), encoding="utf-8")
        self.assertEqual((str(self.at.bridge), str(self.at.readable)),
                         sandbox.server(str(self.at.run)))


@unittest.skipIf(_bwrap_works(), _bwrap_works())
class ConfinedTest(unittest.TestCase):

    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-agy-sandbox-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.tmp = tmp
        self.at = Layout(tmp)

    def run_confined(self, script, **kwargs):
        argv = sandbox.argv(str(self.at.run), str(self.at.worktree), [], ["sh", "-c", script],
                            home=str(self.at.home), **kwargs)
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)

    def test_the_worktree_is_read_and_written_and_no_secret_is_read(self):
        (self.at.worktree / "readme.txt").write_text("placeholder: the tree\n", encoding="utf-8")
        done = self.run_confined(
            f"cat '{self.at.key}'; cat '{self.at.token}'; cat '{self.at.run / 'env'}'; "
            f"cat readme.txt; echo written > out.txt; "
            f"echo x > '{self.at.run / 'planted'}' && echo planted")
        self.assertIn("placeholder: the tree", done.stdout)
        self.assertNotIn(_CANARY, done.stdout + done.stderr)
        self.assertNotIn("planted", done.stdout)
        self.assertEqual("written\n", (self.at.worktree / "out.txt").read_text(encoding="utf-8"))
        self.assertFalse((self.at.run / "planted").exists())
        self.assertEqual(_CANARY + "-token\n", self.at.token.read_text(encoding="utf-8"))

    def test_a_commit_inside_is_written_to_the_repository_and_stamped_by_the_hook(self):
        origin = self.tmp / "origin"
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": str(self.at.run / "gitconfig")}
        (self.at.run / "gitconfig").write_text(
            "[user]\n\tname = placeholder\n\temail = placeholder@example.invalid\n"
            f"[core]\n\thooksPath = {self.at.run / 'hooks'}\n[commit]\n\tgpgsign = false\n",
            encoding="utf-8")
        hook = self.at.run / "hooks" / "prepare-commit-msg"
        hook.write_text("#!/bin/sh\nexec git interpret-trailers --in-place --if-exists doNothing "
                        "--trailer 'Exeris-Run: RUN' \"$1\"\n", encoding="utf-8")
        hook.chmod(0o755)

        def git(*args, cwd=None):
            return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()

        git("init", "-q", "-b", "main", str(origin))
        git("commit", "-q", "--allow-empty", "-m", "placeholder: root", cwd=origin)
        self.at.worktree.rmdir()
        git("worktree", "add", "-q", "-b", "run", str(self.at.worktree), cwd=origin)
        common = git("-C", str(self.at.worktree), "rev-parse", "--path-format=absolute",
                     "--git-common-dir")
        argv = sandbox.argv(str(self.at.run), str(self.at.worktree), [],
                            ["sh", "-c", "echo change > f.txt && git add f.txt && "
                                         "git commit -q -m 'placeholder: change'"],
                            home=str(self.at.home), git_common_dir=common)
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("Exeris-Run: RUN", git("log", "-1", "--format=%B", "run", cwd=origin))


if __name__ == "__main__":
    unittest.main()
