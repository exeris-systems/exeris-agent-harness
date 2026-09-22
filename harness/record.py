"""One run record, assembled from what the run knows about itself and what an adapter read.

The record is the point of the harness: a run that leaves no row is a run nobody can compare with
another. What this module does not do is decide anything. It adds no field the row contract does
not name, it interprets nothing, and every value it writes is either something the run observed or
something the configuration declared — a derivation, stated once, in one place, so that a change to
one is a change to the instrument and can be seen as such.

Where a value cannot be established, no row is written. That is the whole of the fail-closed rule
as it applies to a producer: a convenient default is indistinguishable from a measurement once it
is in the column, and the column is what a later comparison averages.
"""

import datetime
import hashlib
import json
import os
import re

from .capture import NoRow

#: Which oracle may judge a run in which domain, mirroring the producer table the row contract
#: publishes. It is a mirror and not a second source: an id absent from it is not writable, because
#: the row would name a state no reader can look up.
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

#: The organisation's writing identities: the voice, the pen and the hands. None of them is a
#: model. A row naming one in `agent.*` puts an identity into the column a comparison across models
#: groups by, and the comparison then reads as a model's result.
PUBLISHERS = ("exeris-bot", "exeris-inbox", "exeris-agent")

#: The credential classes the record admits. A class outside them is not a fourth kind of ledger;
#: it is a configuration nobody has decided the meaning of.
CREDENTIAL_CLASSES = ("api", "subscription", "local")

#: A fence id is a date followed by lower-case alphanumeric segments. Anything else in a segment —
#: a dot in a version, a capital in a provider — is written as a separator, so that one producer at
#: one client version has one spelling and joins to both halves of the rows it marks.
_NOT_SEGMENT = re.compile(r"[^a-z0-9]+")

#: A fence id as the register writes one, in the code span the register's table holds it in. The id
#: is found here rather than built, so this pattern is what reads the register and not what mints
#: an id.
_FENCE_ID = re.compile(r"`(\d{4}-\d{2}-\d{2}(?:-[a-z0-9]+)+)`")

#: The register a fence id resolves in, inside the contract repository.
FENCES = os.path.join("docs", "fences.md")

#: The file the bundle pin is read out of, at the commit the run started from. A path inside a git
#: tree, so its separator is the tree's and not the platform's.
BUNDLE_MANIFEST = ".agents/manifest.yaml"

#: The bundle pin inside `.agents/manifest.yaml`: the version of the organisation's agent bundle
#: the checkout imports. It is the rules the run was subject to, which is why a run without one
#: yields no row — not the manifest's own schema version, which says nothing about the run.
_IMPORT = re.compile(r"^\s*-\s*bundle:\s*(\S+)\s*$")
_VERSION = re.compile(r"^\s*version:\s*[\"']?(\d+\.\d+\.\d+)[\"']?\s*$")


def names_a_publisher(value) -> bool:
    """Whether a value names one of the organisation's writing identities.

    Compared case-blind and with the suffix the host appends taken off first, so that one name in
    three spellings is one name rather than one refusal and two ways past it.
    """
    if not isinstance(value, str):
        return False
    name = value.strip().lower()
    if name.endswith("[bot]"):
        name = name[:-len("[bot]")]
    return name in PUBLISHERS


def refuse_publisher(agent: dict) -> None:
    """Refuse a row that records a publisher as the model that did the work.

    The bot is the pen, never the agent. The identity a run acted under is a legitimate value and
    has its own field — `execution.principal` — so this refusal costs the row nothing it could
    honestly have said.
    """
    harness = agent.get("harness") or {}
    for field, value in (("provider", agent.get("provider")),
                         ("model_id", agent.get("model_id")),
                         ("harness.client", harness.get("client"))):
        if names_a_publisher(value):
            raise NoRow("publisher-named-as-agent", f"agent.{field} is {value!r}")


def stamp(text: str) -> datetime.datetime:
    when = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=datetime.UTC)


def date_of(text: str) -> str:
    """The UTC date a record is filed under — one date per run, everywhere it appears.

    The inbox files a run by `started_at` in UTC, so the stream path, the row's path and the fence
    carry that same date; a run whose artefacts were dated by when each was written would be filed
    under two days whenever one crossed midnight.
    """
    return stamp(text).strftime("%Y-%m-%d")


def segment(value: str) -> str:
    """One lower-case alphanumeric segment of a fence id."""
    return _NOT_SEGMENT.sub("-", str(value).lower()).strip("-")


def fence(execution_repo: str, provider: str, client_version: str) -> str:
    """The registered fence a harness row is written under, read from the register.

    The id names the producer and the client version, because both are part of what a row means: a
    change to either is a change to the run's conditions, and rows either side of one are never
    summarised in a single figure. The date in it is the day the fence was written, not the day a
    run happened — a run's own date would mint a new id every morning, and a week of one client
    version would then be seven fences nobody could summarise across.

    So the id is resolved rather than composed. One client version is one fence, so exactly one
    entry ends in this producer's suffix; where the register carries none, or carries more than
    one, there is no id this row can assert and no row is written. A new client version is
    registered before its first row, which is the whole of what the register is for.
    """
    suffix = f"-harness-{segment(provider)}-cc-{segment(client_version)}"
    path = os.path.join(execution_repo, FENCES)
    try:
        with open(path, encoding="utf-8") as handle:
            register = handle.read()
    except OSError:
        raise NoRow("fence-unregistered", path) from None
    entries = list(dict.fromkeys(value for value in _FENCE_ID.findall(register)
                                 if value.endswith(suffix)))
    if len(entries) != 1:
        raise NoRow("fence-unregistered",
                    f"{path} resolves {len(entries)} id(s) ending {suffix}")
    return entries[0]


def stream_ref(org: str, streams_repo: str, date: str, repo: str, run_id: str) -> str:
    """Where the run's stream will be, pending the commit that puts it there.

    The mark is honest at the moment it is written: the stream has not been committed, so there is
    no commit to name. It is resolved in staging, before a row reaches an inbox — the rule that a
    record is never rewritten begins where records are kept, and staging is the producer's own.
    """
    return f"{org}/{streams_repo}/streams/{date}/{repo}/{run_id}.jsonl@pending"


def bundle_version(manifest: str | None, source: str = BUNDLE_MANIFEST) -> str:
    """The agent bundle the checkout pins, from `.agents/manifest.yaml` as the run found it.

    The text is read at the commit the run started from and passed in, for the reason the agent
    file is: a run that edited the manifest was subject to what it found, and a pin taken from the
    tree afterwards would record what the run wrote. `oracle.version` is derived from this value on
    a documentation row, so a manifest read late would name the rules the run wrote as the rules it
    was judged by.

    Read with a line scanner rather than a parser because the harness carries no dependencies, and
    the two lines that matter — the bundle an import names and the version it pins — are a fixed
    shape in a file a schema check already governs. A value that is not a three-part version is not
    a pin. Nothing there at all is a checkout that pins no bundle, which is the state, not a
    failure to read one.
    """
    if manifest is None:
        raise NoRow("bundle-pin-absent", source)
    lines = manifest.splitlines()

    pins: dict[str, str] = {}
    current = None
    for line in lines:
        found = _IMPORT.match(line)
        if found:
            current = found.group(1)
            continue
        version = _VERSION.match(line)
        if version and current and current not in pins:
            pins[current] = version.group(1)
    if not pins:
        raise NoRow("bundle-pin-absent", source)
    # The organisation's own bundle where the checkout imports it; otherwise the single pin the
    # file carries. A checkout importing several bundles and none of them the organisation's is a
    # composition this derivation has no rule for, and a guess there would be a version nobody
    # could check the row against.
    for name in ("exeris-agents",):
        if name in pins:
            return pins[name]
    if len(pins) == 1:
        return next(iter(pins.values()))
    raise NoRow("bundle-pin-absent", f"{source} pins {', '.join(sorted(pins))}")


def _component(text: str) -> bytes:
    """One component of the system-prompt hash: stripped of one trailing newline, given exactly one.

    Exactly one, including for a component that is empty. An empty component is a state — no
    routine is configured, the checkout carries no agent file — and it contributes its separator
    like any other, so that three components are never mistaken for two.
    """
    text = str(text)
    if text.endswith("\n"):
        text = text[:-1]
    return (text + "\n").encode("utf-8")


def system_prompt_sha256(prompt_digest: str, routine: str, agents_file: str) -> str:
    """The instructions this run was subject to, as one digest.

    Three components in this order: the prompt the run was given, the routine the configuration
    names, and the agent file in force at the commit the run started from. The client's own system
    prompt is deliberately outside it — a producer cannot read that text — and a change to it is
    proxied, imperfectly, by the client version on the row. That is a stated hole in the model
    reference rather than a covered case.

    The prompt enters as its own digest rather than as its text. It is hashed where it is read —
    when it is passed, or in the adapter — and the text is not carried to this point, because a
    prompt may be private material and a run record is metadata. A change to this composition is a
    change to the instrument and writes a fence.
    """
    if not prompt_digest:
        raise NoRow("prompt-absent",
                    "no prompt was passed and the session exposes no first prompt")
    body = b"".join(_component(part) for part in (prompt_digest, routine, agents_file))
    return hashlib.sha256(body).hexdigest()


def oracle(domain: str, bundle: str) -> dict:
    """Which oracle judged the run, at what version, in what calibration state.

    Every suite is `not-run`, and `not-run` is not a pass: none of them has been run as a suite.
    Naming the suite anyway is how a row says which pass it is waiting for, and the result repeats
    the status because the field holds a score as it was published and no score was.
    """
    if not domain:
        raise NoRow("domain-absent", "the configuration declares no domain for this repository")
    if domain not in ORACLES:
        raise NoRow("oracle-unmappable", domain)
    oracle_id, suite = ORACLES[domain]
    return {
        "id": oracle_id,
        "version": bundle if oracle_id == "docs-guardrails" else SCB_VERSION,
        "calibration": {"suite": suite, "status": "not-run", "result": "not-run"},
    }


def accounting(credential_class, usage: dict) -> dict:
    """The ledger this run belongs to, and its token counts.

    No currency figure, in any mode. Under a subscription there is no per-run price, and a figure
    computed from a price list is imputed; once it shares a column with a reported one the two are
    indistinguishable, and every later cost conclusion is corrupted undetectably.
    """
    if credential_class not in CREDENTIAL_CLASSES:
        raise NoRow("credential-class-absent", str(credential_class))
    out = {"mode": credential_class}
    if usage:
        out["usage"] = dict(usage)
    return out


def assemble(*, manifest: dict, repo_config, facts, visibility: str, bundle: str, dirty: bool,
             result_commits: list, ended_at: str, capture_version: str, fence: str, ref: str,
             prompt_digest: str, routine: str, agents_file: str) -> dict:
    """The run record, complete, in the order the contract lists its components."""
    started = stamp(manifest["started_at"])
    wall_time_ms = max(int((stamp(ended_at) - started).total_seconds() * 1000), 0)

    scope = manifest.get("scope")
    if not scope:
        raise NoRow("scope-absent", "the row contract requires the kind of workload a run was")

    agent = {
        "provider": manifest["provider"],
        "model_id": facts["model_id"],
        # The runtime exposes no dated snapshot for the model that takes the turns, so the row
        # carries the alias marked as unresolved. The bare alias is refused by the contract,
        # because one alias may name different weights at different times and a row that cannot
        # tell is worse than a row that says it cannot.
        "model_snapshot": f"unresolved:{facts['model_id']}",
        "harness": {"client": facts["client"], "version": facts["version"]},
        "system_prompt_sha256": system_prompt_sha256(prompt_digest, routine, agents_file),
    }
    refuse_publisher(agent)

    execution = {
        "turns": facts["turns"],
        "tool_calls": facts["tool_calls"],
        "wall_time_ms": wall_time_ms,
        "event_stream": {"ref": ref, "sha256": facts["sha256"],
                         "event_count": facts["event_count"]},
        "capture_level": facts["capture_level"],
        # Who acted, as opposed to who was asked. It is here and never in `agent.*`.
        "principal": dict(manifest["principal"]),
        "human_prompts": facts["human_prompts"],
        "permission_denials": facts["permission_denials"],
        # An empty array is a measurement: the harness owns the run's worktree and watched its
        # branch, so a run that committed nothing is a run that committed nothing.
        "result_commits": list(result_commits),
    }

    row = {
        "run_id": manifest["run_id"],
        "started_at": manifest["started_at"],
        "workload": {
            "fingerprint": manifest["task"],
            "domain": repo_config.domain or "",
            "scope": scope,
        },
        "agent": agent,
        "repository_state": {
            "repository": manifest["repo"],
            "visibility": visibility,
            "commit": manifest["base_sha"],
            "bundle_version": bundle,
            "dirty": bool(dirty),
        },
        "execution": execution,
        "accounting": accounting(repo_config.provider_credential, facts["usage"]),
        "oracle": oracle(repo_config.domain or "", bundle),
        # The oracle's suite has not been run as a suite, so neither its pass nor its failure is
        # admissible as a label. Fail-closed says exactly that.
        "outcome": "UNKNOWN",
        "instrument": {
            "capture_version": capture_version,
            "fence": fence,
        },
    }
    # A group a run was never planned into cannot be declared afterwards, and an unplanned task was
    # never an arm of anything: the pairing travels only with a registry entry.
    pairing = manifest.get("pairing")
    if pairing and str(manifest["task"]).startswith("reg:"):
        row["pairing"] = dict(pairing)
    return row


def capture_version(execution_repo: str) -> str:
    """The version of the row contract a row is written against, from the contract itself.

    Read rather than restated: it is the contract's version and never the producer's, so a copy
    kept here would be a second source that drifts in one of them.
    """
    path = os.path.join(execution_repo, "schemas", "VERSION")
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        raise NoRow("capture-version-unreadable", path) from None
    if not re.match(r"^\d+\.\d+\.\d+$", value):
        raise NoRow("capture-version-unreadable", f"{path} holds {value!r}")
    return value


#: The organisation's pull-request template, as the headings and fields a body has to carry. A run
#: opens its pull request as a draft with the classification unanswered, because those answers are
#: the accountable person's: the template gate exempts a draft precisely so that the person who
#: marks it ready is the one who filled it in.
_CLASSIFICATION = (
    ("Scope class", "<runtime hot path | runtime non-hot | test-tooling | docs-only>"),
    ("Wall impact", "<none | from-module → to-module>"),
    ("Generated files touched", "<yes | no | n/a>"),
    ("TCK obligation", "<satisfied | debt #N | n/a>"),
    ("Compatibility impact", "<none | additive | breaking (ADR-NNN)>"),
    ("Cross-repo impact", "<none | repo: what must change>"),
    ("ADRs referenced", "<ADR-NNN, … | none>"),
    ("Evidence state", "<citable | unartifacted | n/a>"),
)


def pull_request_body(owner_login: str, run_id: str) -> str:
    """The body of the draft pull request a run's work becomes.

    Exactly one `Owner:` line, naming the organisation member accountable for what the run
    produced. Accountability does not move to the author field when the author is an application:
    it moves to this line, and the named person answers for every part of the change in review
    exactly as an author would.
    """
    lines = [
        "Motivation:",
        "<!-- Why this change exists: the constraint, failure or measurement. -->",
        "",
        "Modification:",
        "<!-- What changed at the level of contracts, seams and behaviour. -->",
        "",
        "Result:",
        "<!-- What is different now. What is explicitly NOT covered. -->",
        "",
        "## Classification",
    ]
    lines += [f"{name}: {placeholder}" for name, placeholder in _CLASSIFICATION]
    lines += [
        "",
        "## Verification",
        "<!-- The exact commands run after the last push, and what they prove. -->",
        "",
        f"Owner: @{owner_login}",
        f"Exeris-Run: {run_id}",
    ]
    return "\n".join(lines) + "\n"


def inbox_pull_request_body(rows: int, date: str, repositories) -> str:
    """The body of the pull request that carries a batch of rows into the inbox.

    It carries no `Owner:` line, and that is the rule rather than an omission: the line is required
    on a pull request the execution identity authored and forbidden on every other, and this one is
    opened by the person whose sign-off the batch is. The classification is answered rather than
    left as placeholders for the same reason — this is not a draft somebody else will finish.
    """
    named = ", ".join(sorted(set(repositories))) or "none"
    return "\n".join([
        "Motivation:",
        f"A local harness captured {rows} run record(s). Rows produced outside the inbox are lost "
        f"unless something carries them in, and the producer validates them before it asks.",
        "",
        "Modification:",
        f"{rows} run record(s) under `inbox/{date}/runs/`, one file per record, named by the id "
        f"inside it. Source repositories: {named}.",
        "",
        "Result:",
        "The rows are in the inbox and nothing reads them. No aggregation, no comparison, no "
        "sentence about a model — the inbox holds records.",
        "",
        "## Classification",
        "Scope class: docs-only",
        "Wall impact: none",
        "Generated files touched: yes",
        "TCK obligation: n/a",
        "Compatibility impact: none",
        "Cross-repo impact: none",
        "ADRs referenced: ADR-086, ADR-087",
        "Evidence state: citable",
        "",
        "## Verification",
        "`python3 tools/inbox_validate.py --root .` — run by the producer over these rows before "
        "this pull request was opened, and green.",
        "",
        "Refs: ADR-086, ADR-087",
    ]) + "\n"


def write(path: str, row: dict) -> str:
    """One record per file, sorted and newline-terminated, under the id it calls itself."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path
