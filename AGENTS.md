---
title: "exeris-agent-harness: guardrails for an agent working in this repository"
type: reference
visibility: public
owning-repo: exeris-agent-harness
status: active
last-verified: 2026-09-22
---

# exeris-agent-harness

Guardrails for an agent working inside this repository. The human-facing description of what the
harness does is in [`README.md`](README.md); this file is the entry point an agent reads first.

## Mission and scope

This repository is the harness that gives work an agent carries out end to end its own identity —
a GitHub App bound to a worktree rather than to a shell — so that such work is distinguishable in
the commit graph from a maintainer's own work and from work an agent was steered through line by
line. V0 is four commands (`open-run`, `close-run`, `flush`, `baseline`) plus the modules under
`harness/` and the per-vendor launchers under `adapters/` that back them.

## Operating contract and safety boundaries

- **The identity's ceiling is fixed, not extended from here.** `identity/app-manifest.json` states
  the App's `default_permissions`; widening them, or installing the App on a repository that holds
  run records, is the one thing this repository exists to prevent, and is never done to make a
  feature easier to build.
- **A run never falls back to a person's credential.** Where the identity cannot mint its own
  token, a run refuses to open rather than borrowing the shell's — a change to `harness/token.py`
  or `harness/runstate.py` keeps that failure mode closed, not made silent.
- **The harness's own records never reach the inbox under the identity's token.** `close-run` acts
  as the identity and stops at a draft pull request against this repository's own remote; carrying
  a batch into the records inbox is `flush`, and it acts as the person, over their own `gh` and
  their own git. Collapsing that split is the one change [`policy/README.md`](policy/README.md)
  exists to rule out.
- **A value the harness could not establish is absent, never defaulted.** `harness/record.py` is
  the seam: it adds no field the row contract does not name, and a run whose measurement is
  missing prints the reason rather than filling the row with a guess.
- **English** in source, comments, commit messages, pull-request titles and documents.

## Architecture and documentation entry points

| Path | What it holds |
| :-- | :-- |
| [`harness/`](harness) | The commands and the seams they share: `cli.py`, `token.py`, `worktree.py`, `runner.py`, `record.py`, `oracle.py`, `providers.py`, `capture.py`. |
| [`adapters/<vendor>/`](adapters) | One launcher and one session reader per vendor CLI (`claude`, `codex`, `gemini`, `antigravity`) — never a second place a row's fields are computed. |
| [`bin/exeris-agent`](bin/exeris-agent) | The CLI entry point; holds no logic beyond putting the checkout on the import path. |
| [`policy/README.md`](policy/README.md) | The map to the identity's ceiling and the organisation's rulesets — the boundary is configuration, this page points at it. |
| [`identity/app-manifest.json`](identity/app-manifest.json) | The App's own permissions and hook settings, published so another organisation can instantiate its own identity. |
| [`docs/adr/ADR-087.link.md`](docs/adr/ADR-087.link.md) | How this repository's identity is registered, and where it sits beside the two identities ADR-087 also governs. |
| [`README.md`](README.md) | The commands, the configuration file, the row contract each arm feeds, and what V0 deliberately leaves out. |

## `.agents/` discovery

This repository defines no reusable agent role, skill or workflow of its own, so it carries no
`.agents/` tree. `adapters/` is not that tree: it launches a vendor's own CLI as one arm of a run
and reads that vendor's session log; it composes nothing under `agents-md-schema.md` and is
checked as ordinary source. The policy an agent working here has to honour is
[`policy/README.md`](policy/README.md); the workflow this repository runs its own pull requests
under is `.github/workflows/guardrails.yml`, which calls the organisation's shared review routine
rather than defining one locally. A future change that gives this repository a reusable profile or
skill belongs under `.agents/`, per `agents-md-schema.md`, and not under `adapters/` or a provider
directory.

## Verification and reporting

Before opening a pull request:

```sh
python3 -m unittest discover -s tests -p '*_test.py'
```

Report the count the run actually produced (`Ran N tests`, `OK` or the failures), not the count a
prior run produced — the same discipline this repository's own row contract asks of a harness run.
Tests write into a temporary state root and clean up after themselves; none of them reach GitHub.

## Provider-adapter note

No rendered provider adapter (`CLAUDE.md`, `GEMINI.md`, a `.cursorrules` file) exists in this
repository. An agent working here reads this file directly, in whatever client it runs under.
