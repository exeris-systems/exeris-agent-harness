"""Every git and `gh` call the harness makes, behind one object.

Two properties it exists to hold.

*The environment is chosen per call, not per process.* Work a run does reaches GitHub as the
execution identity, under the run's own environment; work a person does — carrying a batch of rows
into the inbox — reaches it as that person, under theirs. A single seam is where that difference is
visible; a `subprocess.run` at each call site is where it is forgotten once.

*The forge is substitutable and the filesystem is not.* A test cannot reach GitHub and should not
mock git: one is a network service with an API this code has to get right, the other is a program
that answers the same way about a temporary repository as about a real one. So the `gh` half of
this object is what a test replaces, and the `git` half runs for real.

`gh` is used as a REST client and never as porcelain. Porcelain output is shaped for a person
reading a terminal and is free to change with the tool; the REST response is the host's own answer,
under a version this module states on every request.
"""

import json
import re
import subprocess

ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"

#: `gh` reports the status of a refused call in its message rather than in its exit code, which is
#: 1 for every failure. A caller that treats one status differently from another has to read it
#: from there.
_STATUS = re.compile(r"\(HTTP (\d{3})\)")


class CommandError(Exception):
    """A subprocess whose failure the caller cannot carry on past."""


class GhError(CommandError):
    """A REST call the host refused. `status` is the HTTP status where one could be read."""

    def __init__(self, endpoint: str, status: int | None, detail: str):
        super().__init__(f"{endpoint} was refused"
                         f"{f' ({status})' if status else ''}: {detail}")
        self.endpoint = endpoint
        self.status = status
        self.detail = detail


class Runner:
    """git and `gh`, run as subprocesses."""

    def git(self, cwd: str, *args: str, env=None, check: bool = True) -> str:
        done = subprocess.run(["git", "-C", cwd, *args],
                              capture_output=True, text=True, env=env)
        if check and done.returncode != 0:
            raise CommandError(f"`git {' '.join(args)}` failed in {cwd}: {done.stderr.strip()}")
        return done.stdout.strip()

    def git_raw(self, cwd: str, *args: str, env=None, check: bool = True) -> str:
        """The same, with the output exactly as git wrote it.

        For content rather than for an answer: a file's trailing newline is part of the file, and a
        digest taken over a stripped copy of it is a digest of something else.
        """
        done = subprocess.run(["git", "-C", cwd, *args],
                              capture_output=True, text=True, env=env)
        if check and done.returncode != 0:
            raise CommandError(f"`git {' '.join(args)}` failed in {cwd}: {done.stderr.strip()}")
        return done.stdout

    def git_lines(self, cwd: str, *args: str, env=None, check: bool = True) -> list[str]:
        """The same, as the non-empty lines of its output — the shape `rev-list` answers in."""
        return [line for line in self.git(cwd, *args, env=env, check=check).splitlines()
                if line.strip()]

    def api(self, endpoint: str, *, method: str = "GET", body=None, env=None):
        """One REST call, as parsed JSON.

        The body goes in on standard input rather than as `-f` pairs: `-f` sends everything as a
        string, and a request that has to say `draft: true` would then be saying `"true"`.
        """
        args = ["gh", "api", "--method", method,
                "-H", f"Accept: {ACCEPT}", "-H", f"X-GitHub-Api-Version: {API_VERSION}",
                endpoint]
        payload = None
        if body is not None:
            args += ["--input", "-"]
            payload = json.dumps(body)
        try:
            done = subprocess.run(args, input=payload, capture_output=True, text=True, env=env)
        except OSError as exc:
            raise GhError(endpoint, None, f"`gh` could not be run: {exc}") from None
        if done.returncode != 0:
            detail = done.stderr.strip() or done.stdout.strip()
            found = _STATUS.search(detail)
            raise GhError(endpoint, int(found.group(1)) if found else None, detail)
        try:
            return json.loads(done.stdout or "null")
        except json.JSONDecodeError as exc:
            raise GhError(endpoint, None, f"the answer was not JSON: {exc}") from None
