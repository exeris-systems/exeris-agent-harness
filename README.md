# exeris-agent-harness

The harness that gives work carried out by an agent an identity of its own.

Before it, every commit in these repositories came from one login: the maintainer's own work, the
work an agent was steered through line by line, and the work an agent carried out end to end, all
indistinguishable in the commit graph and therefore indistinguishable in any dataset built from it.
The harness separates them by giving the third kind its own principal — the organisation's
execution identity, a GitHub App — and by binding that identity to a worktree rather than to a
shell, so that the boundary holds when the agent opens a second terminal.

**V0 is five commands: one opens a run, one drives it headlessly with the oracle in the loop, one
closes it, one carries what it produced, and one measures the person doing the same work.** A run
is:

- a **worktree** on a branch of its own, cut from `origin/<default>`: the attribution boundary.
  Anything committed inside it is the run's, including a line a person types there — that is
  steering. A change that should count as a person's is made in the person's own checkout.
- a **token** the identity minted for itself, good for an hour, scoped to the organisation over
  https and to nothing else. A run that cannot mint it fails; it never falls back to a person's
  credential, and where the helper does not answer, git fails instead of asking the terminal. A run
  that outlives the hour is reopened rather than refreshed, and `status` carries each run's expiry
  so that the end of a long one is a line on the listing rather than a push refused for reasons of
  its own.
- a **git configuration** bound to the tree: the identity's `user.*`, a credential helper that
  answers for one organisation, ssh remotes rewritten to https before git resolves them, and
  signing off. It is bound at worktree scope, which git reads after the repository's own config,
  so a clone that configures a credential helper or a hooks path of its own does not reach inside
  the run.
- an **environment** that drops what the shell it was opened from carried — a person's API tokens,
  an agent socket, an askpass program — before it adds the run's own.
- a **`prepare-commit-msg` hook** that stamps every commit with the run's id, so a commit can be
  joined to the record that observed it.
- an **arm** it was opened under: a model behind a client, under a ledger, launched by an adapter,
  declared together in one `[providers.<name>]` table. `--provider` names that table, so the vendor
  the row records, the ledger it is billed to and the snapshot of the weights that ran are what the
  arm declared and not what the repository happened to be configured with.
- a **manifest** saying what the run is — task, repository, arm, branch, base commit, the
  accountable owner, and the principal that will act.
- a **record**, written when the run closes: one row in the shape of the layer's row contract,
  saying what the run was, what it did and under what conditions — and never what it produced. A
  value the run could not establish is not defaulted; where one is missing there is no row, and the
  reason is named and counted rather than repaired.

The identity's ceiling is its App permissions and the organisation's rulesets;
[`policy/README.md`](policy/README.md) is the map to both.

## The commands

```sh
bin/exeris-agent open-run --repo exeris-systems/exeris-docs --task reg:<id> --provider claude \
                          --scope <scope>
source ~/.local/state/exeris-agent/runs/<ULID>/env
cd     ~/.local/state/exeris-agent/runs/<ULID>/wt
#   … the agent works in that tree …
bin/exeris-agent close-run --run <ULID>
bin/exeris-agent flush
```

`open-run` prints the second and third lines; `--launch` runs the arm's adapter in the worktree
instead of printing them. `--provider` names a `[providers.<name>]` table, or one of the built-in
defaults — `claude`, `codex`, `gemini` — which declare a vendor CLI's own vendor and adapter and
leave the ledger to the table, because which ledger a vendor CLI bills to is a property of the
login it runs under. `--task adhoc` opens an unplanned run, which is capturable but never paired,
so `--group` requires a registry entry. `exeris-agent status` lists the runs on the machine.

`drive` runs the arm with nobody at it, which is how a paired group measures what the work cost to
reach `TRUE_DONE` rather than what one pass happened to leave:

```sh
bin/exeris-agent open-run --repo exeris-systems/exeris-docs --task reg:<id> --provider claude \
                          --scope <scope> --prompt-file <task.md>
bin/exeris-agent drive     --run <ULID> --oracle-rounds 3
bin/exeris-agent close-run --run <ULID>
```

It launches the arm's adapter headless in the run's tree, under the environment `open-run` wrote,
with every directory the repository's `readable` names passed as `--add-dir`. After each pass the
oracle judges the tree. Where the admitted outcome is `FALSE_DONE` and rounds remain, the **same
session** is resumed — the client's own session id, `--resume` for Claude Code and `--conversation`
for Antigravity, read from what the previous pass printed — with a prompt that is a fixed template
around each failing gate's check and detail, verbatim, and nothing else. The loop stops at
`TRUE_DONE`, at `UNKNOWN` (an instrument that could not judge is never looped on), or when
`--oracle-rounds` feedback rounds have been sent; `0`, the default, is a single pass. A client that
names no session to resume is refused a loop rather than given a new session.

Each round is recorded in `<run>/drive.json` — its number, the digest of its prompt, the outcome
after it and the time the pass took — and the feedback prompts are kept beside it in `<run>/drive/`,
because they are instrument output and not the task. `close-run` reads that record: the oracle's
prompts are not counted in `execution.human_prompts` (a recorded one the session does not hold is a
refusal, not a zero), the row sits on the fence `…-harness-<adapter>-oracle<N>-cc-…` for a loop
allowed `N` rounds (with `-v2` after it where the second generation of the documentation oracle
judged the row, and `-mcp` after that where the arm was given the MCP server — see *The MCP server
for the arms*), and the rounds used are staged in `staging/drive.json` and printed — the row
has no field for them. `drive` is a command of its own rather than `open-run --drive` because
`--launch` hands the process to the client for a person to work in, and a loop has to outlive the
pass it launched.

A loop that stops at `TRUE_DONE` has one step left: the same session is asked for the pull
request's body, written to `<run>/body/pr-body.md` in the organisation's template, and the
organisation's own template check (`[pull_request] body_check`) is run over it. Its findings, and
nothing else, are sent back for up to `--body-rounds` rounds (2 by default). The body rounds are
recorded under `body` in `drive.json`, their prompts are the instrument's and are not counted as a
person's, and they cost what they cost: to `TRUE_DONE` means to a pull request that can be opened,
not to a diff. A body round is told not to touch the tree; one that does has the tree judged again,
and the loop stops on that judgement.

`baseline` is **the human arm**, and the one command that acts as nobody but the person:

```sh
bin/exeris-agent baseline --repo exeris-systems/exeris-docs --task reg:<id> --group <G> \
                          --scope <scope>
cd     ~/.local/state/exeris-agent/runs/<ULID>/wt
#   … the person does the work in that tree, as themselves …
bin/exeris-agent baseline --close --run <ULID>
```

No token is minted, no identity is written and no environment is exported: the commits are the
person's, authored as themselves. What the harness binds is a hooks path carrying two hooks — the
`Exeris-Run:` trailer, so the commits join to this measurement, and one that writes the time of
each commit to `<run>/timing.log`, which is evidence beside the run and enters no record.

`baseline --close` writes `{wall_time_ms, outcome, changes}` — the shape the row contract names for
`human_baseline` — into `<run>/staging/baseline.json` and prints it. The outcome comes from the
same oracle a model arm's does, over the person's own tree, under the same calibration: two arms
judged by two instruments compare the instruments. The task registry is another
repository's, so the group's record is updated by the person: what is printed is what they paste.
A model arm of the same group opened on this machine reads the staged measurement directly, and one
opened elsewhere is given the group's record with `--group-file <path>`.

**The human arm runs first.** `open-run --group <G> --baseline human` refuses to open a model arm
while that group has no human baseline, because every row of such a group carries the measurement
identically and a row written before it existed could not. `--no-baseline-required` opens the arm
anyway; it invents nothing, and the run's work is pushed while its row is refused with the reason
that says the baseline could not be read.

`close-run` acts **as the identity**: it checks the pull request body the arm wrote at
`<run>/body/pr-body.md` (the path the run's environment exports as `EXERIS_PR_BODY`), pushes the
run's branch with the token the run minted, opens a draft pull request carrying that body with one
`Owner:` line and the run's id added, and stages a record beside a copy of the session log it
references. The body is composed and passed through the organisation's template check — as the
execution identity's pull request, with the ADR rule on where the commits touch an ADR, whose
`adr` label is then applied — before anything is pushed: a run with no body, or with one the check
refuses, pushes nothing and opens nothing, and `--no-pr` pushes without asking for one. The gate
exempts a draft, which is why the check runs here rather than waiting for the ready click. A draft,
because a human marking it ready is the moment a person takes on what the run produced. It closes the run either way: where the record cannot be
assembled — two models on the main chain, a client version that moved under the session, a checkout
with no bundle pin, a client version (or, on a local arm, a set of weights) no fence is registered
for, a group whose human arm was never measured — the push and the pull request stand and the
reason is printed.

`flush` acts **as the person**: their own `gh`, their own git. It carries the session streams into
the streams repository, resolves each staged row's reference to the commit that now holds its
stream, runs the inbox's own validator over the batch, and opens one pull request against the
inbox. A red validator commits nothing and asks for nothing — a defect is fixed at the producer,
not quarantined in the dataset. The human who opens that pull request is signing off on the batch;
they are not lending a credential to the runs it describes, and a shell still bound to a run is
refused rather than used. Rows whose visibility is not the one the target inbox declares in its own
`inbox.json` stay in staging and are counted: an enterprise-private row's inbox is the enterprise
sibling, which does not exist yet.

## Configuration

`~/.config/exeris-agent/config.toml` (the directory 0700) holds identifiers and one path — no
secret:

```toml
[github]
client_id       = "…"     # the App's client id, which the assertion issues under
installation_id = 0       # what tokens are minted against
bot_user_id     = 0       # the App's BOT USER id, not its App id: the noreply address is built
                          # from it, and the App id in that position names nobody
private_key     = "…"     # a PEM at mode 0600, or a .age path where `age` is installed
age_identity    = "…"     # the identity a .age key is decrypted against, itself at mode 0600
bot_login       = "exeris-agent[bot]"
owner_login     = "…"     # the organisation member accountable for what a run produces;
                          # without one, `open-run` refuses — a pull request the run's work
                          # becomes needs exactly one `Owner:` line, and this is where it
                          # comes from
org             = "exeris-systems"
execution_repo  = "…"     # the local clone rows are carried into, and the clone the oracle is
                          # imported from — the harness keeps no copy of it
streams_repo    = "…"     # the local clone session streams are carried into; a flush commits
                          # and pushes from both, under the person's own identity

[oracle]
docs_index      = "…"      # the central ADR registry a documentation checkout is judged against,
                           # in the clone that publishes it. A checkout that is itself the
                           # registry is checked against itself and never needs this; without it
                           # anywhere else that gate does not run, because the alternative is a
                           # gate that fetches over the network, and a gate that reports the
                           # network is not a gate
bridge          = "…"      # the Exeris MCP server's `dist/server.js`, absolute: passed to the
                           # oracle, pinned (package version, and commit where its package
                           # directory is a git checkout) in every run's manifest, and the one
                           # server an arm with `mcp = true` is given

[pull_request]
body_check      = "…"      # the organisation's `scripts/pr_body_check.py`, in a clone of
                           # exeris-systems/.github. Every body is checked with it before the
                           # identity sends it; named rather than copied, so the harness never
                           # holds a second answer to what the template requires. Without it no
                           # pull request is opened

[registry]
path            = "…"      # the local clone of the private task registry. A `reg:T-NNN` run reads
                           # `registry/tasks/T-NNN.json` there when it opens and records the task's
                           # `oracle_inputs.preserve`; a task file that cannot be read refuses the
                           # run. Without it no task tells the oracle anything

[repos.exeris-docs]         # the bare name; `[repos."exeris-systems/exeris-docs"]` names the
                            # same repository, so a vocabulary configured either way is enforced
                            # either way
path            = "…"      # the clone, when it is not beside this checkout
domain          = "…"      # which oracle judges work done here, spelled as the contract spells
                           # it: `docs-sweep` or `construction`
scope           = ["…"]    # the vocabulary --scope is checked against
routine         = "…"      # the routine a run here follows, hashed into the record with the
                           # prompt and the agent file; omitted where there is none
readable        = ["…"]    # directories outside the worktree every `drive` pass may read, passed
                           # as `--add-dir`; a property of the repository, so every arm of a group
                           # reads the same ones, and a path that is not a directory is refused
mcp             = false    # whether an arm working here is given the server `[oracle] bridge`
                           # pins as context tools; a property of the repository for the reason
                           # `readable` is
```

A run against a repository the configuration says nothing about opens and closes, and yields no
row: the domain and the scope vocabulary are what the record's own fields are filled from, and a
producer that guessed at one would be writing a column nobody could read.

### How a run is judged

`harness/oracle.py` is the seam every outcome this harness writes comes through — a run record's
and a human baseline's alike, because they are the same question asked about the same work.

A `docs-sweep` run is judged by the oracle `execution_repo` publishes, **imported from that clone
and never copied here**: a copy is a second implementation of the gates, free to drift from the one
the calibration suite was run against, and a row's `oracle.version` would then name rules that
never judged it. The tree it is asked about is the run's own worktree at the head the run left,
read before anything is pushed, so that what was judged is what the run produced.

Whether that judgement is admissible is not this harness's to decide. It is read, at the moment the
row is written, from the file the oracle's own mutation suite publishes — `status` and `result` as
they stood. While that suite has not passed, the row is `UNKNOWN` whatever the gates found
(ADR-086 §E.19), and the calibration beside it says which pass it is waiting for. Nothing is
remembered between runs: a row is interpretable only against the calibration in force when it was
written.

The gates themselves stay with the run, as `staging/judgement.json`. They are not columns on the
row — a per-gate column is a column about one oracle's internals, not comparable across oracles and
renamed whenever a gate is — and the file carries both what the oracle concluded and what the row
was allowed to say, because that pair is what explains a run whose gates all passed and whose row
reads `UNKNOWN`.

**Two generations.** The execution repository may publish the second generation of the
documentation oracle beside the first, calibrated by the suite `docs-mutation-v2` in
`oracle-selftest-v2.json`. Where that file is present it is the calibration read, and its status is
the one the outcome rule applies; `oracle-selftest.json` is the fallback only while it is absent.
Under the second generation the oracle is asked three things more: `base`, the commit the run
started from; `preserve`, the task's patterns for files whose bodies must be unchanged against
`base`; and `bridge`, the pinned MCP server. They are passed only where the second calibration is in
force and the oracle's `judge` names them — under the first one's they are dropped, because that
suite measured an oracle never given them. An `adhoc:` run has no task record and asks for no
`preserve`. The suite that calibrated a judgement is on the row's `oracle.calibration.suite`, and a
row judged by the second generation sits on a `-v2` fence.

A `construction` run is still `UNKNOWN` at `not-run`. That suite has not been run as a suite, and
an oracle whose pass has never been contradicted by a known-broken input is unvalidated.

### The arms

An arm is a model behind a client, under a ledger, launched by an adapter. Those four travel
together — a model id without the client that ran it is not a model reference, and a client without
the ledger it was billed under is not an accounting row — so they are declared together:

```toml
[providers.<name>]          # `--provider <name>`; a name matching a built-in completes it
provider        = "…"      # the row's `agent.provider`: the vendor whose model took the turns
model_id        = "…"      # what the adapter launches with; the row carries what it observed
credential      = "…"      # `api`, `subscription` or `local` — the row's `accounting.mode`
adapter         = "…"      # `claude`, `antigravity`, `codex`, `gemini`
weights         = "…"      # a local weights file; its sha256 becomes `model_snapshot`
harness_client  = "…"      # what the adapter cannot read of `agent.harness`, declared
harness_version = "…"

[providers.<name>.env]      # what the adapter exports for the run — never a secret inline
SOME_VARIABLE   = "…"      # a value, or `file:<path>` for one that has to be read from a file
```

**The table is configuration and nothing else.** It says which arms exist on this machine; nothing
here says which arm suits which workload, and nothing in the harness reads one. What the arms are
for is what a comparison of the rows may one day say, not what the producer says while it writes
them.

Three rules the tables are checked against when a run opens, rather than when it is recorded:

- an arm's launch environment may not set any of the names the run's own boundary is made of, so a
  table cannot hand a run a credential the harness did not mint or displace the identity bound to
  the tree;
- `weights` and `credential = "local"` are one fact stated twice — the row contract admits a
  digest as a snapshot only under `local`, and a run whose weights file is absent refuses to open
  rather than recording a snapshot of something nobody can show it loaded;
- an adapter that takes its task on the command line requires `--prompt-file`, because its stream
  carries no prompt text afterwards and the digest of what instructed the run has no other source.

The manifest records the arm as it was resolved, including the weights digest, and records the
*names* of the launch variables without their values: a token read out of a file to be exported is
a credential, and the record of a run is not where a credential is kept.

### The MCP server for the arms

`[repos.<name>] mcp = true` gives an arm working there the server `[oracle] bridge` pins — the
same program the oracle reads the registry through, so the arm's context and the instrument's
judgement come from one build. The manifest records `mcp: {server, bridge: {version, commit},
tools}` for an arm given it and `mcp: null` for one that is not, and a row whose arm had it sits on
a `-mcp` fence. How the server reaches the arm is the client's:

- **Claude Code** takes it per invocation. `open-run` writes `<run>/mcp.json`, one stdio server
  named `exeris` running `node <bridge>` with `EXERIS_DOCS_ROOT` set to the first `readable`
  directory holding `adr-index.md` (none is a refusal) and `EXERIS_BRIDGE_MODE=contributor`. Every
  `drive` pass is given `--mcp-config <run>/mcp.json --strict-mcp-config`, so no server of the
  person's own configuration is loaded, and the server's read-only documentation tools are added
  to the allowed tools as `mcp__exeris__docs-list_adrs`, `mcp__exeris__docs-get_adr` and
  `mcp__exeris__docs-search` — the name Claude Code gives an MCP tool, `mcp__<server>__<tool>`,
  keeping `-` and `_`. The configuration is given to `drive`'s passes; a person working in a run
  opened with `--launch` works under their own client configuration.
- **Antigravity** has no per-invocation flag: it reads its own user-level configuration. So the
  run is checked rather than configured. With `mcp = true`, `open-run` requires `agy mcp list` to
  show exactly one enabled stdio server whose command is `node <pinned bridge>`, records that line,
  and otherwise refuses, naming the `agy mcp add` that points it there; with `mcp = false` it
  refuses where any enabled server is the Exeris bridge, because the arms of a group read through
  the same context tools. `drive` checks the listing again before its first pass. The client exposes
  every tool the server lists, which the manifest records as `tools: ["*"]`.

### The three arms this machine runs

| Arm | `[providers.…]` | What the row says |
| :-- | :-- | :-- |
| a vendor CLI | `claude` (or `codex`, `gemini`) with a `credential` | the vendor, the model the session log names, `unresolved:<model_id>` |
| Antigravity | `adapter = "antigravity"`, a `model_id`, a `credential` | the vendor, the model the stream names, `unresolved:<model_id>` |
| a local model | `adapter = "claude"`, `credential = "local"`, `weights`, and an `env` pointing the client at a local endpoint | `provider = "local"`, `accounting.mode: local`, `model_snapshot: sha256:<weights>` |

**The local arm runs no new adapter.** The client is the same one the vendor arm uses, pointed at a
local endpoint by the arm's launch environment — `ANTHROPIC_BASE_URL` at a proxy that speaks the
client's protocol in front of a `llama-server` serving the weights, and `ANTHROPIC_AUTH_TOKEN` at
whatever that proxy requires, read from a file. Both processes are the person's to start and to
stop; the harness does not manage them, does not check that they are listening, and says nothing
about which weights are worth serving. What it does is digest the file, so that the row names what
ran:

```toml
[providers.local-claude]
provider   = "local"
credential = "local"
adapter    = "claude"
model_id   = "<as the weights' publisher writes it>"
weights    = "~/models/<file>.gguf"

[providers.local-claude.env]
ANTHROPIC_BASE_URL   = "http://127.0.0.1:<port>"
ANTHROPIC_AUTH_TOKEN = "file:~/.config/exeris-agent/local-proxy-token"
```

### What the Antigravity stream is read as

This client writes its events to standard output, so the file the launcher redirects them into is
both the session log and the stream the row references. Every count below comes from that file, and
everything it does not carry is absent from the row rather than zero:

| Row field | From |
| :-- | :-- |
| `agent.model_id` | the `init` event's model |
| `agent.harness` | `antigravity`, at the version the launcher recorded when the run started |
| `execution.turns` | the closing event's own turn count |
| `execution.tool_calls` | steps, collapsed by index, whose type is none of `user_input`, `agent_response`, `system_message` |
| `execution.human_prompts` | `user_input` steps less the one that started the run and less one per oracle round a `drive` sent |
| `accounting.usage` | the last closing event's input, output and cache-read counts — a resumed conversation's closing event carries the conversation's totals |
| `execution.wall_time_ms` | the closing event's duration where there is one closing event; with more, the run's own clock |
| `execution.permission_denials` | steps whose state names a refusal — **absent** where no state in the stream names one |
| `execution.capture_level` | `full` where a refusal was observed, `counts-only` otherwise |
| `agent.system_prompt_sha256` | the `--prompt-file` the harness passed, composed as for every other arm |

The tool step types are unmeasured: the client names them as it likes, so they are counted rather
than enumerated, and the distinct types and states a run used are printed when it closes. The one
name the two counts rest on is `user_input` — a tool step is a step that is not conversation, and
steering is the prompts after the first — so a stream that never spells it is refused rather than
counted, and the types it did use are printed beside the refusal. That is how a client whose
vocabulary differs is discovered: by a run that leaves no row and says what it saw, never by a
column of tool calls with the prompts folded into it. The
client's thinking-token count and its total are deliberately not carried — neither is one of the
four counts the contract names, and the second is a sum a reader can take.

## What is deliberately not here

V0 is the identity and the run, and nothing that would have to be re-measured once either changes.
Absent on purpose:

- **`serve`** — the JSON-RPC contract the CLI will be the first client of, and its surface golden.
  The methods are designed; freezing them before a second client exists would freeze a guess.
- **the provenance record** — who acted on GitHub. It joins to a run record by run id, and there
  are no run records yet.
- **an oracle of this repository's own** — the one that judges a documentation run is imported from
  the execution repository, which is where it is published and calibrated. A copy here would be a
  second implementation of the gates, free to drift from the one the suite was run against; the
  construction oracle is absent for the older reason, that its suite has not been run as a suite.
- **hook changes** — the run id is exported and nothing reads it. The L0 dispatcher is the layer
  the checkout already renders; the harness does not reach into it.
- **review-policy enforcement** — the policy file is a record of what independent approval means;
  enforcing it from here would put the rule in two places.
- **containers** — the isolation V0 ships is environment plus tripwire, stated as such in
  `policy/README.md`, with the measurement that decides whether something stronger is owed.
- **stream redaction** — a session log is carried whole into the streams repository and nothing in
  it is filtered. What such a log may contain is an open question in the records this layer rests
  on; until it is answered, the stream is treated as content throughout — it never enters an inbox,
  and the row carries its location, its digest and its event count rather than any of it.
- **`tool_surface`** — the digest of what a run was permitted to do. Two rows are comparable only
  where the runner's powers are recorded, so the field is owed; until the harness reads those
  permissions it is left absent rather than filled, because an absent digest means the powers are
  unrecorded and a digest over something else would mean nothing at all.
- **price lists** — a run record carries what was spent in tokens and turns, never a currency
  figure derived from a table that changes under it.
- **`scope_denials`** — a count of what an agent was stopped from doing. The counter is only
  meaningful once there is a boundary that denies rather than one that records.

Apache-2.0. Standard library only, Python 3.14 — a harness that needed a package index to bind an
identity would be one more thing to trust.
