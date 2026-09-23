#!/bin/sh
# Launch Antigravity's CLI inside the run's worktree.
#
# The harness has already bound the identity: git in this tree commits as the execution identity,
# `gh` reads the run's own configuration directory, and the hook stamps every commit with the run
# id. What this adapter adds is the one thing this client needs that the others do not — the task,
# on the command line.
#
# THE STREAM IS THE SESSION LOG. This client writes its events to standard output and nowhere else,
# so the redirection below is what makes a session readable afterwards: the file it writes is what
# the adapter counts and what is carried into the streams repository. A run launched without it
# leaves no record of itself beyond its commits.
#
# `--log-file` is the client's own diagnostic log and is not that stream. It is kept beside the run
# so that a failure to produce a stream has somewhere to be read from, and it is never carried.
#
# The version is captured here rather than at close, because the version that ran is the version
# that was installed when it ran, and a client upgraded between the run and its record would
# otherwise be reported as the one that did the work.
#
# Both streams are captured, because where a CLI prints its version is not a property this adapter
# gets to assume. Discarding standard error leaves an empty file under a client that prints there,
# and the adapter then falls back to asking the installed program — which succeeds, silently, and
# records the wrong version. The reader takes a version out of whatever else the line says and
# refuses a line too long to be one, so a diagnostic banner costs nothing.
set -eu

run_dir="$(dirname "${GIT_CONFIG_GLOBAL:?the run environment has not been sourced}")"
prompt_file="${EXERIS_PROMPT_FILE:?the task reaches this client on its command line}"
model="${EXERIS_MODEL_ID:?the provider table declares no model_id}"

agy --version > "${run_dir}/agy.version" 2>&1 || true

exec agy --output-format stream-json \
         --model "${model}" \
         --mode accept-edits \
         --print="$(cat "${prompt_file}")" \
         --log-file "${run_dir}/agy.log" \
         "$@" > "${run_dir}/agy.jsonl"
