"""The filesystem this client's process sees: the run's tree and nothing of the person's.

The client's model reads through shell commands granted by name — `cat`, `grep`, `find` and the
rest — and neither the client's allow-list nor its own sandbox can hold a command to a path. Whatever
those commands print enters the session stream, and the stream is carried into the streams
repository. So the confinement is the operating system's: the launcher runs the client under
bubblewrap, in a mount namespace built from the plan below, and a path the plan does not mount is
not there to be read.

The plan, in the order bubblewrap applies it — a later mount lies over an earlier one, so the empty
filesystems over `/tmp` and the home come before every bind beneath them:

* the root, read-only; then an empty `/tmp`, an empty home and an empty run directory, which hide
  the App's private key where it sits under the home, the person's `gh` and git configuration, the
  run's installation token and environment file, every other run directory and every other
  worktree. The run directory is emptied on its own because a state root can be moved outside the
  home, where the read-only root would otherwise show it;
* the App's private key and the identity it is decrypted with, each covered by an empty file where
  the empty filesystems do not already hide it — a key configured under `/etc` or `/opt` is on the
  read-only root;
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
* and last, the empty home and the empty run directory made read-only, so that nothing written
  outside the mounts above is written anywhere at all. `/tmp` stays writable, and is the sandbox's
  own.

The rest of the root stays readable: `/etc`, `/usr`, `/var` and any path outside the home, `/tmp` and
the run directory. What the plan hides is the person's and the run's secrets, not the operating
system's files.

Only `KEPT_ENVIRONMENT` crosses into the sandbox, with `HOME` at the client's own home. The
installation token reaches the run's own calls as `GH_TOKEN`, and a process's environment is
readable from `/proc` by any command the client may run — bubblewrap's own included, which is the
first process of the sandbox's process namespace. So bubblewrap is itself started with only the kept
variables, and clears its environment again before the client starts.

A mounted directory that holds the run directory or the home is refused rather than mounted, since
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

#: The two files and the directory of the run the git inside the sandbox reads.
GITCONFIG = "gitconfig"
HOOKS = "hooks"

#: The client's flag for a directory it may read beyond its workspace.
ADD_DIR = "--add-dir"

#: Where a pinned server's entry point lives inside its package: `<package>/dist/server.js`.
_ENTRY_DEPTH = 2

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
         home: str = os.environ["HOME"], uid: int = os.getuid(), bridge_dir: str | None = None,
         git_common_dir: str | None = None, docs_root: str | None = None,
         program_dirs=(), secrets=(), environment=None) -> list[str]:
    """The bubblewrap command line that runs `agy_command` inside the plan the module states.

    Every path is mounted at its own name. `program_dirs` are the directories the client's and the
    server's programs are found in that the empty home or `/tmp` would hide; `bridge_dir` and
    `docs_root` are mounted only when given. `secrets` are files covered by an empty one where the
    empty filesystems leave them visible. Of `environment` (the launcher's own, by default) only
    `KEPT_ENVIRONMENT` is passed in.
    """
    environment = kept(os.environ if environment is None else environment)
    agy_home = drive.home(run_dir)
    runtime = f"/run/user/{uid}"
    out = [BWRAP, "--die-with-parent", "--unshare-pid",
           "--ro-bind", "/", "/",
           "--tmpfs", "/tmp",
           "--tmpfs", home,
           "--tmpfs", run_dir,
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
    for path in _unique(secrets):
        if os.path.isfile(path) and not _hidden(path, home, run_dir):
            out += ["--ro-bind", os.devnull, path]
    bus = os.path.join(runtime, "bus")
    out += ["--tmpfs", runtime,
            "--bind-try", bus, bus,
            "--remount-ro", home,
            "--remount-ro", run_dir,
            "--clearenv"]
    for key in KEPT_ENVIRONMENT:
        if key in environment:
            out += ["--setenv", key, environment[key]]
    out += ["--setenv", "HOME", agy_home,
            "--chdir", worktree,
            "--", *agy_command]
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


def _hidden(path: str, home: str, run_dir: str | None = None) -> bool:
    """Whether the empty home, `/tmp` or run directory covers `path` inside the sandbox."""
    return any(_within(path, top) for top in (home, "/tmp", run_dir) if top)


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
                       docs_root=docs_root, program_dirs=program_dirs(programs, home),
                       secrets=secrets)
    except ValueError as exc:
        print(f"exeris-agent: {exc}; the run is refused", file=sys.stderr)
        return 2
    os.execvpe(BWRAP, command, kept(os.environ))
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
