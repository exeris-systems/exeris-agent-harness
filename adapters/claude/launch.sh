#!/bin/sh
# Launch Claude Code inside the run's worktree.
#
# The harness has already bound the identity: git in this tree commits as the execution identity,
# `gh` reads the run's own configuration directory, and the hook stamps every commit with the run
# id. What the adapter adds is policy rather than credential, and it is written into the run's own
# settings file so that the run behaves the same on a machine whose user-level settings differ.
#
# `includeCoAuthoredBy: false` — an agent-executed commit is authored by the execution identity and
# names its model on the run record; a `Co-authored-by:` trailer on that commit would assert the
# other authorship form as well, and the two forms are exclusive.
#
# `attribution.sessionUrl: false` — the run's commit message carries the run id, which is what
# joins a commit to the record that observed it; the vendor's session identifier is adapter data
# and is not written into published history, which a commit message is, permanently and once per
# commit. The vendor's own configuration directory is untouched, so this run's setting is this
# run's.
set -eu

run_dir="$(dirname "${GIT_CONFIG_GLOBAL:?the run environment has not been sourced}")"
settings="${run_dir}/claude-settings.json"

cat > "${settings}" <<'JSON'
{
  "includeCoAuthoredBy": false,
  "attribution": {
    "sessionUrl": false
  }
}
JSON

exec claude --settings "${settings}" "$@"
