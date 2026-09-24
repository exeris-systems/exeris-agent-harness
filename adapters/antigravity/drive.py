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

**MCP, from the user's own configuration.** This client takes no MCP configuration per invocation;
it reads the servers its user-level configuration lists, which `agy mcp list` prints. So the
harness cannot give an arm the Exeris server — it can only establish, when the run opens, that the
server the client will start is the pinned one, and refuse the run where it is not. An arm not
given the server is held to the converse: no enabled server of the Exeris bridge, so that two arms
of a group read through the same context tools or through none. The client exposes every tool the
server lists; there is no per-tool allowance to narrow it by.
"""

import json
import os
import re
import subprocess

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


#: How this client is given an MCP server: its user-level configuration, checked when a run opens.
MCP_ROUTE = "user"

#: The server's tools an arm given it may use: every one it lists, since the client allows no fewer.
MCP_TOOLS = ("*",)

#: The listing of the client's configured servers, with a program named by constant.
MCP_LIST = ("agy", "mcp", "list")

#: The keys a listed server's command and its whole line are kept under.
_COMMAND = "command"
_LINE = "line"

#: The first column of the listing's heading line.
_HEADING = "NAME"

#: What marks a configured server as the Exeris bridge other than running the pinned path: a name
#: that says so, or a command that runs the bridge's package from wherever it was checked out.
_BRIDGE_NAME = "exeris"
_BRIDGE_PACKAGE = "exeris-ai-bridge"


class McpRefused(Exception):
    """The client's configured servers are not the ones the arm's table says it has."""


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


def _is_bridge(server: dict, pinned: str | None) -> bool:
    runs = server[_COMMAND]
    return (_BRIDGE_NAME in server["name"].lower() or _BRIDGE_PACKAGE in runs
            or bool(pinned and pinned in runs))


def _listing() -> str:
    try:
        done = subprocess.run(list(MCP_LIST), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise McpRefused(f"`{' '.join(MCP_LIST)}` could not be run: {exc}") from None
    if done.returncode:
        raise McpRefused(f"`{' '.join(MCP_LIST)}` failed: {done.stderr.strip()}")
    return done.stdout


def mcp_state(pinned: str | None, *, enabled: bool) -> str | None:
    """The server line an arm given the bridge will start, or nothing for an arm not given it.

    Given the bridge: exactly one enabled stdio server whose command is `node <pinned path>` must
    be listed, and its line is the answer. Not given it: no enabled server that is the Exeris
    bridge may be listed. Either way anything else is `McpRefused`, naming how to put it right.
    """
    servers = [server for server in mcp_servers(_listing()) if server["status"] == "enabled"]
    if not enabled:
        found = [server[_LINE] for server in servers if _is_bridge(server, pinned)]
        if found:
            raise McpRefused(f"this arm is not given the Exeris MCP server and agy's own "
                             f"configuration enables it ({'; '.join(found)}); the arms of a group "
                             f"read through the same context tools — disable it with "
                             f"`agy mcp disable <name>`, or set mcp = true for the repository")
        return None
    wanted = bridge.command(pinned)
    found = [server[_LINE] for server in servers
             if server["type"] == "stdio" and server[_COMMAND] == wanted]
    if len(found) != 1:
        raise McpRefused(f"this arm is given the Exeris MCP server and agy's own configuration "
                         f"enables {len(found)} stdio server(s) running `{wanted}`; agy takes no "
                         f"MCP server per invocation, so point it at the pinned one: `agy mcp add "
                         f"--env {bridge.DOCS_ROOT_VARIABLE}=<docs root> --env "
                         f"{bridge.MODE_VARIABLE}={bridge.MODE} {bridge.SERVER} {wanted}`")
    return found[0]


def arguments(*, readable, resume: str | None, mcp_config: str | None = None) -> list[str]:
    """What follows the launcher: the readable directories, and the conversation to continue.

    The prompt is not here; see `PROMPT`. `mcp_config` is accepted and unused: this client reads
    its servers from its own configuration, which `mcp_state` checked when the run opened.
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
