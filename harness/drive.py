"""Driving an arm headlessly to `TRUE_DONE`, with the organisation's oracle in the loop.

A paired group measures what the work cost to reach `TRUE_DONE`, and a single pass measures what it
cost to reach wherever that pass stopped. So a driven run is a loop: a pass, a judgement of the tree
it left, and — where the judgement is `FALSE_DONE` and rounds remain — the same session resumed with
a prompt that says which checks still fail. The judgement is the one `oracle.judge` gives and the
decision is taken on what `oracle.outcome_of` admits, so an uncalibrated oracle stops the loop at
`UNKNOWN` exactly as it leaves a row at `UNKNOWN`.

**The feedback is the instrument's words, not a person's.** It is a fixed template around each
failing gate's `check` and `detail`, verbatim, and nothing else: a hint beyond what the oracle said
would be steering, and steering is what `execution.human_prompts` counts. Because it is instrument
output rather than the task, its text is kept beside the run; the task's text is not, and the first
round records only the digest the manifest already holds.

**The loop never runs on `UNKNOWN`.** An instrument that could not judge has said nothing a pass
could act on, and a loop that resumed on it would be spending rounds on the instrument's state.

**Every round is one session.** A later pass resumes the session the previous one ran in; where the
client names no session to resume, the loop is refused rather than continued in a new one, because
a second session is a second run and its counts are not this run's.

**A run that reaches `TRUE_DONE` is not done until its pull request can be opened.** The same
session is asked for the pull request's body (`pr_body.REQUEST`), and the organisation's template
check is run over what it wrote; where the check reports findings and body rounds remain, they are
sent back verbatim and the session resumed again. The body rounds are recorded under `body`, apart
from the oracle's rounds: they are not judgements of the tree, and the tree is expected not to move
under them — where it does, the tree is judged again and the loop stops on that judgement.

What a driven run leaves is `<run>/drive.json`, rewritten after every round: the rounds as they
ran, what each was told (as a digest), what the oracle made of the tree after it, and how long the
pass took. `close-run` reads it for the three things the row depends on — which prompts were the
instrument's, how many rounds the loop was allowed, and so which fence the row sits on.
"""

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable

from . import oracle, pr_body, runstate
from .capture import NoRow

#: The run's record of how it was driven, and the directory beside it the rounds' files go in.
RECORD = "drive.json"
DIRECTORY = "drive"

#: The outcome the loop continues on. Every other outcome ends it.
CONTINUE_ON = "FALSE_DONE"

#: Why a loop stopped, where it was not an outcome that stopped it. The first two are the loop's own
#: ends; the rest are refusals, and a refused loop exits as a refusal.
EXHAUSTED = "rounds-exhausted"
PASS_FAILED = "pass-failed"
RESUME_UNAVAILABLE = "resume-unavailable"
SESSION_CHANGED = "session-changed"
NO_FAILING_GATE = "no-failing-gate"
REFUSALS = (RESUME_UNAVAILABLE, SESSION_CHANGED, NO_FAILING_GATE)

#: The outcome the body rounds follow, and how they end where no refusal ended them.
BODY_AFTER = "TRUE_DONE"
BODY_VALID = "valid"
BODY_UNCHECKED = "check-unavailable"

#: What a body round is told where the arm left no body to check. Fixed, like the feedback.
BODY_ABSENT = "the file {path} is absent or empty"

#: The feedback prompt around the failing gates. Fixed, so that two rows' feedback differs in what
#: the oracle said and in nothing else.
FEEDBACK_HEAD = ("The organisation's checks still fail on this tree, and the task is not done.\n"
                 "\n"
                 "Failing checks, as the checks reported them:\n")
FEEDBACK_LINE = "- {check}: {detail}\n"

#: The two routes a prompt reaches a client by, as an adapter's `PROMPT` names them, and the
#: variable a launcher reads the prompt's file from.
PROMPT_ON_STDIN = "stdin"
PROMPT_IN_FILE = "file"
PROMPT_FILE = "EXERIS_PROMPT_FILE"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")

#: A word of a pass's command line: one line of text, none of it a control character. What reaches
#: that line is a launcher path, options, readable directories and a session id, and none of those
#: carries one; a word that does, or an empty word, did not come from where the harness meant it to.
_WORD = re.compile(r"[^\x00-\x1f\x7f]+")


@dataclasses.dataclass(frozen=True)
class BodyStep:
    """What the body rounds need: where the body goes, how it is checked, how many retries.

    `check` answers the check's findings on the arm's text, nothing where it passes, and raises
    `pr_body.CheckUnavailable` where it cannot be run. `tree` answers the state of the run's tree
    (its head and its uncommitted changes), compared before and after the body rounds.
    """

    path: str
    check: Callable[[str], list[str]]
    tree: Callable[[], object]
    rounds: int


@dataclasses.dataclass(frozen=True)
class Answer:
    """What a finished pass left behind for its adapter to find the session in.

    A client names its session either on standard output or in the stream its launcher wrote into
    the run's directory; the adapter reads whichever of the two its client writes.
    """

    stdout: str
    run_dir: str


def digest(text: str) -> str:
    """A prompt's digest, over the UTF-8 bytes a session log writes it back as."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def failing(gates) -> list[dict]:
    """The gates that failed, in the order the oracle reported them."""
    return [gate for gate in gates or ()
            if isinstance(gate, dict) and gate.get("result") == "fail"]


def feedback(gates) -> str | None:
    """The feedback prompt: the template and each failing gate's check and detail, verbatim.

    Nothing where no gate failed. A `FALSE_DONE` with no failing gate behind it is a judgement the
    template has nothing to say about, and inventing a sentence for it would be a hint.
    """
    found = failing(gates)
    if not found:
        return None
    return FEEDBACK_HEAD + "".join(FEEDBACK_LINE.format(check=gate.get("check", ""),
                                                        detail=gate.get("detail", ""))
                                   for gate in found)


def run_pass(argv: list[str], *, cwd: str, env: dict, stderr_path: str,
             prompt: str | None = None) -> tuple[int, str]:
    """One pass of the adapter: its exit status and what it printed on standard output.

    Standard input carries the prompt where the client reads it from there, and is otherwise
    closed; either way it reaches its end, because a pass nobody sits at has nobody to answer it.
    Standard error goes to a file beside the run, owner-only, where a failed pass can be read
    afterwards. A command line with a word `_WORD` does not match is not started.
    """
    descriptor = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, runstate.SECRET_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as errors:
        command = []
        for word in argv:
            if not _WORD.fullmatch(word):
                errors.write(f"the pass was not started: word {len(command)} of its command line "
                             f"is empty or carries a control character\n")
                return 127, ""
            command.append(word)
        feed = {"stdin": subprocess.DEVNULL} if prompt is None else {"input": prompt}
        try:
            done = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                  stderr=errors, text=True, **feed)
        except OSError as exc:
            errors.write(f"the pass could not be started: {exc}\n")
            return 127, ""
    return done.returncode, done.stdout or ""


def _write(run_dir: str, record: dict) -> None:
    runstate.write_text(run_dir, RECORD, json.dumps(record, indent=2, sort_keys=True) + "\n")


def _stop(code: int, admitted: str, number: int, max_rounds: int) -> str | None:
    """Why the loop ends after this round, or nothing where it goes on."""
    if admitted != CONTINUE_ON:
        return admitted
    if code != 0:
        return PASS_FAILED
    if number > max_rounds:
        return EXHAUSTED
    return None


@dataclasses.dataclass(frozen=True)
class Passes:
    """What every pass of one run is started with: the same client, tree, environment and powers.

    Held together because it is one fact — a run's passes differ in what they are told and in
    which session they resume, and in nothing here.
    """

    module: object
    launcher: str
    run_dir: str
    worktree: str
    environment: dict
    readable: tuple
    mcp_config: str | None = None

    def run(self, number: int, resume: str | None, text: str,
            prompt_file: str) -> tuple[int, str, int]:
        """One pass: `(exit status, standard output, wall time in milliseconds)`."""
        argv = [self.launcher, *self.module.arguments(readable=self.readable, resume=resume,
                                                      mcp_config=self.mcp_config)]
        env, prompt = _prompt_route(self.module, self.environment, text, prompt_file)
        started = time.monotonic()
        code, stdout = run_pass(argv, cwd=self.worktree, env=env, prompt=prompt,
                                stderr_path=os.path.join(self.run_dir, DIRECTORY,
                                                         f"round-{number}.stderr"))
        return code, stdout, int((time.monotonic() - started) * 1000)

    def session(self, stdout: str) -> str | None:
        """The session a pass that printed `stdout` ran in, as its adapter reads it."""
        return self.module.resume_id(Answer(stdout=stdout, run_dir=self.run_dir))


def loop(*, adapter: str, passes: Passes, task_text: str, task_file: str, task_sha256: str,
         max_rounds: int, judge, body: BodyStep | None = None) -> dict:
    """Run passes until the oracle admits an outcome other than `FALSE_DONE`, or the rounds end.

    `max_rounds` is how many feedback rounds may follow the first pass; `0` is a single pass.
    `judge` answers the oracle's judgement of the tree as it stands. `body`, where given, is the
    step asked for once the loop stops at `TRUE_DONE`. Answers the record, which is also on disk; a
    record whose `stopped` is one of `REFUSALS` is a loop that was refused.
    """
    run_dir = passes.run_dir
    os.makedirs(os.path.join(run_dir, DIRECTORY), mode=runstate.DIR_MODE, exist_ok=True)
    record = {"adapter": adapter, "oracle_rounds": max_rounds, "rounds": [], "stopped": None}
    prompt_text, prompt_file, prompt_sha256 = task_text, task_file, task_sha256
    resume = None
    number = 0
    while True:
        number += 1
        code, stdout, wall_time_ms = passes.run(number, resume, prompt_text, prompt_file)
        judgement = judge()
        admitted = oracle.outcome_of(judgement)
        record["rounds"].append({"round": number, "prompt_sha256": prompt_sha256,
                                 "outcome": admitted, "oracle_outcome": judgement.get("outcome"),
                                 "wall_time_ms": wall_time_ms, "exit": code})
        record["stopped"] = _stop(code, admitted, number, max_rounds)
        if record["stopped"] is None:
            record["stopped"], resume, prompt_file, prompt_text = _next(
                passes, stdout, resume, number + 1, judgement)
            prompt_sha256 = digest(prompt_text) if prompt_text is not None else None
        if resume and not record["stopped"]:
            record["session"] = resume
        _write(run_dir, record)
        if record["stopped"]:
            break
    if body is not None and record["stopped"] == BODY_AFTER:
        before = body.tree()
        record["body"] = _body_rounds(passes, stdout=stdout, resumed=resume, number=number,
                                      body=body)
        if body.tree() != before:
            record["body"]["tree_moved"] = True
            record["stopped"] = oracle.outcome_of(judge())
        _write(run_dir, record)
    return record


def _body_rounds(passes: Passes, *, stdout: str, resumed: str | None, number: int,
                 body: BodyStep) -> dict:
    """The body rounds, resumed in the session the oracle's rounds ran in.

    Refused, before any is sent, where the client named no session or another one. Each round
    after the first sends the check's findings on the previous one's file.
    """
    out = {"rounds": [], "stopped": None}
    session = passes.session(stdout)
    if not session or (resumed and session != resumed):
        out["stopped"] = SESSION_CHANGED if session else RESUME_UNAVAILABLE
        return out
    text = pr_body.request(body.path)
    for attempt in range(body.rounds + 1):
        number += 1
        findings = _body_round(passes, body, out, number=number, session=session, text=text)
        if out["stopped"]:
            return out
        if attempt < body.rounds:
            text = pr_body.feedback(body.path, findings)
    out["stopped"] = EXHAUSTED
    return out


def _body_round(passes: Passes, body: BodyStep, out: dict, *, number: int, session: str,
                text: str) -> list[str]:
    """One body round, recorded in `out`: the check's findings, with `out` stopped where the round
    ended the body step — a failed pass, another session, a check that could not run, or a pass."""
    prompt_file = runstate.write_text(passes.run_dir,
                                      os.path.join(DIRECTORY, f"round-{number}.prompt"), text)
    code, answered, wall_time_ms = passes.run(number, session, text, prompt_file)
    entry = {"round": number, "prompt_sha256": digest(text), "exit": code,
             "wall_time_ms": wall_time_ms}
    out["rounds"].append(entry)
    if code != 0 or passes.session(answered) != session:
        out["stopped"] = PASS_FAILED if code != 0 else SESSION_CHANGED
        return []
    findings = _body_findings(body, out)
    if findings is None:
        return []
    entry["findings"] = len(findings)
    if not findings:
        out["stopped"] = BODY_VALID
        out["sha256"] = digest(pr_body.read(body.path))
    return findings


def _body_findings(body: BodyStep, out: dict) -> list[str] | None:
    """The check's findings on the body as the arm left it; nothing, with `out` stopped, where the
    check could not be run."""
    written = pr_body.read(body.path)
    if written is None:
        return [BODY_ABSENT.format(path=body.path)]
    try:
        return body.check(written)
    except pr_body.CheckUnavailable as exc:
        out["stopped"], out["detail"] = BODY_UNCHECKED, str(exc)
        return None


def _prompt_route(module, environment: dict, prompt_text: str,
                  prompt_file: str) -> tuple[dict, str | None]:
    """The pass's environment and standard input, with the prompt on the route the adapter names.

    `PROMPT_ON_STDIN`: the text is the pass's standard input and the environment is the run's.
    `PROMPT_IN_FILE`: the environment names the prompt's file under `PROMPT_FILE` and standard input
    is closed. Neither route puts the prompt on the command line `run_pass` starts.
    """
    env = dict(environment)
    if module.PROMPT == PROMPT_ON_STDIN:
        return env, prompt_text
    if module.PROMPT == PROMPT_IN_FILE:
        env[PROMPT_FILE] = prompt_file
        return env, None
    raise ValueError(f"{module.__name__} names no prompt route: {module.PROMPT!r}")


def _next(passes: Passes, stdout: str, resumed: str | None, number: int, judgement: dict):
    """`(stopped, session, prompt file, prompt text)` for the round after this one.

    Refused where the client named no session to continue, named a different one from the session
    this pass resumed, or where the judgement has no failing gate to say anything about.
    """
    session = passes.session(stdout)
    if not session:
        return RESUME_UNAVAILABLE, None, None, None
    if resumed and session != resumed:
        return SESSION_CHANGED, None, None, None
    text = feedback(judgement.get("gates"))
    if text is None:
        return NO_FAILING_GATE, None, None, None
    path = runstate.write_text(passes.run_dir, os.path.join(DIRECTORY, f"round-{number}.prompt"),
                               text)
    return None, session, path, text


def refusal(record: dict) -> str | None:
    """What a refused loop says, or nothing where the loop ended on its own terms."""
    stopped = record.get("stopped")
    if stopped == RESUME_UNAVAILABLE:
        return (f"the {record.get('adapter')} pass named no session to resume, so the oracle's "
                f"feedback was not sent: a new session would be a second run")
    if stopped == SESSION_CHANGED:
        return (f"the {record.get('adapter')} client answered a resume with another session; the "
                f"loop stops rather than count two sessions as one")
    if stopped == NO_FAILING_GATE:
        return ("the oracle said FALSE_DONE and named no failing gate, so there is no feedback "
                "to send")
    return None


def read_record(run_dir: str) -> dict | None:
    """The run's drive record, nothing where the run was not driven, `NoRow` where it is unreadable.

    Unreadable is a refusal and not an absence: a record that exists says the run was driven, and a
    row written without it would count the oracle's prompts as a person's.
    """
    path = os.path.join(run_dir, RECORD)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise NoRow("drive-record-unreadable", f"{path}: {exc}") from None
    rounds = record.get("rounds") if isinstance(record, dict) else None
    limit = record.get("oracle_rounds") if isinstance(record, dict) else None
    if (not isinstance(rounds, list) or not isinstance(limit, int) or isinstance(limit, bool)
            or limit < 0):
        raise NoRow("drive-record-unreadable", f"{path} states no rounds and no limit")
    for entry in rounds[1:] + _body_entries(record):
        if not isinstance(entry, dict) or not _DIGEST.match(str(entry.get("prompt_sha256"))):
            raise NoRow("drive-record-unreadable", f"{path} carries a round with no prompt digest")
    return record


def oracle_prompts(record: dict | None) -> list[str]:
    """The digests of the prompts the instrument wrote: every round's but the first, the task's,
    and every body round's."""
    if not record:
        return []
    return ([entry["prompt_sha256"] for entry in record["rounds"][1:]]
            + [entry["prompt_sha256"] for entry in _body_entries(record)])


def _body_entries(record: dict) -> list:
    found = record.get("body") if isinstance(record, dict) else None
    entries = found.get("rounds") if isinstance(found, dict) else None
    return entries if isinstance(entries, list) else []


def oracle_rounds(record: dict | None) -> int:
    """How many feedback rounds the loop was allowed: `0` for a run that was not driven."""
    return int(record["oracle_rounds"]) if record else 0


def summary(record: dict, run_id: str) -> dict:
    """What is staged beside the row: the rounds the loop used, of how many it was allowed.

    The row has no field for either, and gets none: the count is a property of how this producer
    drove the run, which the fence already separates, and a reader who needs it finds it here.
    """
    rounds = record.get("rounds") or []
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    return {
        "body_rounds": len(_body_entries(record)),
        "body_stopped": body.get("stopped"),
        "run_id": run_id,
        "oracle_rounds": record.get("oracle_rounds"),
        "passes": len(rounds),
        "feedback_rounds": max(len(rounds) - 1, 0),
        "stopped": record.get("stopped"),
        "rounds": [{key: entry.get(key) for key in
                    ("round", "prompt_sha256", "outcome", "wall_time_ms", "exit")}
                   for entry in rounds],
    }
