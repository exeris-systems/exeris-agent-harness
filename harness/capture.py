"""The contract between the harness and a provider's adapter.

An adapter answers two questions about one run: where the client wrote the log of the session, and
what that log says. It answers the second in counts and digests only. The text of a prompt, of a
file or of a tool argument never leaves an adapter, because those may be private material and a run
record is metadata; what the record carries about the stream is a reference, a digest and a count.

An adapter that cannot answer says so with a **reason**, and a reason is a refusal to write a row,
not a failure of the run: the work happened, its commits are pushed, and what is missing is the
observation. The vocabulary below is closed because these are counted — a producer that invents a
reason per occasion cannot say how often each one fired.

`locate` answers with the path of the run's session log. Where there is none it says so — by
raising `NoRow` where a search was made and did not settle on one file, and by answering the pair
`(None, "adapter-identity-only")` where the adapter has no search to make at all, because that
runtime's log has never been read by one. The second shape is the honest answer of an adapter that
binds identity and claims nothing further, and the caller reads either.

`read` answers with a mapping, and the keys are the run record's own names rather than the
client's. An adapter translates once, where the client's vocabulary is understood; a record
assembled from each client's own names would carry the translation at the far end, where the reader
no longer has the log to check it against.
"""

#: Why a run that happened yields no row. Every one of these is a statement about the instrument
#: rather than about the work, which is why each is counted rather than repaired: a row assembled
#: past one of them would carry a number whose meaning nobody could state afterwards.
REASONS = (
    # The runtime exposes no session log an adapter has actually read. Identity binds at the
    # worktree and nothing more is claimed.
    "adapter-identity-only",
    # The log could not be identified: none under the run's own directory matched the run, or
    # several did and picking one would be a guess.
    "session-not-found",
    "multiple-sessions",
    # More than one model took turns on the main chain. `agent.model_id` is singular and names the
    # model that took the turns, so a run that changed model is two runs and neither is this one.
    "multi-model",
    # A subagent ran under a different model from the run's. The row would report the run's model
    # over work another model did.
    "subagent-model-divergence",
    # The client's own version moved inside the session. The client is part of the model reference,
    # so a session under two versions is two references and the row could state neither.
    "harness-version-moved",
    # The log names no model, or no client version, at all.
    "model-absent",
    "harness-version-absent",
    # The log could not be read as a session.
    "session-unreadable",
    # Below here the refusal is the harness's rather than the adapter's, and the reasons are named
    # in the same vocabulary because they are counted beside the others.
    "bundle-pin-absent",
    "scope-absent",
    "domain-absent",
    "oracle-unmappable",
    "credential-class-absent",
    "prompt-absent",
    "routine-unreadable",
    # A component of the checkout the record hashes is in the tree at the commit the run started
    # from and could not be read out of it, or that commit does not resolve at all. Absent from the
    # tree is a state and hashes as one; unrecoverable is not.
    "agents-file-unreadable",
    "bundle-manifest-unreadable",
    "capture-version-unreadable",
    # The producer and client version this run ran under resolve to no single entry in the fence
    # register. An id nobody wrote down is a fence nobody can say they are on the far side of.
    "fence-unregistered",
    # An identity the organisation writes with was recorded as the model that did the work.
    "publisher-named-as-agent",
    # The run is an arm of a group planned with a human arm, and the producer could not read that
    # arm's measurement. Every row of such a group carries it identically, so a row written without
    # it is a row of a group nobody can interpret.
    "human-baseline-absent",
)


class NoRow(Exception):
    """A run the producer will not write a row for. `reason` is the counted code."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


#: What `read` answers, key by key. Every one of these is a count, a digest or an identifier; none
#: of them is text a person or a repository wrote.
#:
#: ``path``                  the log this was read from
#: ``sha256``                the log's digest, for `execution.event_stream.sha256`
#: ``event_count``           lines the adapter could parse, for `event_stream.event_count`
#: ``model_id``              the model that took the turns, as the vendor spells it
#: ``client`` / ``version``  the client that ran it and the version of it — `agent.harness`
#: ``turns`` / ``tool_calls``            `execution`'s two required counts
#: ``usage``                 token counts under `accounting.usage`'s own four names
#: ``wall_time_ms``          what the runtime says the work took, where it says so at all
#: ``human_prompts``         prompts a person submitted after the first
#: ``permission_denials``    tool calls the client refused
#: ``system_prompt_sha256``  the run's own prompt, hashed where it was read. It is the FIRST
#:                           COMPONENT of the row's `agent.system_prompt_sha256` and not that
#:                           field's value: the record composes it with the routine and the agent
#:                           file in force. The text is hashed here because here is where it is
#:                           read, and it is not carried further.
#: ``capture_level``         how much of `execution` this adapter could observe
#: ``notes``                 lines the producer prints beside the run and writes into no row
#:
#: A key a stream does not carry is answered as `None`, which the record reads as absent. That is
#: the whole of the rule for an optional count: `0` is a measurement of a run nothing refused, and
#: a runtime that reports no refusals at all has not measured one — so the two never share a value,
#: and `capture_level` says which of them the row is.
KEYS = ("path", "sha256", "event_count", "model_id", "client", "version", "turns", "tool_calls",
        "usage", "wall_time_ms", "human_prompts", "permission_denials", "system_prompt_sha256",
        "capture_level", "notes")
