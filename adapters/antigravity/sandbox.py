"""The filesystem this client's process sees: the run's tree and nothing of the person's.

The client's model reads through shell commands granted by name — `cat`, `grep`, `find` and the
rest — and neither the client's allow-list nor its own sandbox can hold a command to a path. Whatever
those commands print enters the session stream, and the stream is carried into the streams
repository. So the confinement is the operating system's: the launcher runs the client under
bubblewrap, in a mount namespace built from the plan below, and a path the plan does not mount is
not there to be read.

The plan, in the order bubblewrap applies it — a later mount lies over an earlier one, so the empty
filesystems over `/tmp` and the home come before every bind beneath them:

* the root, read-only; then an empty `/tmp`, an empty home and an empty directory of runs — the
  one this run's directory sits in — which hide the App's private key where it sits under the home,
  the person's `gh` and git configuration, this run's installation token and environment file, and
  every other run with its own token, environment file and worktree. The directory of runs is
  emptied on its own because the state root can be moved outside the home, where the read-only root
  would otherwise show it;
* a fresh `/dev` and `/proc`;
* read-write: the worktree, the run's own home for this client (it writes its state there), and the
  worktree's git common directory, where `git commit` writes objects and refs;
* read-only over that: the clone's own configuration and hooks directory and the worktree's own
  configuration, where a `core.hooksPath` would take precedence over the run's and stop the hook
  that stamps `Exeris-Run:`;
* read-only: the run's git configuration and hooks directory — what `GIT_CONFIG_GLOBAL` names and
  that hook — each readable directory, the pinned server's package and the documentation root it
  serves, and the directories holding the client's and the server's programs where the empty
  filesystems would otherwise hide them;
* the App's private key and the identity it is decrypted with, each covered by an empty file where
  the empty filesystems do not already hide it — a key configured under `/etc` or `/opt` is on the
  read-only root, and the cover lies over whatever read-only directory holds it;
* the user's runtime directory emptied, and in it only the session bus socket bound, read-write:
  the client signs in through the keyring over that bus, and nothing else there — an SSH or GnuPG
  agent's socket among them — is the client's to reach;
* and last, the empty home and the empty directory of runs made read-only, so that nothing written
  outside the mounts above is written anywhere at all. `/tmp` stays writable, and is the sandbox's
  own.

The rest of the root stays readable: `/etc`, `/usr`, `/var` and any path outside the home, `/tmp` and
the directory of runs. What the plan hides is the person's and the runs' secrets, not the operating
system's files.

Only `KEPT_ENVIRONMENT` crosses into the sandbox, with `HOME` at the client's own home. The
installation token reaches the run's own calls as `GH_TOKEN`, and a process's environment is
readable from `/proc` by any command the client may run — bubblewrap's own included, which is the
first process of the sandbox's process namespace. So bubblewrap is itself started with only the kept
variables, and clears its environment again before the client starts.

A mounted directory that holds the directory of runs or the home is refused rather than mounted, since
it would put back everything the empty filesystems hide.

Nothing else of the run directory is mounted: not its installation token, its environment file, its
`gh` configuration, its staging area or its streams. The client runs in the worktree, dies with the
launcher, and sees only its own processes.
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
from harness import bridge, config  # noqa: E402

#: The program the plan is handed to.
BWRAP = "bwrap"

#: The file and the directory of the run the git inside the sandbox reads.
GITCONFIG = "gitconfig"
HOOKS = "hooks"

#: The client's flag for a directory it may read beyond its workspace.
ADD_DIR = "--add-dir"

#: Where a pinned server's entry point lives inside its package: `<package>/dist/server.js`.
_ENTRY_DEPTH = 2

#: The system's shared temporary directory, emptied inside the sandbox: any process on the machine
#: may leave a file there, so nothing of it is the client's to read.
SHARED_TEMPORARY = os.path.join(os.sep, "tmp")

#: The variables the client is started with, where they are set; every other one is cleared. The
#: locale and terminal; the session bus the keyring sign-in goes through; the run's git identity,
#: configuration and the closures under its credential helper; the run's id. Not the installation
#: token, the `gh` configuration, or anything the person's shell carried besides these.
KEPT_ENVIRONMENT = ("PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TERM", "TZ",
                    "USER", "LOGNAME", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_SSH_COMMAND",
                    "GIT_TERMINAL_PROMPT", "GIT_ASKPASS", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EXERIS_RUN")


def argv(run_dir: str, worktree: str, readable, agy_command, *,
         home: str | None = None, uid: int | None = None, bridge_dir: str | None = None,
         git_common_dir: str | None = None, git_dir: str | None = None,
         docs_root: str | None = None, program_dirs=(), secrets=(),
         environment=None) -> list[str]:
    """The bubblewrap command line that runs `agy_command` inside the plan the module states.

    Every path is mounted at its own name. `program_dirs` are the directories the client's and the
    server's programs are found in that the empty home or `/tmp` would hide; `bridge_dir` and
    `docs_root` are mounted only when given. `secrets` are files covered by an empty one where the
    empty filesystems leave them visible. Of `environment` (the launcher's own, by default) only
    `KEPT_ENVIRONMENT` is passed in. `home` and `uid` are the launcher's own unless given.
    """
    home = os.environ["HOME"] if home is None else home
    uid = os.getuid() if uid is None else uid
    agy_home = drive.home(run_dir)
    runs = os.path.dirname(run_dir)
    runtime = f"/run/user/{uid}"
    out = [BWRAP, "--die-with-parent", "--unshare-pid",
           "--ro-bind", "/", "/",
           "--tmpfs", SHARED_TEMPORARY,
           "--tmpfs", home,
           "--tmpfs", runs,
           "--dev", "/dev",
           "--proc", "/proc"]
    for path in (worktree, agy_home, git_common_dir):
        if path:
            out += ["--bind", path, path]
    out += _git_settings(git_common_dir, git_dir)
    for path in (os.path.join(run_dir, GITCONFIG), os.path.join(run_dir, HOOKS)):
        out += ["--ro-bind", path, path]
    out += _read_only([*readable, bridge_dir, docs_root, *program_dirs], runs, home)
    out += _covered(secrets, runs, home)
    bus = os.path.join(runtime, "bus")
    out += ["--tmpfs", runtime,
            "--bind-try", bus, bus,
            "--remount-ro", home,
            "--remount-ro", runs,
            "--clearenv"]
    for key, value in kept(os.environ if environment is None else environment).items():
        out += ["--setenv", key, value]
    out += ["--setenv", "HOME", agy_home,
            "--chdir", worktree,
            "--", *agy_command]
    return out


def _git_settings(git_common_dir: str | None, git_dir: str | None) -> list[str]:
    """The clone's configuration and hooks and the worktree's own configuration, read-only over the
    writable git directory, so that no `core.hooksPath` set from inside outranks the run's."""
    paths = []
    if git_common_dir:
        paths += [os.path.join(git_common_dir, "config"), os.path.join(git_common_dir, "hooks")]
    if git_dir:
        paths.append(os.path.join(git_dir, "config.worktree"))
    out = []
    for path in _unique(paths):
        out += ["--ro-bind-try", path, path]
    return out


def _read_only(paths, runs: str, home: str) -> list[str]:
    """Each path bound read-only. One that holds the directory of runs or the home, or lies inside
    the directory of runs — a run's directory, with its token, among them — is refused."""
    out = []
    for path in _unique(paths):
        for covered in (runs, home):
            if _within(covered, path):
                raise ValueError(f"{path} holds {covered}, which the sandbox exists to hide")
        if _within(path, runs):
            raise ValueError(f"{path} is inside {runs}, which the sandbox exists to hide")
        out += ["--ro-bind", path, path]
    return out


def _covered(secrets, runs: str, home: str) -> list[str]:
    """An empty file over each secret the empty filesystems leave visible."""
    out = []
    for path in _unique(secrets):
        if os.path.isfile(path) and not _hidden(path, home, runs):
            out += ["--ro-bind", os.devnull, path]
    return out


def kept(environment) -> dict[str, str]:
    """The variables of `environment` that cross into the sandbox."""
    return {key: environment[key] for key in KEPT_ENVIRONMENT if key in environment}


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


def common_dir(worktree: str, which: str = "--git-common-dir") -> str | None:
    """The worktree's git common directory, where its objects and refs are written — or, with
    `which="--git-dir"`, the worktree's own git directory, where its own configuration is."""
    try:
        done = subprocess.run(["git", "-C", worktree, "rev-parse", "--path-format=absolute",
                               which], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    path = done.stdout.strip()
    return path if done.returncode == 0 and os.path.isdir(path) else None


def _within(path: str, top: str) -> bool:
    """Whether `path` is `top` or lies beneath it."""
    return os.path.commonpath([os.path.abspath(path), os.path.abspath(top)]) == os.path.abspath(top)


def _hidden(path: str, home: str, runs: str | None = None) -> bool:
    """Whether the empty home, `/tmp` or directory of runs covers `path` inside the sandbox."""
    return any(_within(path, top) for top in (home, SHARED_TEMPORARY, runs) if top)


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
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"exeris-agent: {exc}; without it the App's key cannot be hidden from the client, "
              f"and the run is refused", file=sys.stderr)
        return 2
    secrets = [path for path in (cfg.key_path, cfg.age_identity_path) if path]
    bridge_dir, docs_root = server(run_dir)
    programs = [agy_command[0]] + ([bridge.PROGRAM] if bridge_dir else [])
    try:
        command = argv(run_dir, worktree, readable_dirs(agy_command[1:]), agy_command, home=home,
                       bridge_dir=bridge_dir, git_common_dir=common_dir(worktree),
                       git_dir=common_dir(worktree, "--git-dir"),
                       docs_root=docs_root, program_dirs=program_dirs(programs, home),
                       secrets=secrets)
    except ValueError as exc:
        print(f"exeris-agent: {exc}; the run is refused", file=sys.stderr)
        return 2
    os.execvpe(BWRAP, command, kept(os.environ))
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
