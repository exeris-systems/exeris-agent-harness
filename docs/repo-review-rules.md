---
title: "Review rules for exeris-agent-harness"
type: reference
visibility: public
owning-repo: exeris-agent-harness
status: active
last-verified: 2026-09-24
---

# Review rules for `exeris-agent-harness`

The `repo-routine` extension of `exeris-systems/.github`'s `docs-guardrails-review.md`, applied
**after** its steps and under its severity tags, output format and verdict schema. It adds checks and
raises severities; it lowers nothing and skips nothing. One review, one verdict, one publisher — the
extension exists so that having rules of one's own is not a reason to keep a review of one's own.

## What this repository is answerable for

The execution identity and the run. Three things live here and nowhere else: the token a run acts
under and the boundary around it, the record a run leaves and where it is derived from, and the
way a run's rows and streams reach the repositories that hold them. A reviewer applying only the
shared routine can judge this repository's prose and its pull request bodies, and can say nothing
about any of the three.

## Step H — rules of this repository

H1. **No human credential is a fallback.** A run acts as `exeris-agent` or it does not open
    (ADR-085 §I.30(b), ADR-087 §A.1). A change that lets a run proceed on a person's token, reads
    the invoking shell's `gh` configuration or git credentials into a run, or degrades to the
    user's identity when the App token cannot be minted → `[HARD BLOCK]`. `baseline` is the one
    command that acts as the person, and it says so in its own name.

H2. **A secret reaches a process through a descriptor or a 0600 file, never argv, never a
    record.** The private key, the installation token and a provider's launch token are read where
    they are used and appear in no command line, no `manifest.json`, no row, no stream, no log. A
    diff that puts one there → `[HARD BLOCK]`. A change to `harness/token.py` or to how
    `[providers.*.env]` values are resolved arrives with a test asserting the negative — the bytes
    are not in argv, not under the run directory except at the mode the run's own token has →
    else `[CONTRACT]`.

H3. **A row is derived from the runtime's own record and never self-reported.** Every counter
    comes from the session log or the stream the runtime wrote; a field the record does not carry
    is absent with `capture_level` saying so, never `0`, never a default (ADR-087 §C.14). A diff
    that fills a field the source cannot yield → `[HARD BLOCK]`. A row that names a publisher
    (`exeris-bot`, `exeris-inbox`, `exeris-agent`) as a model, provider or harness → `[HARD BLOCK]`.

H4. **The hands do not hold the pen.** Nothing here writes to an inbox or to the streams repository
    under the App token: rows and streams are staged under the run and carried in by `flush`,
    under the person's credential, as that person's sign-off (ADR-087 §A.1). A code path that
    reaches either repository with the run's token → `[HARD BLOCK]`.

H5. **A list of arms is configuration, never a recommendation** (ADR-086 Engineering Protocol 8,
    binding until V3). A provider table, an adapter, a README line or a pull request body that says
    or implies one model suits a kind of task → `[HARD BLOCK]`. The harness runs what it is told
    to run and records what happened.

H6. **The oracle is imported, never copied, and its outcome is written only under a calibration
    that passed.** The docs oracle is read from the execution repository's clone, where it is
    published and calibrated; a second implementation of a gate here → `[HARD BLOCK]`. A change that
    writes `TRUE_DONE` or `FALSE_DONE` to a row without reading the calibration in force at
    `status: pass` — `oracle-selftest-v2.json` where the execution repository publishes it,
    `oracle-selftest.json` only where it does not — or that reads the outcome from anything but the
    oracle → `[HARD BLOCK]`.

H7. **A rule arrives with a case that can fail, and the body's count matches the suite.** A change
    to `harness/` or `adapters/` that adds or alters a refusal, a derivation or a boundary without
    a case in `tests/` → `[HARD BLOCK]`. `REPOSITORY CHECK OUTPUT` carries what
    `python3 -m unittest discover -s tests -p '*_test.py'` reported; a *Verification* section whose
    count disagrees with it → `[STYLE]`; one claiming tests that do not exist → `[HARD BLOCK]`.

## Where this does not apply, and what it costs

Not to the shared routine's own steps: PR body, records, commits and hygiene are judged by
`docs-guardrails-review.md` and are not restated here — a rule in two places drifts in one of them.
Not to the App's permissions or the organisation's rulesets, which are configuration in
`identity/app-manifest.json` and `exeris-systems/.github` and are judged where they live.

The cost is that these rules are prose a reviewer applies, not a program: H1, H3 and H5 are
judgement, and a reviewer that reads them loosely enforces them loosely. H2, H4, H6 and H7 are
mechanical and belong in the suite or in CI the moment either can express them; the suite already
holds cases for most of H2, H4 and H6, and this file names what the suite does not yet hold.
