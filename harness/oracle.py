"""Who judges a run, and what that judgement is admissible as.

This module is the seam. `judge` is the one place an outcome enters the harness — a run record's
`outcome` and a human baseline's are the same question asked about the same work, so they are asked
here, once, and neither caller carries a second answer.

A documentation run is judged by the oracle the execution repository publishes. It is **imported
from the configured clone and never copied here**: a copy is a second implementation of the gates,
free to drift from the one the calibration suite was run against, and a row's `oracle.version`
would then name rules that never judged it.

The calibration is read from the file that suite publishes, at the moment a row is written. It is
not decided here and not remembered between runs, because a row is interpretable only against the
calibration in force when it was written, and a pair typed into a producer is a second owner of a
number that has a home.

Fail-closed (ADR-086 §E.19) is applied in `outcome_of` and nowhere else: while the suite has not
passed, neither a pass nor a failure is admissible as a label, whatever the gates found. A
judgement therefore carries what the oracle said, and `outcome_of` carries what a row may say —
two values because they answer two questions, and a producer that collapsed them could not explain
a row reading `UNKNOWN` over gates that all passed.

The gates are the run's local evidence and are staged beside the run rather than written to its
row. A per-gate column is a column about one oracle's internals: it is not comparable across
oracles, it changes when a gate is renamed, and the record's own question is which oracle judged
the run and what it concluded.

Construction is `UNKNOWN` at `not-run`, and that is not a placeholder. Its suite has not been run
as a suite; an oracle whose pass has never been contradicted by a known-broken input is
unvalidated, and neither its pass nor its failure is admissible as a label.
"""

import importlib
import json
import os
import sys

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

#: The construction oracle's own version. The documentation oracle's version is the judgement's:
#: the gates are the rules of the bundle the judged checkout pins, so the version in force is the
#: one the oracle read out of the tree it ran over.
SCB_VERSION = "1.3"

#: The outcomes a judgement may carry, as the row contract spells them. `UNKNOWN` is a run whose
#: gates did not run; `UNREACHABLE` is a run that never got far enough to be judged.
OUTCOMES = ("TRUE_DONE", "FALSE_DONE", "UNKNOWN", "UNREACHABLE")

#: A suite's status. Three-valued, and `not-run` is not a pass.
STATUSES = ("pass", "fail", "not-run")
NOT_RUN = "not-run"
PASS = "pass"
UNKNOWN = "UNKNOWN"
UNREACHABLE = "UNREACHABLE"

#: The domain the published oracle judges, and where that oracle and its calibration live inside
#: the execution repository. Both are paths into one checkout, so the gates a run was judged by and
#: the calibration written beside its outcome can never come from two versions of the oracle.
DOCS_DOMAIN = "docs-sweep"
DOCS_PACKAGE = "oracles"
DOCS_MODULE = f"{DOCS_PACKAGE}.docs_guardrails"
DOCS_SELFTEST = (DOCS_PACKAGE, "docs-guardrails", "oracle-selftest.json")


def suite(domain: str) -> str | None:
    """The suite that calibrates the oracle of this domain, where the register names one."""
    entry = ORACLES.get(str(domain))
    return entry[1] if entry else None


def _uncalibrated(domain: str) -> dict:
    """A calibration that admits nothing, naming the suite a row of this domain waits for.

    Naming the suite while it has not run is how a row says which pass it is waiting for, and
    `result` repeats the status because that field holds a score as it was published and no score
    was.
    """
    return {"suite": suite(domain), "status": NOT_RUN, "result": NOT_RUN}


def calibration(domain: str, execution_repo: str | None) -> tuple[dict, str]:
    """`(calibration, reason)` — the state of this domain's suite as that suite published it.

    The file is read on every judgement rather than carried between them, because the value a row
    carries is the one in force when the row was written: a remembered pair would let a suite that
    has since failed keep admitting labels.

    Anything that is not a well-formed published status and score is `not-run` with a reason. A
    file naming another suite is one of those: a result published by some other suite is not this
    oracle's calibration, and reading it as one would admit labels on the strength of a pass that
    was never about these gates.
    """
    registered = suite(domain)
    if domain != DOCS_DOMAIN:
        return _uncalibrated(domain), ""
    if not execution_repo:
        return (_uncalibrated(domain),
                "the configuration names no execution_repo, so no calibration can be read")
    path = os.path.join(execution_repo, *DOCS_SELFTEST)
    try:
        with open(path, encoding="utf-8") as handle:
            published = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return _uncalibrated(domain), f"no calibration at {path}: {exc}"
    if not isinstance(published, dict):
        return _uncalibrated(domain), f"{path} publishes no calibration object"
    named = published.get("suite")
    if named != registered:
        return (_uncalibrated(domain),
                f"{path} publishes the result of {named!r} and not of {registered!r}")
    status, result = published.get("status"), published.get("result")
    if status not in STATUSES or not isinstance(result, str) or not result:
        return (_uncalibrated(domain),
                f"{path} publishes no status and score this oracle is calibrated by")
    out = {"suite": registered, "status": status, "result": result}
    run_at = published.get("run_at")
    if isinstance(run_at, str) and run_at:
        out["run_at"] = run_at
    return out, ""


def _docs_oracle(execution_repo: str):
    """The published oracle, imported out of the execution repository's own checkout.

    The checkout goes in front of `sys.path`, and the package is dropped from the module table when
    what is loaded came from anywhere else, so the module answering here is always the one in the
    checkout the configuration names. A module left over from another path would judge under rules
    that cannot be looked up from the row it produced.
    """
    root = os.path.abspath(execution_repo)
    loaded = sys.modules.get(DOCS_MODULE)
    if loaded is not None:
        found = os.path.abspath(getattr(loaded, "__file__", "") or "")
        if not found.startswith(root + os.sep):
            for name in [n for n in sys.modules
                         if n == DOCS_PACKAGE or n.startswith(DOCS_PACKAGE + ".")]:
                del sys.modules[name]
            loaded = None
    if loaded is not None:
        return loaded
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    return importlib.import_module(DOCS_MODULE)


def _docs_judgement(worktree: str, execution_repo: str | None,
                    index: str | None) -> tuple[dict, str]:
    """`(judgement, reason)` from the published oracle, or `UNKNOWN` and why there is none.

    `index` is the central registry a checkout that is not itself the registry is checked against.
    Where the configuration names none, that gate is `not-run` inside the oracle, which is the
    honest state: the alternative is a check that fetches over the network, and a check that
    reports the network is not a gate.
    """
    empty = {"outcome": UNKNOWN, "version": None, "gates": []}
    if not execution_repo:
        return empty, "the configuration names no execution_repo, so no oracle can be imported"
    try:
        module = _docs_oracle(execution_repo)
    # A checkout may hold no oracle at all, or one that does not import. Either way nothing has
    # judged the run, and nothing judged is `UNKNOWN`.
    except Exception as exc:
        return empty, f"no oracle under {execution_repo}: {exc}"
    try:
        answer = module.judge(worktree, index=index).as_dict()
    # The oracle runs the organisation's own checkers as subprocesses over a tree neither it nor
    # this harness controls. An instrument that raised has said nothing about the run, which is
    # `UNKNOWN`; letting the exception out would lose a push and a pull request that already exist.
    except Exception as exc:
        return empty, f"the oracle reached no verdict: {exc}"
    outcome = answer.get("outcome")
    if outcome not in OUTCOMES:
        return empty, f"the oracle answered {outcome!r}, which is not an outcome"
    return {"outcome": outcome, "version": answer.get("oracle_version") or None,
            "gates": list(answer.get("gates") or ())}, ""


def judge(worktree: str, domain: str, *, execution_repo: str | None = None,
          index: str | None = None) -> dict:
    """What the oracle of `domain` says about the work in `worktree`, and at what calibration.

    `{"outcome", "calibration", "version", "gates", "reason"}` — what the oracle concluded, the
    state of the suite that decides whether that conclusion is admissible, the version of the rules
    it applied, the gates behind it, and what could not be read where something could not be. The
    outcome and the calibration travel together because one without the other is uninterpretable: a
    label from an uncalibrated oracle is a claim its instrument cannot support, and a calibration
    state with no label says nothing about the run.

    The outcome here is the oracle's own; `outcome_of` is what a row may carry.
    """
    state, why = calibration(domain, execution_repo)
    if domain == DOCS_DOMAIN:
        found, reason = _docs_judgement(worktree, execution_repo, index)
        return {"outcome": found["outcome"], "calibration": state, "version": found["version"],
                "gates": found["gates"], "reason": "; ".join(x for x in (why, reason) if x)}
    return {"outcome": UNKNOWN, "calibration": state, "version": SCB_VERSION, "gates": [],
            "reason": why}


def outcome_of(judgement: dict) -> str:
    """The judgement's outcome, fail-closed: nothing but `UNKNOWN` while the suite has not passed.

    Applied to every outcome this harness writes — a run record's and a human baseline's alike —
    because the rule is about the instrument and not about who was measured. A label admitted here
    on an uncalibrated suite would be refused by the contract at the far end, and a producer that
    let one through would be asking a validator to enforce its own fail-closed rule for it.
    """
    calibrated = judgement.get("calibration") or {}
    outcome = judgement.get("outcome")
    if outcome not in OUTCOMES:
        return UNKNOWN
    if calibrated.get("status") != PASS and outcome not in (UNKNOWN, UNREACHABLE):
        return UNKNOWN
    return outcome


def evidence(judgement: dict, *, run_id: str, domain: str) -> dict:
    """The run's local record of how it was judged, for staging beside the run.

    Both outcomes are written. The oracle's is what the gates add up to; the admitted one is what a
    row of this run may carry, and the calibration between them is the whole of the difference — a
    file holding only the second could not explain a run whose gates all passed and whose row reads
    `UNKNOWN`.
    """
    registered = ORACLES.get(str(domain))
    return {
        "run_id": run_id,
        "domain": domain,
        "oracle_id": registered[0] if registered else None,
        "oracle_version": judgement.get("version"),
        "outcome": judgement.get("outcome"),
        "admitted": outcome_of(judgement),
        "calibration": judgement.get("calibration") or {},
        "gates": list(judgement.get("gates") or ()),
        "reason": judgement.get("reason") or "",
    }
