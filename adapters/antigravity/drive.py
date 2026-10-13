"""One headless pass of this client, and the conversation a later pass resumes.

The launcher already runs this client in print mode, in its accept-edits mode, with its events
redirected into the run's stream. A driven pass is that launcher with the readable directories
added, and — after the first — the conversation it is to continue.

The conversation id is read from the stream, where the client writes it on the `init` event that
opens every invocation: `{"event": "init", "conversation_id": "…", "init": {…}}`. A resumed
invocation opens with an `init` naming the conversation it continued, and its step indices carry on
from the previous invocation's, which is what lets the reader count the whole conversation as one
session. A stream that names no conversation leaves no way to resume the same one, and the loop is
refused rather than continued in a new session.

**Powers and servers, from a home of the run's own.** This client takes neither its tool
permissions nor its MCP servers per invocation: it reads both from files under `$HOME/.gemini/`,
which on a person's machine hold whatever that person allowed while working with it by hand. A run
under that home would be allowed what the person happened to allow, and two arms of a group would
differ in a power neither of them records. So `open-run` writes `<run>/agy-home`, holding only the
three files the client reads them from, and the launcher runs the client with `HOME` pointing there:

* `antigravity-cli/settings.json` allows exactly `permissions(mcp=…)` — `COMMANDS`, and the Exeris
  server's documentation tools where the arm is given the server — keeps the client's own file tools
  inside its workspace, and trusts the run's worktree. Where the granted commands read is bounded
  by the launcher's sandbox (`sandbox.py`), not by this file;
* `config/config.json` grants nothing globally, and says its grants are already in the shape the
  client would otherwise migrate them to, so nothing is carried in from elsewhere;
* `config/mcp_config.json` lists the pinned server for an arm given it and no server for one not.

Reading and editing need no grant in the client's accept-edits mode. A command outside the list is
refused, and in print mode the refusal also ends the invocation, which the stream's closing event
records. The client signs in through the operating system's keyring, not through a file under its
home, so no credential is written or copied here.

What the client lists from that home is checked when the run opens and again before `drive`'s first
pass, with `agy mcp list` run under the same `HOME`: exactly the pinned server for an arm given it,
none for an arm not. The harness wrote the file the listing comes from, so a listing that differs
says the client read its servers from somewhere other than that home, and the run is refused rather
than driven under a server set nobody recorded.
"""

import json
import os
import re
import subprocess

from adapters.claude import drive as claude_drive
from harness import bridge

#: The file the launcher appends the client's events to, inside the run's directory.
STREAM = "agy.jsonl"

#: How the prompt reaches this client: from the file `EXERIS_PROMPT_FILE` names, which the launcher
#: reads onto the client's own command line. A feedback pass is a pass whose environment names the
#: feedback file instead of the task.
PROMPT = "file"

#: A conversation id as this client writes it: a UUID. A value of any other shape is not a
#: conversation this client named, and is not handed back to it as one.
CONVERSATION_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


#: How this client is given an MCP server: a home of the run's own, written when the run opens.
MCP_ROUTE = "home"

#: The server's tools an arm given it may use: the documentation family, read-only — the same three
#: Claude Code's arm is allowed, so that the two arms of a group read through the same tools.
MCP_TOOLS = claude_drive.MCP_TOOLS

#: The commands a pass may run without asking. Two halves, one capability: the git and listing
#: commands Claude Code's arm is allowed by name, and the read-only commands that stand in for the
#: built-in Read, Glob and Grep it is allowed as tools — this client's model reads and searches the
#: tree through its shell, and in print mode a refused command ends the pass, so a list equal by
#: name would leave it unable to read what Claude's arm reads. The grant is by command name, and the
#: client refuses the forms of `find` that write or run another program (`-delete`, `-exec`,
#: `-execdir`, `-ok`, `-fprint`, `-fprintf`, `-fls`). Where these commands read is bounded by the
#: launcher's sandbox, not by this list: the root is read-only and the home, `/tmp`, the directory
#: of runs and the user's runtime directory are emptied before the worktree, the readable
#: directories, this client's home and what git and the programs need are mounted back, so the
#: person's and the run's secrets are not there to be read; the operating system's own files stay
#: readable. The git commands that only read the repository — `show`,
#: `rev-parse`, `ls-files` — and `which` are granted for the same reason: the model inspects before it
#: edits, and each refusal costs it the pass. Not granted: `git config`, which could unset the hook
#: that stamps `Exeris-Run:`, and `git branch`, which could delete branches in the clone whose git
#: directory the sandbox mounts writable. Git may not push, which is the close's.
COMMANDS = ("git add", "git commit", "git status", "git diff", "git log", "ls",
            "pwd", "cat", "head", "tail", "find", "grep", "wc",
            "git show", "git rev-parse", "git ls-files", "which")

#: The run's own home for this client, inside the run's directory, and the three files under it.
HOME = "agy-home"
SETTINGS = (".gemini", "antigravity-cli", "settings.json")
GRANTS = (".gemini", "config", "config.json")
MCP_CONFIG = (".gemini", "config", "mcp_config.json")

#: The modes the home is written with: it is the run's, and nobody else's to read.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

#: The listing of the client's configured servers, with a program named by constant.
MCP_LIST = ("agy", "mcp", "list")

#: The keys a listed server's command and its whole line are kept under.
_COMMAND = "command"
_LINE = "line"

#: The first column of the listing's heading line.
_HEADING = "NAME"


class McpRefused(Exception):
    """The client's configured servers are not the ones the run's home gives it."""


def permissions(*, mcp: bool) -> list[str]:
    """The client's allow-list for a pass, in its own grammar: `command(…)` and `mcp(<server>/…)`.

    Fixed, because a pass nobody sits at has nobody to ask, and a surface that varied between rounds
    or between runs would make two rows differ in a property neither of them records.
    """
    allow = [f"command({command})" for command in COMMANDS]
    if mcp:
        allow += [f"mcp({bridge.SERVER}/{tool})" for tool in MCP_TOOLS]
    return allow


def home(run_dir: str) -> str:
    """The run's own home for this client."""
    return os.path.join(run_dir, HOME)


def _write(root: str, parts: tuple, value: dict) -> None:
    directory = root
    for part in parts[:-1]:
        directory = os.path.join(directory, part)
        os.makedirs(directory, mode=_DIR_MODE, exist_ok=True)
        os.chmod(directory, _DIR_MODE)
    target = os.path.join(directory, parts[-1])
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.chmod(target, _FILE_MODE)


def write_home(run_dir: str, *, worktree: str, server_config: dict | None) -> list[str]:
    """Write the run's home for this client and return the allow-list it grants.

    `server_config` is the one-server configuration the harness gives an arm given the Exeris server
    (`bridge.server_config`), nothing for an arm not given it. The readable directories are not
    here: they reach the client as `--add-dir` on each pass, which is access the client grants
    without `allowNonWorkspaceAccess`. That setting stays off, and it bounds the client's own file
    tools; the reading commands the list grants are bounded by the launcher's sandbox instead.
    """
    root = home(run_dir)
    os.makedirs(root, mode=_DIR_MODE, exist_ok=False)
    os.chmod(root, _DIR_MODE)
    allow = permissions(mcp=bool(server_config))
    _write(root, SETTINGS, {"permissions": {"allow": allow},
                            "allowNonWorkspaceAccess": False,
                            "trustedWorkspaces": [worktree]})
    _write(root, GRANTS, {"userSettings": {"globalPermissionGrants": {"allow": []},
                                           "permissionGrantsV2Migrated": True}})
    servers = {}
    for name, server in ((server_config or {}).get("mcpServers") or {}).items():
        servers[name] = {**server, "disabled": False}
    _write(root, MCP_CONFIG, {"mcpServers": servers})
    return allow


def granted(run_dir: str) -> list[str] | None:
    """The allow-list the run's home grants now, or nothing where it cannot be read as one."""
    try:
        with open(os.path.join(home(run_dir), *SETTINGS), encoding="utf-8") as handle:
            allow = (json.load(handle).get("permissions") or {}).get("allow")
    except (OSError, ValueError, AttributeError):
        return None
    return allow if isinstance(allow, list) else None


def mcp_servers(listing: str) -> list[dict]:
    """The servers `agy mcp list` printed, as `{name, type, status, command, line}`.

    The listing is columns separated by runs of blanks under a heading line; the command is the
    rest of the line, blanks included.
    """
    out = []
    for line in listing.splitlines():
        words = line.split(None, 3)
        if len(words) < 4 or words[0] == _HEADING:
            continue
        out.append({"name": words[0], "type": words[1], "status": words[2],
                    _COMMAND: words[3].strip(), _LINE: " ".join(line.split())})
    return out


def _listing(run_dir: str) -> str:
    """What the client lists under the run's home, never under the person's."""
    root = home(run_dir)
    if not os.path.isdir(root):
        raise McpRefused(f"the run has no home for agy to read its servers from: {root}")
    environment = {**os.environ, "HOME": root}
    try:
        done = subprocess.run(list(MCP_LIST), capture_output=True, text=True, timeout=60,
                              env=environment)
    except (OSError, subprocess.SubprocessError) as exc:
        raise McpRefused(f"`{' '.join(MCP_LIST)}` could not be run: {exc}") from None
    if done.returncode:
        raise McpRefused(f"`{' '.join(MCP_LIST)}` failed: {done.stderr.strip()}")
    return done.stdout


def mcp_state(run_dir: str, pinned: str | None, *, enabled: bool) -> str | None:
    """The server line an arm given the bridge will start, or nothing for an arm not given it.

    Read under the run's home, whose server file the harness wrote. Given the bridge: the listing
    is exactly one enabled stdio server whose command is `node <pinned path>`, and its line is the
    answer. Not given it: the listing is empty. Anything else is `McpRefused`, because the client
    then reads its servers from somewhere the harness did not write.
    """
    servers = mcp_servers(_listing(run_dir))
    lines = "; ".join(server[_LINE] for server in servers) or "no server"
    if not enabled:
        if servers:
            raise McpRefused(f"this arm is not given the Exeris MCP server and agy, under the "
                             f"run's own home, lists {lines}; the home lists none, so agy is "
                             f"reading its servers from somewhere else")
        return None
    wanted = bridge.command(pinned)
    if (len(servers) != 1 or servers[0]["status"] != "enabled"
            or servers[0]["type"] != "stdio" or servers[0][_COMMAND] != wanted):
        raise McpRefused(f"this arm is given the Exeris MCP server and agy, under the run's own "
                         f"home, lists {lines}; the home lists exactly one enabled stdio server "
                         f"running `{wanted}`, so agy is not reading the servers the harness "
                         f"wrote for it")
    return servers[0][_LINE]


def arguments(*, readable, resume: str | None, mcp_config: str | None = None) -> list[str]:
    """What follows the launcher: the readable directories, and the conversation to continue.

    The prompt is not here; see `PROMPT`. `mcp_config` is accepted and unused: this client reads
    its servers, and its permissions, from the run's own home, which the launcher points it at.
    """
    argv = []
    for directory in readable:
        argv += ["--add-dir", directory]
    if resume:
        if not CONVERSATION_ID.fullmatch(resume):
            raise ValueError(f"{resume!r} is not a conversation id this client writes")
        argv += ["--conversation", resume]
    return argv


def resume_id(answer) -> str | None:
    """The conversation the latest invocation ran in, from its `init` event, or nothing.

    `answer` is a `drive.Answer`; the conversation is in the stream under its `run_dir`, not on
    standard output. The last `init` in the stream is the latest invocation's, because the launcher
    appends, and its id is taken only where it has the shape `CONVERSATION_ID` states.
    """
    found = None
    try:
        with open(os.path.join(answer.run_dir, STREAM), encoding="utf-8",
                  errors="replace") as handle:
            for line in handle:
                found = _named(line, found)
    except OSError:
        return None
    return found


def _named(line: str, found: str | None) -> str | None:
    """The conversation an `init` line names, `found` where the line is not an `init`."""
    try:
        entry = json.loads(line)
    except ValueError:
        return found
    if not isinstance(entry, dict) or entry.get("event") != "init":
        return found
    named = entry.get("conversation_id")
    return named if isinstance(named, str) and CONVERSATION_ID.fullmatch(named) else None
