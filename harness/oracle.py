"""Who judges a run, and what that judgement is admissible as.

This module is the seam. `judge` is the one place an outcome enters the harness — a run record's
`outcome` and a human baseline's are the same question asked about the same work, so they are asked
here, once, and neither caller carries a second answer.

**Today it judges nothing.** Every domain answers `UNKNOWN` with its suite `not-run`, because no
suite has been run as a suite; an oracle whose pass has never been contradicted by a known-broken
input is unvalidated, and neither its pass nor its failure is admissible as a label. That is not a
placeholder to be tidied away: it is the fail-closed rule, and a judge that returned a pass before
its suite did would be writing the label the rule exists to refuse.

What replaces it is a judge per domain, each one gated on its own suite. Until such a suite
publishes a result, the honest answer from this module is the one below.
"""

#: Which oracle may judge a run in which domain, mirroring the register the row contract publishes.
#: It is a mirror and not a second source: an id absent from it is not writable, because the row
#: would name a state no reader can look up.
#:
#: The keys are the domains the record admits, spelled as the contract spells them. A second
#: spelling accepted here would be a domain name that resolves to an oracle for the producer and to
#: nothing for the reader, and a domain whose name averages two oracles' populations is the
#: meaningless figure the record exists to prevent — so a configuration that names a domain the
#: contract does not know writes no row.
ORACLES = {
    "docs-sweep": ("docs-guardrails", "docs-mutation-v1"),
    "construction": ("scb", "oracle-selftest"),
}

#: The construction oracle's own version. The documentation oracle's version is the bundle pinned
#: in the checkout — the rules the run was subject to — so it is read from the row rather than set
#: here.
SCB_VERSION = "1.3"

#: The outcomes a judgement may carry, as the row contract spells them. `UNKNOWN` is a run whose
#: gates did not run; `UNREACHABLE` is a run that never got far enough to be judged.
OUTCOMES = ("TRUE_DONE", "FALSE_DONE", "UNKNOWN", "UNREACHABLE")

#: A suite's status. Three-valued, and `not-run` is not a pass.
STATUSES = ("pass", "fail", "not-run")


def suite(domain: str) -> str | None:
    """The suite that calibrates the oracle of this domain, where the register names one."""
    entry = ORACLES.get(str(domain))
    return entry[1] if entry else None


def judge(worktree: str, domain: str) -> dict:
    """What the oracle of `domain` says about the work in `worktree`, and at what calibration.

    `{"outcome": …, "calibration": {"suite": …, "status": …, "result": …}}` — the outcome as the
    row contract spells it, and the state of the suite that would make it admissible. The two
    travel together because one without the other is uninterpretable: a label from an uncalibrated
    oracle is a claim its instrument cannot support, and a calibration state with no label says
    nothing about the run.

    Naming the suite while it has not run is how a row says which pass it is waiting for, and
    `result` repeats the status because that field holds a score as it was published and no score
    was.
    """
    return {
        "outcome": "UNKNOWN",
        "calibration": {"suite": suite(domain), "status": "not-run", "result": "not-run"},
    }


def outcome_of(judgement: dict) -> str:
    """The judgement's outcome, fail-closed: nothing but `UNKNOWN` while the suite has not passed.

    Applied to every outcome this harness writes — a run record's and a human baseline's alike —
    because the rule is about the instrument and not about who was measured. A label admitted here
    on an uncalibrated suite would be refused by the contract at the far end, and a producer that
    let one through would be asking a validator to enforce its own fail-closed rule for it.
    """
    calibration = judgement.get("calibration") or {}
    outcome = judgement.get("outcome")
    if outcome not in OUTCOMES:
        return "UNKNOWN"
    if calibration.get("status") != "pass" and outcome not in ("UNKNOWN", "UNREACHABLE"):
        return "UNKNOWN"
    return outcome
