# exeris-agent-harness

The harness that gives work carried out by an agent an identity of its own.

Before it, every commit in these repositories came from one login: the maintainer's own work, the
work an agent was steered through line by line, and the work an agent carried out end to end, all
indistinguishable in the commit graph and therefore indistinguishable in any dataset built from it.
The harness separates them by giving the third kind its own principal — the organisation's
execution identity, a GitHub App — and by binding that identity to a worktree rather than to a
shell, so that the boundary holds when the agent opens a second terminal.

**V0 is three commands: one opens a run, one closes it, one carries what it produced.** A run is:

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
- a **manifest** saying what the run is — task, repository, provider, branch, base commit, the
  accountable owner, and the principal that will act.
- a **record**, written when the run closes: one row in the shape of the layer's row contract,
  saying what the run was, what it did and under what conditions — and never what it produced. A
  value the run could not establish is not defaulted; where one is missing there is no row, and the
  reason is named and counted rather than repaired.

The identity's ceiling is its App permissions and the organisation's rulesets;
[`policy/README.md`](policy/README.md) is the map to both.

## The three commands

```sh
bin/exeris-agent open-run --repo exeris-systems/exeris-docs --task reg:<id> --provider claude \
                          --scope <scope>
source ~/.local/state/exeris-agent/runs/<ULID>/env
cd     ~/.local/state/exeris-agent/runs/<ULID>/wt
#   … the agent works in that tree …
bin/exeris-agent close-run --run <ULID>
bin/exeris-agent flush
```

`open-run` prints the second and third lines; `--launch` runs the provider's adapter in the
worktree instead of printing them. `--task adhoc` opens an unplanned run, which is capturable but
never paired, so `--group` requires a registry entry. `exeris-agent status` lists the runs on the
machine.

`close-run` acts **as the identity**: it pushes the run's branch with the token the run minted,
opens a draft pull request carrying one `Owner:` line and the run's id, and stages a record beside
a copy of the session log it references. A draft, because a human marking it ready is the moment a
person takes on what the run produced. It closes the run either way: where the record cannot be
assembled — two models on the main chain, a client version that moved under the session, a checkout
with no bundle pin, a client version no fence is registered for — the push and the pull request
stand and the reason is printed.

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
execution_repo  = "…"     # the local clone rows are carried into
streams_repo    = "…"     # the local clone session streams are carried into; a flush commits
                          # and pushes from both, under the person's own identity

[repos.exeris-docs]             # the bare name; `[repos."exeris-systems/exeris-docs"]` names the
                                # same repository, so a vocabulary configured either way is
                                # enforced either way
path                = "…"      # the clone, when it is not beside this checkout
domain              = "…"      # which oracle judges work done here, spelled as the contract
                               # spells it: `docs-sweep` or `construction`
scope               = ["…"]    # the vocabulary --scope is checked against
provider_credential = "…"      # `api`, `subscription` or `local` — the ledger a run belongs in
routine             = "…"      # the routine a run here follows, hashed into the record with the
                               # prompt and the agent file; omitted where there is none
```

A run against a repository the configuration says nothing about opens and closes, and yields no
row: the domain, the scope vocabulary and the credential class are what the record's own fields are
filled from, and a producer that guessed at one would be writing a column nobody could read.

## What is deliberately not here

V0 is the identity and the run, and nothing that would have to be re-measured once either changes.
Absent on purpose:

- **`serve`** — the JSON-RPC contract the CLI will be the first client of, and its surface golden.
  The methods are designed; freezing them before a second client exists would freeze a guess.
- **the provenance record** — who acted on GitHub. It joins to a run record by run id, and there
  are no run records yet.
- **`baseline`** — the command that measures a repository before a run.
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
