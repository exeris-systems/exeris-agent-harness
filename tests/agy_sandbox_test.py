"""The Antigravity sandbox: what the client's process can read is the run's tree, nothing more.

Two levels. The command line is checked on every machine: the empty filesystems come before every
bind beneath them, the paths the client writes are bound read-write, the paths it only reads are
bound read-only, no part of the run directory that holds a secret is ever a bind source, a key the
empty filesystems leave visible is covered, and of the environment only the kept variables cross.
Where `bwrap` can build a namespace, the plan is also run: a program inside it reads the worktree
and writes to it, and reads none of the canaries placed where the App's key, the installation token
and the run's environment file live, nor the token as `GH_TOKEN` through `/proc`, with the run
directory and the key under the home and outside it; and a commit made inside it is stamped by the
run's hook. Every path is under a temporary directory, so no real secret is involved.
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
    """A home, a run directory laid out as `open-run` lays one out, and a worktree.

    The run directory and the key are under the home unless `outside` names a directory beyond both
    the home and `/tmp`, standing in for a state root and a key configured elsewhere.
    """

    def __init__(self, tmp: pathlib.Path, outside: pathlib.Path | None = None):
        self.home = tmp / "home"
        state = outside / "state" if outside else self.home / ".local" / "state" / "exeris-agent"
        self.run = state / "runs" / "RUN"
        self.worktree = self.run / "wt"
        self.worktree.mkdir(parents=True)
        self.key = (outside or self.home / ".config") / "exeris-agent" / "key.pem"
        self.key.parent.mkdir(parents=True)
        self.key.write_text(_CANARY + "-key\n", encoding="utf-8")
        self.token = self.run / "token"
        self.token.write_text(_CANARY + "-token\n", encoding="utf-8")
        # Another run beside this one, with its own token and environment file.
        self.sibling = self.run.parent / "OTHER"
        self.sibling.mkdir()
        (self.sibling / "token").write_text(_CANARY + "-sibling-token\n", encoding="utf-8")
        (self.sibling / "env").write_text(_CANARY + "-sibling-env\n", encoding="utf-8")
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
                                 program_dirs=[str(self.at.home / ".local" / "bin")],
                                 environment={"PATH": "/usr/bin", "GH_TOKEN": _CANARY,
                                              "GH_CONFIG_DIR": str(self.at.run / "gh"),
                                              "GITHUB_TOKEN": _CANARY, "EXERIS_RUN": "RUN",
                                              "HOME": str(self.at.home)})

    def test_home_tmp_and_the_run_are_emptied_before_any_bind_beneath_them(self):
        tmpfs = {self.argv[i + 1]: i for i, a in enumerate(self.argv) if a == "--tmpfs"}
        self.assertIn(str(self.at.home), tmpfs)
        self.assertIn("/tmp", tmpfs)
        self.assertIn(str(self.at.run.parent), tmpfs)
        self.assertNotIn(str(self.at.run), tmpfs)
        self.assertLess(self.argv.index("--ro-bind"), min(tmpfs.values()))
        for flag, source, destination, index in _binds(self.argv, {"--bind", "--ro-bind",
                                                                    "--bind-try"}):
            if destination == "/":
                continue
            for top in (str(self.at.home), "/tmp", str(self.at.run.parent)):
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

    def test_the_clone_s_and_the_worktree_s_git_settings_are_read_only_over_the_git_directory(self):
        argv = sandbox.argv(str(self.at.run), str(self.at.worktree), [], ["agy"],
                            home=str(self.at.home), uid=4242, git_common_dir=str(self.at.common),
                            git_dir=str(self.at.common / "worktrees" / "wt"), environment={})
        writable = argv.index(str(self.at.common))
        settings = [b for b in _binds(argv, {"--ro-bind-try"})]
        self.assertEqual({str(self.at.common / "config"), str(self.at.common / "hooks"),
                          str(self.at.common / "worktrees" / "wt" / "config.worktree")},
                         {b[1] for b in settings})
        self.assertTrue(all(b[1] == b[2] and b[3] > writable for b in settings))

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

    def test_of_the_environment_only_the_kept_variables_cross(self):
        clear = self.argv.index("--clearenv")
        passed = {self.argv[i + 1]: self.argv[i + 2] for i, a in enumerate(self.argv)
                  if a == "--setenv"}
        self.assertTrue(all(i > clear for i, a in enumerate(self.argv) if a == "--setenv"))
        self.assertEqual({"PATH": "/usr/bin", "EXERIS_RUN": "RUN",
                          "HOME": str(self.at.run / agy_drive.HOME)}, passed)
        self.assertNotIn(_CANARY, self.argv)
        self.assertTrue(set(sandbox.KEPT_ENVIRONMENT).isdisjoint(
            {"GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "HOME"}))

    def test_a_key_the_empty_filesystems_leave_visible_is_covered(self):
        # Beneath the checkout's own directory: beyond the home, /tmp and the run.
        outside = pathlib.Path(tempfile.mkdtemp(prefix=".agy-sandbox-key-", dir=_HERE))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        key = outside / "key.pem"
        key.write_text("placeholder\n", encoding="utf-8")
        argv = sandbox.argv(str(self.at.run), str(self.at.worktree), [], ["agy"],
                            home=str(self.at.home), uid=4242,
                            secrets=[str(key), str(self.at.key), str(outside / "absent.pem")],
                            environment={})
        covered = [list(b[1:3]) for b in _binds(argv, {"--ro-bind"}) if b[1] == os.devnull]
        self.assertEqual([[os.devnull, str(key)]], covered)

    def test_the_client_runs_in_the_worktree_under_its_own_home(self):
        self.assertIn(["--setenv", "HOME", str(self.at.run / agy_drive.HOME)],
                      [self.argv[i:i + 3] for i in range(len(self.argv) - 2)])
        self.assertEqual(str(self.at.worktree), self.argv[self.argv.index("--chdir") + 1])
        self.assertIn("--die-with-parent", self.argv)
        self.assertIn("--unshare-pid", self.argv)
        separator = self.argv.index("--")
        self.assertEqual(["agy", "--add-dir", str(self.at.readable)], self.argv[separator + 1:])

    def test_a_mount_that_holds_the_home_or_the_runs_or_lies_among_the_runs_is_refused(self):
        for wide in (self.at.home, self.at.run, self.at.run.parent, self.at.sibling,
                     self.at.home.parent):
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

    def run_confined(self, script, at=None):
        """`script` run as the launcher runs the client: through `sandbox.py`, from the worktree,
        with the token in the environment it is started with, as a run's is."""
        at = at or self.at
        configuration = at.run.parent / "config.toml"
        configuration.write_text('[github]\nclient_id = "placeholder"\ninstallation_id = 1\n'
                                 f'bot_user_id = 1\nprivate_key = "{at.key}"\n',
                                 encoding="utf-8")
        environment = {"PATH": os.environ["PATH"], "HOME": str(at.home),
                       "EXERIS_AGENT_CONFIG": str(configuration),
                       "GH_TOKEN": _CANARY + "-environment",
                       "GITHUB_TOKEN": _CANARY + "-environment"}
        return subprocess.run([sys.executable, str(pathlib.Path(sandbox.__file__)), str(at.run),
                               "--", "sh", "-c", script], cwd=at.worktree, env=environment,
                              capture_output=True, text=True, timeout=60)

    def assert_nothing_secret_is_read(self, at):
        (at.worktree / "readme.txt").write_text("placeholder: the tree\n", encoding="utf-8")
        done = self.run_confined(
            f"cat '{at.key}'; cat '{at.token}'; cat '{at.run / 'env'}'; "
            f"cat '{at.sibling / 'token'}' '{at.sibling / 'env'}'; grep -r . '{at.run.parent}'; "
            f"tr '\\0' '\\n' < /proc/self/environ; env; "
            f"for p in /proc/[0-9]*/environ; do tr '\\0' '\\n' < $p; done; "
            f"cat readme.txt; echo written > out.txt; "
            f"echo x > '{at.run / 'planted'}' && echo planted", at=at)
        self.assertIn("placeholder: the tree", done.stdout)
        self.assertIn("PATH=", done.stdout)
        self.assertNotIn(_CANARY, done.stdout + done.stderr)
        self.assertNotIn("planted", done.stdout)
        self.assertEqual("written\n", (at.worktree / "out.txt").read_text(encoding="utf-8"))
        self.assertFalse((at.run / "planted").exists())
        self.assertEqual(_CANARY + "-token\n", at.token.read_text(encoding="utf-8"))

    def test_the_worktree_is_read_and_written_and_no_secret_is_read(self):
        self.assert_nothing_secret_is_read(self.at)

    def test_runs_and_a_key_outside_the_home_and_tmp_are_not_read_either(self):
        # A state root, with this run and another, and a key configured beyond both the home and
        # /tmp: here, beneath the checkout's own directory, which the sandbox's read-only root shows.
        outside = pathlib.Path(tempfile.mkdtemp(prefix=".agy-sandbox-outside-", dir=_HERE))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        second = self.tmp / "second"
        second.mkdir()
        self.assert_nothing_secret_is_read(Layout(second, outside=outside))

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
        own = git("-C", str(self.at.worktree), "rev-parse", "--path-format=absolute", "--git-dir")
        git("config", "extensions.worktreeConfig", "true", cwd=origin)
        git("config", "--worktree", "user.name", "placeholder", cwd=self.at.worktree)
        # Inside, a hooks path set in the clone's or the worktree's own configuration would outrank
        # the run's; both are read-only there, so the attempt fails and the stamp is still made.
        argv = sandbox.argv(str(self.at.run), str(self.at.worktree), [],
                            ["sh", "-c", "git config --local core.hooksPath /nonexistent; "
                                         "git config --worktree core.hooksPath /nonexistent; "
                                         "echo change > f.txt && git add f.txt && "
                                         "git commit -q -m 'placeholder: change'"],
                            home=str(self.at.home), git_common_dir=common, git_dir=own,
                            environment=env)
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("Exeris-Run: RUN", git("log", "-1", "--format=%B", "run", cwd=origin))


if __name__ == "__main__":
    unittest.main()
