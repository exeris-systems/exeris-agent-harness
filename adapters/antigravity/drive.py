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

#: The file the launcher appends the client's events to, inside the run's directory.
STREAM = "agy.jsonl"


def arguments(*, prompt_text: str, prompt_file: str, readable, resume: str | None) -> list[str]:
    """What follows the launcher: the readable directories, and the conversation to continue.

    The prompt is not here. The launcher reads it from the file the environment names, so a
    feedback pass is a pass whose environment names the feedback file instead of the task.
    """
    argv = []
    for directory in readable:
        argv += ["--add-dir", directory]
    if resume:
        argv += ["--conversation", resume]
    return argv


def environment(*, prompt_file: str) -> dict:
    """The prompt file the launcher reads for this pass."""
    return {"EXERIS_PROMPT_FILE": prompt_file}


def resume_id(*, stdout: str, run_dir: str) -> str | None:
    """The conversation the latest invocation ran in, from its `init` event, or nothing.

    The last `init` in the stream is the latest invocation's, because the launcher appends.
    """
    found = None
    try:
        with open(os.path.join(run_dir, STREAM), encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict) and entry.get("event") == "init":
                    named = entry.get("conversation_id")
                    found = named if isinstance(named, str) and named else None
    except OSError:
        return None
    return found
