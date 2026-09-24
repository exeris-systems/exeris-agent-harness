"""One headless pass of this client, and the session a later pass resumes.

The launcher is the same one a person runs by hand; what this module adds is the command line of a
pass nobody sits at. Every pass of a driven run is given the same powers, so that a row's rounds
differ in what they were told and never in what they were allowed to do.

The session id comes from the pass's own answer on standard output and nowhere else. That answer
also carries a currency figure; nothing here reads it, for the reason the session reader does not.

**MCP, per invocation.** This client takes an MCP configuration on its command line, so an arm
given the Exeris server is given it by the harness and by nothing else: `--mcp-config` names the
one-server file `open-run` wrote into the run's directory, and `--strict-mcp-config` drops every
server the person's own configuration lists. The server's tools are then allowed by name, in the
form this client addresses an MCP tool in `--allowedTools` — `mcp__<server>__<tool>`, where the
client keeps `-` and `_` in a name and writes every other character outside `[A-Za-z0-9]` as `_`.
Only `MCP_TOOLS` are allowed: the read-only documentation tools, which are what the tasks run here
ask of the server.
"""

import json
import re

from harness import bridge

#: The tools a driven pass may use without asking. Fixed, because a pass nobody sits at has nobody
#: to ask, and a surface that varied between rounds or between runs would make two rows differ in a
#: property neither of them records. Git may read and commit; it may not push, which is the close's.
ALLOWED_TOOLS = ("Read,Edit,Write,Glob,Grep,Bash(git add:*),Bash(git commit:*),"
                 "Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(ls:*)")

#: How this client is given an MCP server: a configuration file on each invocation's command line.
MCP_ROUTE = "config"

#: The server's tools an arm given it may use: the documentation family, read-only.
MCP_TOOLS = ("docs-list_adrs", "docs-get_adr", "docs-search")

#: A tool or server name as it may appear inside an `--allowedTools` entry unchanged.
_MCP_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: Edits are accepted without a prompt; everything outside `ALLOWED_TOOLS` is still refused.
PERMISSION_MODE = "acceptEdits"

#: How the prompt reaches this client: on standard input, which print mode reads to its end and
#: writes into the session log byte for byte, so the digest the loop records is the digest the
#: session reader finds.
PROMPT = "stdin"

#: A session id as this client writes it: a UUID. A value of any other shape is not a session this
#: client named, and is not handed back to it as one.
SESSION_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def mcp_tools() -> list[str]:
    """`MCP_TOOLS` as this client names them in `--allowedTools`."""
    out = []
    for tool in MCP_TOOLS:
        if not (_MCP_NAME.fullmatch(bridge.SERVER) and _MCP_NAME.fullmatch(tool)):
            raise ValueError(f"{bridge.SERVER!r}/{tool!r} is not a name this client keeps as is")
        out.append(f"mcp__{bridge.SERVER}__{tool}")
    return out


def arguments(*, readable, resume: str | None, mcp_config: str | None = None) -> list[str]:
    """What follows the launcher on the command line of one pass.

    The prompt is not here: it reaches the client on standard input, see `PROMPT`. `mcp_config` is
    the run's one-server MCP configuration, where the arm was given the server.
    """
    allowed = ALLOWED_TOOLS
    if mcp_config:
        allowed = ",".join([ALLOWED_TOOLS, *mcp_tools()])
    argv = ["-p",
            "--output-format", "json",
            "--permission-mode", PERMISSION_MODE,
            "--allowedTools", allowed]
    if mcp_config:
        argv += ["--mcp-config", mcp_config, "--strict-mcp-config"]
    for directory in readable:
        argv += ["--add-dir", directory]
    if resume:
        if not SESSION_ID.fullmatch(resume):
            raise ValueError(f"{resume!r} is not a session id this client writes")
        argv += ["--resume", resume]
    return argv


def resume_id(answer) -> str | None:
    """The session a pass ran in, from the JSON object it printed, or nothing where it named none.

    `answer` is a `drive.Answer`. Only `session_id` is taken out of the output, and only where it
    has the shape `SESSION_ID` states.
    """
    try:
        found = json.loads(answer.stdout or "")
    except ValueError:
        return None
    if not isinstance(found, dict):
        return None
    named = found.get("session_id")
    return named if isinstance(named, str) and SESSION_ID.fullmatch(named) else None
