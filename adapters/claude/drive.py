"""One headless pass of this client, and the session a later pass resumes.

The launcher is the same one a person runs by hand; what this module adds is the command line of a
pass nobody sits at. Every pass of a driven run is given the same powers, so that a row's rounds
differ in what they were told and never in what they were allowed to do.

The session id comes from the pass's own answer on standard output and nowhere else. That answer
also carries a currency figure; nothing here reads it, for the reason the session reader does not.
"""

import json

#: The tools a driven pass may use without asking. Fixed, because a pass nobody sits at has nobody
#: to ask, and a surface that varied between rounds or between runs would make two rows differ in a
#: property neither of them records. Git may read and commit; it may not push, which is the close's.
ALLOWED_TOOLS = ("Read,Edit,Write,Glob,Grep,Bash(git add:*),Bash(git commit:*),"
                 "Bash(git status:*),Bash(git diff:*),Bash(git log:*),Bash(ls:*)")

#: Edits are accepted without a prompt; everything outside `ALLOWED_TOOLS` is still refused.
PERMISSION_MODE = "acceptEdits"


def arguments(*, prompt_text: str, prompt_file: str, readable, resume: str | None) -> list[str]:
    """What follows the launcher on the command line of one pass.

    The prompt comes first, straight after `-p`: `--add-dir` takes every word up to the next option,
    and a prompt written after it would be read as one more directory.
    """
    argv = ["-p", prompt_text,
            "--output-format", "json",
            "--permission-mode", PERMISSION_MODE,
            "--allowedTools", ALLOWED_TOOLS]
    for directory in readable:
        argv += ["--add-dir", directory]
    if resume:
        argv += ["--resume", resume]
    return argv


def environment(*, prompt_file: str) -> dict:
    """Nothing: this client takes its prompt on the command line."""
    return {}


def resume_id(*, stdout: str, run_dir: str) -> str | None:
    """The session a pass ran in, from the JSON object it printed, or nothing where it named none.

    Only `session_id` is taken out of the answer.
    """
    try:
        answer = json.loads(stdout or "")
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(answer, dict):
        return None
    found = answer.get("session_id")
    return found if isinstance(found, str) and found else None
