"""The filesystem this client's process sees: the run's tree and nothing of the person's.

The client's model reads through shell commands granted by name — `cat`, `grep`, `find` and the
rest — and neither the client's allow-list nor its own sandbox can hold a command to a path. Whatever
those commands print enters the session stream, and the stream is carried into the streams
repository. So the confinement is the operating system's: the launcher runs the client under
bubblewrap, in a mount namespace built from the plan below, and a path the plan does not mount is
not there to be read.

The plan, in the order bubblewrap applies it — a later mount lies over an earlier one, so the empty
filesystems over `/tmp` and the home come before every bind beneath them:

* the root, read-only; then an empty `/tmp` and an empty home, which hide the App's private key,
  the person's `gh` and git configuration, every run directory and every other worktree;
* a fresh `/dev` and `/proc`;
* read-write: the worktree, the run's own home for this client (it writes its state there), and the
  worktree's git common directory, where `git commit` writes objects and refs;
* read-only: the run's git configuration and hooks directory — what `GIT_CONFIG_GLOBAL` names and
  the hook that stamps `Exeris-Run:` — each readable directory, the pinned server's package and the
  documentation root it serves, and the directories holding the client's and the server's programs
  where the empty filesystems would otherwise hide them;
* the user's runtime directory emptied, and in it only the session bus socket bound, read-write:
  the client signs in through the keyring over that bus, and nothing else there — an SSH or GnuPG
  agent's socket among them — is the client's to reach;
* and last, the empty home made read-only, so that nothing written outside the mounts above is
  written anywhere at all. `/tmp` stays writable, and is the sandbox's own.

A mounted directory that holds the run directory or the home is refused rather than mounted, since
it would put back everything the empty filesystems hide.

Nothing else of the run directory is mounted: not its installation token, its environment file, its
`gh` configuration, its staging area or its streams. The client runs with `HOME` at its own home and
in the worktree, dies with the launcher, and sees only its own processes.
"""

import json
import os
import shutil
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from adapters.antigravity import drive  # noqa: E402
from harness import bridge  # noqa: E402

#: The program the plan is handed to.
BWRAP = "bwrap"

#: The two files and the directory of the run the git inside the sandbox reads.
GITCONFIG = "gitconfig"
HOOKS = "hooks"

#: The client's flag for a directory it may read beyond its workspace.
ADD_DIR = "--add-dir"

#: Where a pinned server's entry point lives inside its package: `<package>/dist/server.js`.
_ENTRY_DEPTH = 2


def argv(run_dir: str, worktree: str, readable, agy_command, *,
         home: str = os.environ["HOME"], uid: int = os.getuid(), bridge_dir: str | None = None,
         git_common_dir: str | None = None, docs_root: str | None = None,
         program_dirs=()) -> list[str]:
    """The bubblewrap command line that runs `agy_command` inside the plan the module states.

    Every path is mounted at its own name. `program_dirs` are the directories the client's and the
    server's programs are found in that the empty home or `/tmp` would hide; `bridge_dir` and
    `docs_root` are mounted only when given.
    """
    agy_home = drive.home(run_dir)
    runtime = f"/run/user/{uid}"
    out = [BWRAP, "--die-with-parent", "--unshare-pid",
           "--ro-bind", "/", "/",
           "--tmpfs", "/tmp",
           "--tmpfs", home,
           "--dev", "/dev",
           "--proc", "/proc"]
    for path in (worktree, agy_home, git_common_dir):
        if path:
            out += ["--bind", path, path]
    read_only = [os.path.join(run_dir, GITCONFIG), os.path.join(run_dir, HOOKS), *readable,
                 bridge_dir, docs_root, *program_dirs]
    for path in _unique(read_only):
        for covered in (run_dir, home):
            if _within(covered, path):
                raise ValueError(f"{path} holds {covered}, which the sandbox exists to hide")
        out += ["--ro-bind", path, path]
    bus = os.path.join(runtime, "bus")
    out += ["--tmpfs", runtime,
            "--bind-try", bus, bus,
            "--remount-ro", home,
            "--setenv", "HOME", agy_home,
            "--chdir", worktree,
            "--", *agy_command]
    return out


def _unique(paths) -> list[str]:
    seen = []
    for path in paths:
        if path and path not in seen:
            seen.append(path)
    return seen


def readable_dirs(agy_args) -> list[str]:
    """The directories the client is given as `--add-dir`, in either spelling of the flag."""
    out = []
    args = list(agy_args)
    for index, arg in enumerate(args):
        if arg == ADD_DIR and index + 1 < len(args):
            out.append(args[index + 1])
        elif arg.startswith(ADD_DIR + "="):
            out.append(arg[len(ADD_DIR) + 1:])
    return out


def server(run_dir: str) -> tuple[str | None, str | None]:
    """The pinned server's package directory and its documentation root, from the run's home.

    Nothing for an arm not given the server, or a home whose server file names none.
    """
    try:
        with open(os.path.join(drive.home(run_dir), *drive.MCP_CONFIG), encoding="utf-8") as handle:
            entry = (json.load(handle).get("mcpServers") or {}).get(bridge.SERVER)
    except (OSError, ValueError, AttributeError):
        return None, None
    if not isinstance(entry, dict):
        return None, None
    args = entry.get("args") or []
    package = None
    if args and isinstance(args[0], str) and os.path.isfile(args[0]):
        package = args[0]
        for _ in range(_ENTRY_DEPTH):
            package = os.path.dirname(package)
    root = (entry.get("env") or {}).get(bridge.DOCS_ROOT_VARIABLE)
    root = root if isinstance(root, str) and os.path.isdir(root) else None
    return package, root


def common_dir(worktree: str) -> str | None:
    """The worktree's git common directory, where its objects and refs are written."""
    try:
        done = subprocess.run(["git", "-C", worktree, "rev-parse", "--path-format=absolute",
                               "--git-common-dir"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    path = done.stdout.strip()
    return path if done.returncode == 0 and os.path.isdir(path) else None


def _within(path: str, top: str) -> bool:
    """Whether `path` is `top` or lies beneath it."""
    return os.path.commonpath([os.path.abspath(path), os.path.abspath(top)]) == os.path.abspath(top)


def _hidden(path: str, home: str) -> bool:
    """Whether the empty home or `/tmp` covers `path` inside the sandbox."""
    return any(_within(path, top) for top in (home, "/tmp"))


def program_dirs(programs, home: str) -> list[str]:
    """The directories each program is found in, and the one its real file is in, that the empty
    filesystems would hide."""
    out = []
    for program in programs:
        found = shutil.which(program)
        if not found:
            continue
        for directory in (os.path.dirname(os.path.abspath(found)),
                          os.path.dirname(os.path.realpath(found))):
            if _hidden(directory, home) and directory not in out:
                out.append(directory)
    return out


def main(args) -> int:
    """`sandbox.py <run_dir> -- <agy command…>`, run from the worktree: exec the client confined."""
    if len(args) < 3 or args[1] != "--":
        print("usage: sandbox.py <run_dir> -- <command…>", file=sys.stderr)
        return 2
    if not shutil.which(BWRAP):
        print(f"exeris-agent: {BWRAP} is not on PATH; this client runs only confined to the run's "
              f"tree, and without it the run is refused", file=sys.stderr)
        return 2
    run_dir, agy_command = os.path.abspath(args[0]), args[2:]
    home = os.environ["HOME"]
    worktree = os.getcwd()
    bridge_dir, docs_root = server(run_dir)
    programs = [agy_command[0]] + ([bridge.PROGRAM] if bridge_dir else [])
    try:
        command = argv(run_dir, worktree, readable_dirs(agy_command[1:]), agy_command, home=home,
                       bridge_dir=bridge_dir, git_common_dir=common_dir(worktree),
                       docs_root=docs_root, program_dirs=program_dirs(programs, home))
    except ValueError as exc:
        print(f"exeris-agent: {exc}; the run is refused", file=sys.stderr)
        return 2
    os.execvp(BWRAP, command)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
