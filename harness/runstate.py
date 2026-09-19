"""The run directory: everything a run needs that is not in the worktree.

`~/.local/state/exeris-agent/runs/<ULID>/`, 0700, holding the token, the git configuration bound to
the run, the hook that stamps its commits, the environment that binds a shell to it, and the
manifest that says what the run is. State rather than config or cache, per the XDG split: it
outlives a reboot and it is not something a person edits.

`EXERIS_AGENT_STATE` moves the root. It exists so a test gets a real run directory in a temporary
place instead of a mock of one.
"""

import json
import os
import shutil
import stat

DEFAULT_ROOT = "~/.local/state/exeris-agent"
DIR_MODE = 0o700
SECRET_MODE = 0o600
EXECUTABLE_MODE = 0o700

#: Every path the run writes is escaped where it lands — a shell word in the credential helper, a
#: quoted value in the git config — so this list is a second check rather than the only one. It
#: stays because the root is taken from the environment, anything running as the user can set it,
#: and a state root holding a quote or a newline is a mistake worth refusing rather than a case
#: worth supporting.
FORBIDDEN_IN_ROOT = ("'", '"', "\n")


class StateError(Exception):
    """The run directory could not be established."""


def root() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get("EXERIS_AGENT_STATE")
                                              or DEFAULT_ROOT))


def runs_root() -> str:
    return os.path.join(root(), "runs")


def path(run_id: str) -> str:
    return os.path.join(runs_root(), run_id)


def create(run_id: str) -> str:
    """The run's own directory, 0700, with the subdirectories `open-run` fills."""
    base = root()
    bad = [c for c in FORBIDDEN_IN_ROOT if c in base]
    if bad:
        raise StateError(f"the state root {base!r} contains a quote or newline; move it or set "
                         f"EXERIS_AGENT_STATE somewhere without one")
    run_dir = path(run_id)
    if os.path.exists(run_dir):
        raise StateError(f"{run_dir} already exists")
    os.makedirs(run_dir, mode=DIR_MODE)
    # An ancestor created by makedirs inherits the umask, so the one directory that matters gets
    # its mode set explicitly.
    os.chmod(run_dir, DIR_MODE)
    for name in ("hooks", "gh"):
        os.makedirs(os.path.join(run_dir, name), mode=DIR_MODE)
        os.chmod(os.path.join(run_dir, name), DIR_MODE)
    return run_dir


def discard(run_dir: str) -> None:
    """Remove a run directory whose run never opened.

    What is being removed is a credential and the configuration around it, so failing to remove it
    is worse than failing loudly: an unfinished run left on disk is listed by `status` and reads
    exactly like one somebody could enter.
    """
    shutil.rmtree(run_dir, ignore_errors=True)


def all_runs() -> list[str]:
    """Every run directory, oldest first — which is ULID order, which is directory order."""
    try:
        return [os.path.join(runs_root(), name) for name in sorted(os.listdir(runs_root()))
                if os.path.isdir(os.path.join(runs_root(), name))]
    except FileNotFoundError:
        return []


def write_text(run_dir: str, name: str, text: str, mode: int = SECRET_MODE) -> str:
    target = os.path.join(run_dir, name)
    os.makedirs(os.path.dirname(target), mode=DIR_MODE, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(target, mode)
    return target


def _config_value(value) -> str:
    """A git config value, quoted. Git unescapes `\\\\` and `\\"` inside quotes and nothing else."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _shell_value(value) -> str:
    """A POSIX shell single-quoted word."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _credential_helper(token_path: str) -> str:
    """The helper git runs for the organisation's https remotes.

    It answers `get` and nothing else, so a `store` or `erase` from anywhere in the run writes
    nothing: the token has one source, which is the file the mint wrote. Line one of that file is
    the token, which is why reading it is a `sed` rather than a parser. The path is a shell word
    like any other: `sh` runs this body, so a path that is not quoted is a path that is parsed.
    """
    return ("!f() { [ \"$1\" = get ] || exit 0; "
            "echo username=x-access-token; "
            "sed -n '1s/^/password=/p' " + _shell_value(token_path) + "; }; f")


def boundary(run_dir: str, org: str) -> tuple[tuple[str, str | None, str, str], ...]:
    """The run's boundary as `(section, subsection, key, value)`, in the order git must read it.

    Stated once because it has to hold at two scopes. Written only in `GIT_CONFIG_GLOBAL` it does
    not hold at all: git reads a repository's own config *after* the global one, `credential.helper`
    and `http.extraHeader` are multi-valued, so a helper configured in the clone is appended after
    the reset and answers inside the run; `core.hooksPath` and `commit.gpgsign` are single-valued,
    so a clone that sets either simply wins — the first silently drops the trailer a commit is
    joined to its record by, the second signs an identity's commit with a person's key. The
    worktree scope is read last, which is why these same entries are written there too.

    Each is a closure against one way out. The empty `credential.helper` drops whatever the outer
    environment configured, so the run cannot reach a credential it did not mint; the scoped helper
    answers for one organisation over https and for nothing else; the empty `http.extraHeader`
    drops an `Authorization` header configured as a second spelling of the same thing.
    """
    return (
        ("credential", None, "helper", ""),
        ("credential", f"https://github.com/{org}", "helper",
         _credential_helper(os.path.join(run_dir, "token"))),
        ("http", None, "extraHeader", ""),
        ("core", None, "hooksPath", os.path.join(run_dir, "hooks")),
        ("commit", None, "gpgsign", "false"),
        ("tag", None, "gpgsign", "false"),
    )


def worktree_settings(run_dir: str, org: str) -> list[tuple[str, str]]:
    """The boundary as `git config --worktree` arguments, in the same order."""
    return [(f"{section}.{subsection}.{key}" if subsection else f"{section}.{key}", value)
            for section, subsection, key, value in boundary(run_dir, org)]


def _rendered(entries) -> str:
    lines = []
    for section, subsection, key, value in entries:
        lines.append(f'[{section} "{subsection}"]' if subsection else f"[{section}]")
        lines.append(f"\t{key} = {_config_value(value)}" if value != "" else f"\t{key} =")
    return "\n".join(lines)


def write_gitconfig(run_dir: str, *, user_name: str, user_email: str, org: str) -> str:
    """`<run>/gitconfig` — what git reads in this run outside the worktree.

    The boundary is here and in the worktree's own config, because only the second wins over a
    clone's `.git/config`; this copy is what a `git -C` run from outside the tree reads, and the
    two say the same thing. The `insteadOf` rewrites take ssh remotes to https *before* git
    resolves them, because an ssh remote bypasses the credential helper entirely;
    `GIT_SSH_COMMAND=/bin/false` in the environment closes what a rewrite cannot reach.
    """
    text = f"""\
# Written by `exeris-agent open-run`. GIT_CONFIG_GLOBAL points here and GIT_CONFIG_NOSYSTEM=1
# removes /etc/gitconfig, so this file and the repository's own config are everything git reads
# outside the run's worktree; inside it, the worktree's own config carries the same boundary.
[user]
\tname = {_config_value(user_name)}
\temail = {_config_value(user_email)}
{_rendered(boundary(run_dir, org))}
[url "https://github.com/"]
\tinsteadOf = "git@github.com:"
\tinsteadOf = "ssh://git@github.com/"
[extensions]
\tworktreeConfig = true
"""
    return write_text(run_dir, "gitconfig", text)


def write_hook(run_dir: str, run_id: str) -> str:
    """`<run>/hooks/prepare-commit-msg` — the trailer that joins a commit to its run record.

    `--if-exists doNothing` is what makes it idempotent: an amend or a rebase runs the hook again
    over a message that already carries the trailer, and exactly one is what the grammar allows.
    """
    text = f"""\
#!/bin/sh
# Exactly one `Exeris-Run:` trailer per commit of this run, linking it to the record that observed
# it. Idempotent by construction, so an amend or a rebase does not add a second.
exec git interpret-trailers --in-place --if-exists doNothing \\
        --trailer "Exeris-Run: {run_id}" "$1"
"""
    return write_text(run_dir, os.path.join("hooks", "prepare-commit-msg"), text,
                      mode=EXECUTABLE_MODE)


#: Variables the run does not inherit. Everything here is a way to reach a credential the run did
#: not mint, or to configure git past the configuration the run wrote: a person's API tokens, an
#: agent socket that reaches the forge over ssh without git being involved at all, a helper that
#: puts a password question on the terminal, and `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`, which
#: inject config no file can displace. An environment that only adds leaves every one of them in
#: place, and the environment is the surface this isolation is made of.
#:
#: `PACKAGES_READ_TOKEN` is here by decision rather than by category: the App requests no
#: `packages` permission, so a build inside a run that resolves organisation snapshots fails
#: closed. The alternative was to carry a person's registry token into the run, and the local
#: convention makes that token the same value as `GITHUB_TOKEN`, which is unset one line above.
UNSET = (
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "PACKAGES_READ_TOKEN",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_SYSTEM",
)


def env_values(run_dir: str, *, run_id: str, token: str, user_name: str,
               user_email: str) -> dict[str, str]:
    """The environment a run is bound by, as a mapping — the same values the env file exports.

    `GIT_TERMINAL_PROMPT=0` and `GIT_ASKPASS=/bin/false` are the closure under the credential
    helper: where the helper does not answer — another organisation, a rewritten remote, a token
    whose hour is up — git asks the terminal for a username and password, and the terminal belongs
    to a person. A credential the run cannot mint has to be a failure, not a question.

    Deliberately absent: `CLAUDE_CONFIG_DIR` and its equivalents. The vendor's own state holds a
    person's subscription login, the harness has no business in it, and a run that moved it would
    be asking for a second login rather than isolating anything.
    """
    return {
        "GIT_CONFIG_GLOBAL": os.path.join(run_dir, "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GH_CONFIG_DIR": os.path.join(run_dir, "gh"),
        "GH_TOKEN": token,
        "GIT_SSH_COMMAND": "/bin/false",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_AUTHOR_NAME": user_name,
        "GIT_AUTHOR_EMAIL": user_email,
        "GIT_COMMITTER_NAME": user_name,
        "GIT_COMMITTER_EMAIL": user_email,
        "EXERIS_RUN": run_id,
    }


def write_env(run_dir: str, values: dict[str, str]) -> str:
    """`<run>/env` — `source` it and the shell is the run's.

    The `unset` line comes first: a variable the run sets afterwards is the run's, and one it does
    not set would otherwise be the shell's. 0600, because the file carries the installation token,
    the same credential as `<run>/token`.
    """
    lines = [
        "# Written by `exeris-agent open-run`. Source this file, then cd into the worktree: the",
        "# run's identity is bound to the environment and to the tree, never to the shell it came",
        "# from. The vendor CLI's own configuration directory is untouched, so the person's",
        "# subscription login stays theirs.",
        "unset " + " ".join(UNSET),
    ]
    lines += [f"export {key}={_shell_value(value)}" for key, value in values.items()]
    return write_text(run_dir, "env", "\n".join(lines) + "\n")


def write_manifest(run_dir: str, manifest: dict) -> str:
    return write_text(run_dir, "manifest.json",
                      json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                      mode=stat.S_IRUSR | stat.S_IWUSR)


def read_manifest(run_dir: str) -> dict | None:
    try:
        with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
