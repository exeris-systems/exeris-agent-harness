# What the execution identity cannot do

The boundary around `exeris-agent` is expressed as configuration, not as prose, so this page is a
map to the configuration rather than a copy of it. Where a sentence here disagreed with one of the
files below, the file would be right.

## The two files that are the boundary

- **[`../identity/app-manifest.json`](../identity/app-manifest.json)** — the App's permissions, and
  therefore the ceiling on everything a run's token can reach. What is *absent* from
  `default_permissions` is the substance: nothing there grants workflows, administration, members,
  secrets, checks or issues, and a token cannot be scoped up after it is minted. A push touching
  `.github/workflows/**` is refused by GitHub itself — the identity cannot change the gate it is
  judged by. The manifest is public so that another organisation can instantiate its own execution
  identity rather than trust this one; App names are global on GitHub, so each organisation's
  instance is its own.
- **`rulesets/` in [`exeris-systems/.github`](https://github.com/exeris-systems/.github)** — the
  branch rules the identity operates under, shared across the organisation per ADR-085 §C.11 rather
  than restated per repository. The App permission says what the credential can ask for; the
  ruleset says what the repository will accept. Both have to be read to know the answer.

Where the App is installed is the third part, and it is an installation choice rather than a file:
never on the repositories that hold run records, because the hands do not hold the pen.

That is why the harness's own records do not reach the inbox under this identity. A run closes as
the identity — its branch is pushed and its draft pull request opened with the token the run minted
— and the records it staged are carried in afterwards by a person, under their own credential. The
pull request that carries a batch is that person's sign-off on it, and the validator has already
run before it is opened. A harness that reached the inbox with the identity's own token would be
the hands holding the pen, which is the one arrangement this split exists to prevent.

## Key custody, and what V0's relaxation costs

The private key should be encrypted at rest — the harness reads a `.age` path through a pipe when
`age` is present — but it also accepts a plain PEM at mode 0600 owned by the invoking user, and a
plain 0600 key is readable by an agent running as that same user, which makes the L0 deny list on
credential paths a tripwire rather than a wall; encrypt the key on any machine where `age` is
installed.

The encrypted branch decrypts against an identity file named by `age_identity` in the
configuration, held to the same owner-only rule as a plain key. There is no passphrase branch: a
passphrase is read from a terminal, and a run opened without one — from a dispatcher, or with
`--launch` inside another process — would block on a question nobody is there to answer.

## What a run cannot resolve

A run's environment drops the credentials of the shell it was opened from, so a build inside it
that resolves the organisation's own snapshots from GitHub Packages fails, and fails closed: the
App requests no `packages` permission, and the alternative — carrying the maintainer's registry
token in — puts a person's credential inside a run, which is the thing the execution identity
exists to make unnecessary. A repository whose build needs those snapshots is waiting on a
credential of the identity's own, not on an exception here.

## Measurement this policy is waiting on

The isolation above is convention plus tripwire, and it was chosen with a measurement attached that
decides whether it stays: **L0 denials on credential paths, over the first runs — measured: —.**
Zero after a reasonable number of runs and the stronger isolation waits; more than zero and it is
owed, because the reach has then been observed rather than assumed.
