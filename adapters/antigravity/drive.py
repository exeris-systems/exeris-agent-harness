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
"""

import json
import os
import re

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


def arguments(*, readable, resume: str | None) -> list[str]:
    """What follows the launcher: the readable directories, and the conversation to continue.

    The prompt is not here; see `PROMPT`.
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
