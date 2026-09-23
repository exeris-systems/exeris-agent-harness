"""One headless pass of this client, and the session a later pass resumes.

The launcher is the same one a person runs by hand; what this module adds is the command line of a
pass nobody sits at. Every pass of a driven run is given the same powers, so that a row's rounds
differ in what they were told and never in what they were allowed to do.

The session id comes from the pass's own answer on standard output and nowhere else. That answer
also carries a currency figure; nothing here reads it, for the reason the session reader does not.
"""

import json
import re

#: The tools a driven pass may use without asking. Fixed, because a pass nobody sits at has nobody
#: to ask, and a surface that varied between rounds or between runs would make two rows differ in a
#: property neither of them records. Git may read and commit; it may not push, which is the close's.
ALLOWED_TOOLS = ("Read,Edit,Write,Glob,Grep,Bash(git add:*),Bash(git commit:*),"
                 "Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(ls:*)")

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


def arguments(*, readable, resume: str | None) -> list[str]:
    """What follows the launcher on the command line of one pass.

    The prompt is not here: it reaches the client on standard input, see `PROMPT`.
    """
    argv = ["-p",
            "--output-format", "json",
            "--permission-mode", PERMISSION_MODE,
            "--allowedTools", ALLOWED_TOOLS]
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
