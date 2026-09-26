"""The Exeris MCP server, pinned: one program the oracle judges with and an arm reads through.

`[oracle] bridge` names the server's `dist/server.js`. Two readers are given it. The documentation
oracle is handed the path and reads the registry through it; an arm working in a repository whose
table says `mcp = true` is given the same server as its context tools. One program for both is the
point: an arm whose context and an instrument whose judgement came from two builds of the server
would be compared across the difference between the builds.

A server is an instrument, so it is **pinned when a run opens**: its version, from the
`package.json` of the package `dist/` belongs to, and its commit, where that package directory is a
git checkout of its own. A server rebuilt after a run opened is a different instrument, and the pin
is what a reader of the run compares it against.

Nothing here decides which tools an arm may use. The adapter that launches the arm names them,
because how a client addresses one server's tools is the client's own grammar.
"""

import json
import os
import re
import subprocess

#: The name the server is registered under wherever the harness itself writes the registration.
SERVER = "exeris"

#: The server's mode for an arm: it reads the organisation's documents as a contributor would.
MODE = "contributor"

#: The program that runs the server, and the two variables it is configured by.
PROGRAM = "node"
DOCS_ROOT_VARIABLE = "EXERIS_DOCS_ROOT"
MODE_VARIABLE = "EXERIS_BRIDGE_MODE"

#: The file a documentation root is recognised by: the central ADR registry it publishes.
DOCS_INDEX = "adr-index.md"

#: The entry point's own name and the directory it is built into.
ENTRY = "server.js"
BUILD = "dist"
PACKAGE = "package.json"

#: What a record keeps of a pin: the build, never the machine's path to it.
RECORDED = ("version", "commit")

#: Where a manifest keeps the pin, and where the pin keeps the path.
MANIFEST_KEY = "bridge"
PATH_KEY = "path"

#: A path that may reach a command line: absolute, one line, no control character.
_PATH = re.compile(r"/[^\x00-\x1f\x7f]*")

#: A commit as git names one.
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class BridgeError(Exception):
    """A configured bridge the harness cannot pin. Messages name paths, never file contents."""


def _inside(path: str, root: str) -> bool:
    return os.path.commonprefix((path, root)) == root and path.startswith(root + os.sep)


def _version(package: str) -> str:
    """The package's own version, from the `package.json` inside the package directory."""
    manifest = os.path.realpath(os.path.join(package, PACKAGE))
    if not _inside(manifest, package):
        raise BridgeError(f"{os.path.join(package, PACKAGE)} resolves outside {package}")
    try:
        with open(manifest, encoding="utf-8") as handle:
            version = json.load(handle).get("version")
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise BridgeError(f"{manifest} states no version: {exc}") from None
    if not isinstance(version, str) or not version:
        raise BridgeError(f"{manifest} states no version")
    return version


def _commit(package: str) -> str | None:
    """The commit the package directory is checked out at, where it is a checkout of its own.

    A directory inside some other repository's tree is not a checkout of the server, and the
    enclosing repository's head says nothing about which server was built — so the top level git
    reports has to be the package directory itself.
    """
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=package,
                             capture_output=True, text=True, timeout=30)
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=package,
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode or head.returncode:
        return None
    if os.path.realpath(top.stdout.strip()) != package:
        return None
    commit = head.stdout.strip()
    return commit if _COMMIT.fullmatch(commit) else None


def pin(configured: str) -> dict:
    """`{"path", "version", "commit"}` of the server `configured` names, or `BridgeError`.

    The path is resolved first and checked for the shape it has to have — `<package>/dist/server.js`
    — so that what is recorded, passed to the oracle and written into an arm's configuration is the
    file that is actually there and not a link that can be repointed after the run opened.
    """
    if not isinstance(configured, str) or not _PATH.fullmatch(configured):
        raise BridgeError(f"[oracle] bridge {configured!r} is not an absolute path")
    real = os.path.realpath(configured)
    if not _PATH.fullmatch(real) or not os.path.isfile(real):
        raise BridgeError(f"[oracle] bridge names {configured}, which is not a file")
    build = os.path.dirname(real)
    if os.path.basename(real) != ENTRY or os.path.basename(build) != BUILD:
        raise BridgeError(f"[oracle] bridge names {real}, which is not a {BUILD}/{ENTRY}")
    package = os.path.dirname(build)
    built = dict(zip(RECORDED, (_version(package), _commit(package))))
    return {PATH_KEY: real, **built}


def recorded(pinned: dict | None) -> dict | None:
    """What a record carries of a pin: its version and its commit, never the machine's path."""
    if not pinned:
        return None
    return {key: pinned.get(key) for key in RECORDED}


def path_of(pinned: dict | None) -> str | None:
    """The pinned entry point's path, or nothing where there is no pin."""
    return (pinned or {}).get(PATH_KEY)


def docs_root(readable) -> str | None:
    """The first of the readable directories that publishes the central ADR registry."""
    for directory in readable:
        if os.path.isfile(os.path.join(directory, DOCS_INDEX)):
            return directory
    return None


def server_config(bridge: str, root: str) -> dict:
    """The one-server MCP configuration a client that takes one per invocation is given."""
    return {"mcpServers": {SERVER: {
        "command": PROGRAM,
        "args": [bridge],
        "env": {DOCS_ROOT_VARIABLE: root, MODE_VARIABLE: MODE},
    }}}


def command(bridge: str) -> str:
    """The server's command line as a client's own configuration lists it."""
    return f"{PROGRAM} {bridge}"
