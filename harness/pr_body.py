"""The pull request's body: written by the arm that did the work, checked by the organisation's gate.

The arm is the one party that knows why its change exists, what it changed and which commands it
ran, so the body is its to write. What the harness adds is what the arm must not state about
itself: the `Owner:` line, naming the member accountable for the work, and the `Exeris-Run:` line
linking the pull request to its run. A body is sent only after the organisation's own template
check — the script the `pr-body` gate runs, from the clone `[pull_request] body_check` names — has
passed it as the execution identity's body. The gate exempts a draft, and a draft this harness
opens is the one place an unfilled template could otherwise go unread.

**The file is the contract.** Every run has `<run>/body/pr-body.md`; a driven run's arm writes it in
the round after `TRUE_DONE`, and an arm a person sits with writes it where `EXERIS_PR_BODY` says.
`close-run` reads it, composes it and checks it before anything is pushed, so a body that does not
pass leaves no branch and no pull request behind.

**The request and the feedback are the instrument's words.** The request is a fixed template around
the organisation's pull-request template, and the feedback is the check's own messages, verbatim,
for the reason the oracle's feedback is: two rows' prompts differ in what the instrument said and in
nothing else, and a hint beyond it would be steering.
"""

import os
import re
import subprocess
import sys
import tempfile

from . import runstate

#: Where a run's body is written, inside the run's directory.
DIRECTORY = "body"
FILE = "pr-body.md"

#: The variable an arm is told the body's path by.
VARIABLE = "EXERIS_PR_BODY"

#: The checker's file name. A configured path naming any other file is not the organisation's gate.
CHECKER = "pr_body_check.py"

#: The organisation's pull-request template: the sections a body carries, in order, and the
#: classification fields with the values each admits.
SECTIONS = (
    ("Motivation:", "Why this change exists: the constraint, failure or measurement."),
    ("Modification:", "What changed at the level of contracts, seams and behaviour."),
    ("Result:", "What is different now. What is explicitly NOT covered."),
)
CLASSIFICATION = (
    ("Scope class", "<runtime hot path | runtime non-hot | test-tooling | docs-only>"),
    ("Wall impact", "<none | from-module → to-module>"),
    ("Generated files touched", "<yes | no | n/a>"),
    ("TCK obligation", "<satisfied | debt #N | n/a>"),
    ("Compatibility impact", "<none | additive | breaking (ADR-NNN)>"),
    ("Cross-repo impact", "<none | repo: what must change>"),
    ("ADRs referenced", "<ADR-NNN, … | none>"),
    ("Evidence state", "<citable | unartifacted | n/a>"),
)
VERIFICATION = "The exact commands run, and what each showed."

#: What the arm is asked, once the oracle has passed its tree. `{path}` is the body's file and
#: `{template}` the template above.
REQUEST = ("The organisation's checks pass on this tree. One step remains: the pull request body.\n"
           "\n"
           "Write it to {path}, in the organisation's template below, filled from the work in this "
           "session: why the change exists, what changed, what is different now and what is not "
           "covered, every classification line answered with one of the values it lists, and "
           "under Verification the commands you ran and what they showed. After Verification, a "
           "`Refs: ADR-NNN, ADR-NNN` line names every ADR the change implements or amends. Do "
           "not write `Owner:` or `Exeris-Run:` lines: they are added for you. Do not change, "
           "stage or commit anything in the repository.\n"
           "\n"
           "{template}")
FEEDBACK_HEAD = ("The pull request body at {path} does not pass the organisation's template check, "
                 "which reported:\n")
FEEDBACK_LINE = "- {message}\n"
FEEDBACK_TAIL = ("\nRewrite the file so that the check passes. Do not change, stage or commit "
                 "anything in the repository.\n")

#: The variables the checker is started with, and no others. It reads a file and a flag, so it
#: needs an interpreter and a locale; a token in the harness's own environment is not its to hold,
#: and what it prints is sent back into the session and kept with the run's stream.
CHECKER_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")

#: One finding as the checker prints it: `::error file=…,line=…,title=…::<message>`.
_FINDING = re.compile(r"^::error [^:]*::(?P<message>.+)$")

#: A login, as GitHub admits one, with the `[bot]` suffix an application's carries.
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?")

#: A path the checker is started from: one line of text, no control character.
_PATH = re.compile(r"[^\x00-\x1f\x7f]+")

#: The paths an ADR lives under, in the two layouts the gate knows: a code repository's
#: `docs/adr/` and the registry's own root `adr/`.
_ADR_PATH = re.compile(r"(?:^|/)docs/adr/|^adr/")


class CheckUnavailable(Exception):
    """The organisation's check could not be run, so no body can be said to pass it."""


def path(run_dir: str) -> str:
    """The body's file in a run's directory."""
    return os.path.join(run_dir, DIRECTORY, FILE)


def prepare(run_dir: str) -> str:
    """The body's directory, created owner-only, and the file's path."""
    os.makedirs(os.path.join(run_dir, DIRECTORY), mode=runstate.DIR_MODE, exist_ok=True)
    return path(run_dir)


def template() -> str:
    """The template as the request shows it."""
    lines = []
    for heading, prompt in SECTIONS:
        lines += [heading, f"<!-- {prompt} -->", ""]
    lines.append("## Classification")
    lines += [f"{name}: {placeholder}" for name, placeholder in CLASSIFICATION]
    lines += ["", "## Verification", f"<!-- {VERIFICATION} -->", ""]
    return "\n".join(lines)


def request(body_path: str) -> str:
    """The prompt asking the arm for its body."""
    return REQUEST.format(path=body_path, template=template())


def feedback(body_path: str, messages) -> str:
    """The prompt sending the check's findings back, verbatim."""
    return (FEEDBACK_HEAD.format(path=body_path)
            + "".join(FEEDBACK_LINE.format(message=message) for message in messages)
            + FEEDBACK_TAIL)


def compose(written: str, owner_login: str, run_id: str) -> str:
    """The body the pull request carries: the arm's text and the two lines the harness owns."""
    return (written.rstrip() + "\n\n"
            + f"Owner: @{owner_login}\n"
            + f"Exeris-Run: {run_id}\n")


def touches_adr(paths) -> bool:
    """Whether a change touches an ADR, in the sense the gate asks it."""
    return any(_ADR_PATH.search(entry) for entry in paths)


def checker(configured: str | None) -> str:
    """The organisation's checker, resolved, or `CheckUnavailable` naming what is wrong."""
    if not configured:
        raise CheckUnavailable("the configuration names no [pull_request] body_check, so no body "
                               "can be checked before it is sent")
    resolved = os.path.realpath(configured)
    if os.path.basename(resolved) != CHECKER or not os.path.isfile(resolved):
        raise CheckUnavailable(f"[pull_request] body_check names {resolved}, which is not the "
                               f"organisation's {CHECKER}")
    return resolved


def check(checker_path: str, body: str, *, author: str, adr_touched: bool,
          workdir: str) -> list[str]:
    """The check's findings on `body` as `author`'s pull request: nothing where it passes.

    The body is handed over as a file inside `workdir`, owner-only, and removed afterwards. A check
    that fails without saying why is `CheckUnavailable`, never a pass.
    """
    if not _LOGIN.fullmatch(author):
        raise CheckUnavailable(f"{author!r} is not a login the check can be asked about")
    if not _PATH.fullmatch(checker_path):
        raise CheckUnavailable("the checker's path carries a control character")
    environment = {key: value for key, value in os.environ.items() if key in CHECKER_ENVIRONMENT}
    environment["GUARDRAILS_ADR_TOUCHED"] = "1" if adr_touched else "0"
    descriptor, body_file = tempfile.mkstemp(prefix="pr-body-", suffix=".md", dir=workdir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
        try:
            done = subprocess.run([sys.executable, checker_path, "--body-file", body_file,
                                   "--author", author],
                                  capture_output=True, text=True, env=environment, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CheckUnavailable(f"{CHECKER} could not be run: {exc}") from None
    finally:
        os.unlink(body_file)
    found = [match.group("message") for match in map(_FINDING.match, done.stdout.splitlines())
             if match]
    if done.returncode and not found:
        raise CheckUnavailable(f"{CHECKER} exited {done.returncode} and reported nothing: "
                               f"{done.stderr.strip()[-300:]}")
    if found and not done.returncode:
        raise CheckUnavailable(f"{CHECKER} reported findings and exited 0")
    return found


def read(body_path: str) -> str | None:
    """The arm's text, or nothing where the file is absent or empty."""
    try:
        with open(body_path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    return text if text.strip() else None
