"""What this client wrote down about one run, and where it wrote it.

The client emits one newline-delimited JSON event per line on standard output, and the launcher
redirects that to a file inside the run's own directory. **The stream is the session log**: there
is no second file the client keeps of its own, so the file the launcher wrote is both what this
module counts and what is carried into the streams repository.

Three event shapes carry everything the row needs. `init` opens the session and names the model.
`step_update` reports one step of the session as its state moves — a step reaches the stream more
than once, so the steps are counted by their index and never by their updates. `result` closes the
session with the client's own totals: the turns it took, the tokens it spent and the time the work
took.

Everything below is read and nothing is written. What the stream does not carry is absent from the
row, never zero: a count the client does not report and a count of nothing are two different
statements, and `capture_level` is what tells a reader which one a row is making.

**What is deliberately not returned: text.** A step's text and the task it was given never leave
this module. The prompt reaches the client on its command line, and the digest the model reference
needs is taken from that file where the harness reads it — not from here, because the stream
carries no prompt text to take it from.

**A driven run is one conversation in several invocations.** Each invocation opens with its own
`init`, the launcher appends them all to one stream, and a resumed invocation's step indices carry
on from the last one's — so steps still collapse by index across the whole conversation. The
closing event of a resumed invocation carries the conversation's totals so far, turns and tokens
alike, so the last closing event is the session's. Its duration is not: what a resumed invocation's
duration covers is unmeasured, so a stream with more than one closing event states no duration and
the record takes the run's own clock. Two conversations in one stream are two sessions and are
refused.

**The oracle's prompts are not steering.** The stream carries no prompt text, so they cannot be
matched by digest as the other client's are. They are matched by invocation instead: each feedback
round is one resumed invocation, and the prompt that opens it is the oracle's. Every recorded round
needs a resumed invocation that opened with a prompt, or the count is refused rather than guessed.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

from harness.capture import NoRow

#: The client whose stream this reads, as `agent.harness.client` spells it. One model under two
#: clients is two harnesses, so this name is part of the model reference and not decoration.
CLIENT = "antigravity"

#: The program the launcher runs, asked for its version where the run did not record one.
PROGRAM = "agy"

#: What the launcher wrote the stream and the client's version to, inside the run's directory.
STREAM = "agy.jsonl"
VERSION_FILE = "agy.version"

#: The two step types that are not tool work: what a person submitted, and what the model answered.
#: Every other step type is a tool step. The set of those types is unmeasured — the client names
#: them as it likes — so they are counted rather than enumerated, and the names actually seen are
#: printed beside the run so the first real run says what they are.
#:
#: The first of the two is the one name this adapter has to assume, because both counts it takes
#: are defined against it: a tool step is a step that is not conversation, and steering is prompts
#: after the first. A stream that never spells it is a stream whose vocabulary is not this one, and
#: it is refused rather than counted — see `read`.
PROMPT_STEP = "user_input"
#: `system_message` is the client's own line into the conversation — a resumed invocation writes
#: one before the model answers — and is conversation rather than tool work.
CONVERSATION = (PROMPT_STEP, "agent_response", "system_message")

#: A step state that names a refusal. The client's state vocabulary is unmeasured beyond the states
#: a completed step passes through, so this matches the word rather than a list: where no state in
#: a stream names a refusal, the count is absent rather than zero, because a client that never
#: reports one has not measured a run that had none.
_DENIAL = re.compile(r"DENIED|DENY|REJECT|REFUS", re.IGNORECASE)

#: `agent.model_id`'s own pattern, from the row contract. A value the contract could not carry is
#: not a model this run ran under.
_MODEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:/@+-]*[A-Za-z0-9])?$")
_MODEL_MAX = 128

#: A version as a client prints it, found inside whatever else the line says.
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9.]+)?")


def _run_dir(manifest: dict) -> str | None:
    """The run's own directory: the parent of the worktree the harness created for it."""
    worktree = manifest.get("worktree")
    return os.path.dirname(str(worktree)) if worktree else None


def locate(manifest: dict, *, projects_root: str | None = None, override: str | None = None) -> str:
    """The run's stream, which the launcher wrote beside the run and nowhere else.

    There is no search to make and no window to match: this client writes to the file descriptor it
    was given, so the only stream a run can have is the one in its own directory. A run whose
    directory holds none was not launched through this adapter, and no counting can recover it.

    `override` is the person's own answer where a stream was collected some other way. It is taken
    as given, because a person naming a file is evidence.
    """
    if override:
        if not os.path.isfile(override):
            raise NoRow("session-not-found", override)
        return override
    run_dir = _run_dir(manifest)
    if not run_dir:
        raise NoRow("session-not-found", "the run says where neither its tree nor its stream is")
    path = os.path.join(run_dir, STREAM)
    if not os.path.isfile(path):
        raise NoRow("session-not-found", path)
    return path


def _version_text(text: str) -> str | None:
    """A client version out of what the program printed, or nothing where it printed none."""
    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not line:
        return None
    found = _VERSION.search(line)
    if found:
        return found.group(0)
    return line if len(line) <= 64 else None


def version(run_dir: str | None) -> str | None:
    """The client version this run ran under: the launcher's record of it, or the program's answer.

    The file is preferred because it was written when the run started, and the program is what is
    installed now. A client upgraded between a run and its record would otherwise be reported as
    the one that did the work.
    """
    if run_dir:
        try:
            with open(os.path.join(run_dir, VERSION_FILE), encoding="utf-8") as handle:
                found = _version_text(handle.read())
        except OSError:
            found = None
        if found:
            return found
    try:
        done = subprocess.run([PROGRAM, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return _version_text(done.stdout or done.stderr)


def _is_model(value) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _MODEL_MAX
            and bool(_MODEL.match(value)))


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _usage(reported) -> dict:
    """The token counts the row contract has names for.

    The client reports a thinking count and a total beside them. Neither is a count the contract
    carries: the first is not one of its four, and the second is a sum a reader can take. A cache
    write count is not reported at all, and is therefore absent rather than zero.
    """
    if not isinstance(reported, dict):
        return {}
    out = {}
    for ours, theirs in (("input_tokens", "input_tokens"),
                         ("output_tokens", "output_tokens"),
                         ("cache_read_tokens", "cache_read_tokens")):
        counted = _count(reported.get(theirs))
        if counted is not None:
            out[ours] = counted
    return out


def read(path: str, *, oracle_prompts=()) -> dict:
    """Everything the row needs from one stream, in counts and digests.

    `oracle_prompts` are the digests of the prompts an oracle loop sent, one per feedback round.
    Only their number is used here, because the stream carries no text to match them against.

    Steps are collapsed by their index before anything is counted, because a step reaches the
    stream once per state it passes through and a measurement counted per update would report the
    client's chattiness rather than the run's work.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise NoRow("session-unreadable", str(exc)) from None

    event_count = 0
    models: set[str] = set()
    steps: dict[object, str] = {}
    states: dict[object, set[str]] = {}
    result = None
    results = 0
    invocation = 0
    opened_by: dict[object, int] = {}
    conversations: set[str] = set()

    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict):
            continue
        # Every line the stream holds and this module could parse. It is what keeps the row
        # summarisable once a retention window has taken the stream itself away.
        event_count += 1

        event = entry.get("event")
        named = entry.get("conversation_id")
        if isinstance(named, str) and named:
            conversations.add(named)
        if event == "init":
            invocation += 1
            model = (entry.get("init") or {}).get("model")
            if _is_model(model):
                models.add(model)
        elif event == "step_update":
            step = entry.get("step_update") or {}
            index = step.get("step_index")
            if index is None:
                continue
            kind = step.get("step_type")
            if isinstance(kind, str) and kind:
                steps[index] = kind
                if kind == PROMPT_STEP:
                    opened_by.setdefault(index, invocation)
            elif index not in steps:
                steps[index] = ""
            state = step.get("state")
            if isinstance(state, str) and state:
                states.setdefault(index, set()).add(state)
        elif event == "result" and isinstance(entry.get("result"), dict):
            result = entry["result"]
            results += 1

    if not models:
        raise NoRow("model-absent", path)
    if len(models) > 1:
        # Two models named in one stream is two sessions in one file. `agent.model_id` is singular
        # and names the model that took the turns, so neither of them is this run's.
        raise NoRow("multi-model", ", ".join(sorted(models)))
    if len(conversations) > 1:
        raise NoRow("multiple-sessions", ", ".join(sorted(conversations)))
    if result is None:
        # The totals — turns, tokens, wall time — are all the closing event's. A stream without one
        # is a session this adapter cannot count, whatever else it holds.
        raise NoRow("session-unreadable", f"{path} carries no result event")

    client_version = version(os.path.dirname(os.path.abspath(path)))
    if not client_version:
        raise NoRow("harness-version-absent", path)

    turns = _count(result.get("num_turns"))
    if turns is None:
        raise NoRow("session-unreadable", f"{path} reports no turn count")

    kinds: dict[str, int] = {}
    for kind in steps.values():
        kinds[kind or "<unnamed>"] = kinds.get(kind or "<unnamed>", 0) + 1
    notes = [
        "step types: " + ", ".join(f"{name}={count}" for name, count in sorted(kinds.items())),
        "step states: " + (", ".join(sorted({state for seen in states.values()
                                             for state in seen})) or "none"),
    ]

    # The assumption both counts rest on, checked here rather than carried into them. Under a
    # client that spells its prompt step some other way, every prompt is counted as a tool call and
    # the steering count reads `0` — a positive claim that nobody steered the run, made out of a
    # name nobody measured. The names the stream did use go to the person here, because a refusal
    # takes the notes below out of the harness's hands and they are the whole of what makes this
    # one answerable: a first run under an unknown client says which vocabulary it speaks.
    if not any(kind == PROMPT_STEP for kind in steps.values()):
        for note in notes:
            print(f"exeris-agent: {path}: {note}", file=sys.stderr)
        raise NoRow("session-unreadable",
                    f"{path} names no {PROMPT_STEP} step, so its tool steps cannot be told from "
                    f"its prompts")

    prompts = sum(1 for kind in steps.values() if kind == PROMPT_STEP)
    # The invocations after the first that opened with a prompt: each is a place a feedback round
    # could have been sent, and there have to be at least as many as the loop recorded sending.
    resumed = len({number for number in opened_by.values() if number > 1})
    if len(oracle_prompts) > resumed:
        raise NoRow("oracle-prompt-unmatched",
                    f"the oracle loop recorded {len(oracle_prompts)} feedback round(s) and {path} "
                    f"holds {resumed} resumed invocation(s) that opened with a prompt")
    tool_calls = sum(1 for kind in steps.values() if kind and kind not in CONVERSATION)

    # A step whose state names a refusal, counted. Where no state in the stream names one, the
    # count is not zero but absent: this client's state vocabulary is unmeasured, and a run nothing
    # refused must not read the same as a run whose refusals nobody could see.
    denials = sum(1 for seen in states.values() if any(_DENIAL.search(state) for state in seen))

    seconds = result.get("duration_seconds")
    wall_time_ms = (max(int(round(float(seconds) * 1000)), 0)
                    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                    and results == 1 else None)

    return {
        "path": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": event_count,
        "model_id": sorted(models)[0],
        "client": CLIENT,
        "version": client_version,
        # The client's own count of the turns it took, rather than a count of the steps it wrote:
        # a turn and a step are not the same unit, and the client is the one that knows its own.
        "turns": turns,
        "tool_calls": tool_calls,
        "usage": _usage(result.get("usage")),
        "wall_time_ms": wall_time_ms,
        # The first prompt is what started the run; what is counted here is steering, which is why
        # a run asked once and left to finish is `0` rather than `1`. With no prompt in the stream
        # there is no count to take, and the row carries none: this adapter's rule throughout is
        # that what the stream does not carry is absent, and a `0` there would be the one shape of
        # the rule that reads as a measurement.
        # The oracle's prompts are the instrument's and are not steering.
        "human_prompts": max(prompts - 1 - len(oracle_prompts), 0) if prompts else None,
        "permission_denials": denials or None,
        # The stream carries no prompt text. The digest of what instructed the run comes from the
        # file the harness passed the client, which is why this adapter cannot be run without one.
        "system_prompt_sha256": None,
        # `full` only where every optional count came from the stream. Without a refusal in it,
        # one of them did not, and the row says so rather than claiming the level.
        "capture_level": "full" if denials else "counts-only",
        "notes": notes,
    }
