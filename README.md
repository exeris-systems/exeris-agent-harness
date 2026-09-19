# exeris-agent-harness

The harness that gives work carried out by an agent an identity of its own.

Before it, every commit in these repositories came from one login: the maintainer's own work, the
work an agent was steered through line by line, and the work an agent carried out end to end, all
indistinguishable in the commit graph and therefore indistinguishable in any dataset built from it.
The harness separates them by giving the third kind its own principal — the organisation's
execution identity, a GitHub App — and by binding that identity to a worktree rather than to a
shell, so that the boundary holds when the agent opens a second terminal.

**V0 is one command that opens a run.** A run is:

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

The identity's ceiling is its App permissions and the organisation's rulesets;
[`policy/README.md`](policy/README.md) is the map to both.

## The three commands

```sh
bin/exeris-agent open-run --repo exeris-systems/exeris-docs --task reg:<id> --provider claude
source ~/.local/state/exeris-agent/runs/<ULID>/env
cd     ~/.local/state/exeris-agent/runs/<ULID>/wt
```

`open-run` prints the second and third; `--launch` runs the provider's adapter in the worktree
instead of printing them. `--task adhoc` opens an unplanned run, which is capturable but never
paired, so `--group` requires a registry entry. `exeris-agent status` lists the runs on the
machine. `close-run` and `flush` are registered and refuse: they are the next version.

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
execution_repo  = "…/…"
streams_repo    = "…/…"

[repos.exeris-docs]             # the bare name; `[repos."exeris-systems/exeris-docs"]` names the
                                # same repository, so a vocabulary configured either way is
                                # enforced either way
path                = "…"      # the clone, when it is not beside this checkout
domain              = "…"
scope               = ["…"]    # the vocabulary --scope is checked against
provider_credential = "…"
```

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
- **stream redaction** — what a captured session may contain is an open question, and capture that
  outran the answer would be the wrong thing to have to undo.
- **price lists** — a run record carries what was spent in tokens and turns, never a currency
  figure derived from a table that changes under it.
- **`scope_denials`** — a count of what an agent was stopped from doing. The counter is only
  meaningful once there is a boundary that denies rather than one that records.

Apache-2.0. Standard library only, Python 3.14 — a harness that needed a package index to bind an
identity would be one more thing to trust.
