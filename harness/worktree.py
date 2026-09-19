"""The worktree: the attribution boundary of a run.

Anything committed inside it is the run's, including a line a person types there — that is
steering, not a human contribution, and a change that should count as a person's is made in the
person's own checkout under their own identity. Binding identity to the tree rather than to a shell
is what makes the boundary hold when the agent opens a second terminal.

`extensions.worktreeConfig` is a repository extension, so it is enabled in the clone; the
worktree-scoped `user.*` it makes possible is what keeps the run's identity off every other
worktree of the same clone.
"""

import os
import re
import subprocess

#: `owner/name` out of any spelling of a GitHub remote: https, ssh, and the scp-like form.
_SLUG = re.compile(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$")


class WorktreeError(Exception):
    """A repository the harness will not open a run against."""


def git(cwd: str, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise WorktreeError(f"`git {' '.join(args)}` failed in {cwd}: {done.stderr.strip()}")
    return done.stdout.strip()


def is_clone(candidate: str) -> bool:
    return os.path.isdir(candidate) and bool(
        git(candidate, "rev-parse", "--git-dir", check=False))


def resolve_clone(target: str, cfg, *, search_root: str | None = None) -> str:
    """The local clone a run is opened against, from `--repo <owner/name|path>`.

    A path is taken as given. A slug is looked up in `[repos.<name>].path` first — the answer the
    configuration can state — and then beside the harness checkout, which is where these
    repositories sit on a maintainer's machine. Anything else is an error naming the path form,
    because guessing a clone is how a run ends up in the wrong tree.
    """
    if os.path.isdir(os.path.expanduser(target)):
        clone = os.path.abspath(os.path.expanduser(target))
        if not is_clone(clone):
            raise WorktreeError(f"{clone} is not a git repository")
        return clone
    if "/" not in target or target.count("/") != 1:
        raise WorktreeError(f"--repo {target!r} is neither a path nor an owner/name slug")
    name = target.split("/", 1)[1]
    configured = cfg.repo(name).path
    candidates = []
    if configured:
        candidates.append(os.path.expanduser(configured))
    candidates.append(os.path.join(search_root or os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), name))
    for candidate in candidates:
        if is_clone(candidate):
            return os.path.abspath(candidate)
    raise WorktreeError(f"no local clone of {target} found; pass --repo <path>, or give "
                        f"[repos.{name}] a `path` in the configuration")


def origin_slug(clone: str) -> str:
    """`owner/name` from the clone's origin — the repository as GitHub names it."""
    url = git(clone, "remote", "get-url", "origin", check=False)
    match = _SLUG.search(url) if url else None
    if not match:
        raise WorktreeError(f"{clone} has no origin the harness can read as owner/name")
    return f"{match.group(1)}/{match.group(2)}"


def default_branch(clone: str) -> str:
    """The default branch, from `origin/HEAD` where the clone knows it.

    A clone made without it falls back to the two names in use; a repository whose default is
    neither is an error rather than a guess, because branching a run off the wrong base produces a
    pull request nobody asked for.
    """
    ref = git(clone, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if ref.startswith("refs/remotes/origin/"):
        return ref[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if git(clone, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}",
               check=False):
            return name
    raise WorktreeError(f"{clone} has no origin/HEAD and neither origin/main nor origin/master; "
                        f"run `git remote set-head origin --auto`")


def add(clone: str, worktree: str, branch: str, base: str) -> str:
    """Create the run's worktree on a new branch off `origin/<base>`, and return its base commit."""
    if git(clone, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False):
        raise WorktreeError(f"branch {branch} already exists in {clone}")
    git(clone, "worktree", "add", "-b", branch, worktree, f"origin/{base}")
    return git(worktree, "rev-parse", "HEAD")


def prune(clone: str) -> None:
    """Drop the clone's record of a worktree whose directory is gone.

    A run removed before it was finished takes its tree with it; the clone would otherwise keep
    administrative state for a worktree that does not exist. Best effort: this runs while another
    failure is being reported, and must not replace it.
    """
    git(clone, "worktree", "prune", check=False)


def local_hooks_path(clone: str) -> str:
    """`core.hooksPath` as the clone's own config sets it, or empty where it sets none."""
    return git(clone, "config", "--local", "--get", "core.hooksPath", check=False)


def bind_identity(clone: str, worktree: str, *, user_name: str, user_email: str,
                  settings=()) -> None:
    """Worktree-scoped `user.*` and the run's boundary, which the extension has to be enabled for.

    Enabling it in the clone is what the extension is for and changes nothing about any other
    worktree's configuration; without it `git config --worktree` refuses.

    The worktree scope is the last config git reads, which is the whole reason the boundary is
    written here: a clone's own `.git/config` is read after the global file and would otherwise
    append its credential helper after the run's reset, and replace the run's hooks path outright.
    Order within the scope is preserved, so the reset stays ahead of the scoped helper it clears.
    """
    git(clone, "config", "extensions.worktreeConfig", "true")
    git(worktree, "config", "--worktree", "user.name", user_name)
    git(worktree, "config", "--worktree", "user.email", user_email)
    for key, value in settings:
        git(worktree, "config", "--worktree", "--add", key, value)
