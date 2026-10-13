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
# The redirection appends. A driven run resumes one conversation in several invocations, and the
# conversation is one session: its stream is every invocation's events in the order they ran, never
# the last invocation's alone. A run's directory starts without the file, so a single pass writes
# exactly what it always did.
#
# `--log-file` is the client's own diagnostic log and is not that stream. It is kept in the client's
# own home, the one place under the run the confined client can write, so that a failure to produce
# a stream has somewhere to be read from, and it is never carried.
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
#
# THE CLIENT RUNS UNDER THE RUN'S OWN HOME. It reads its tool permissions and its MCP servers from
# files under `$HOME/.gemini/`, and the person's own copies hold whatever they allowed by hand. So
# `HOME` is pointed at `<run>/agy-home`, which `open-run` wrote with the run's fixed allow-list and
# its server set and nothing else, and a run directory without one is refused rather than launched
# under the person's. Only the client's own processes see that `HOME`: git and `gh` read the run's
# configuration from the variables the run's environment names, never from a home directory. The
# client signs in through the operating system's keyring, which no home holds.
#
# THE CLIENT RUNS CONFINED. Its model reads through shell commands granted by name, and a command
# granted by name reads any path the person can. `sandbox.py` runs it under bubblewrap with only the
# worktree, the readable directories, the run's git configuration and hooks and the client's own
# home mounted; the run's token, its environment and everything else of the person's home are not
# there to be read. The redirection and the version probe stay outside, where the run's directory
# is. Without bubblewrap the client is not started.
set -eu

run_dir="$(dirname "${GIT_CONFIG_GLOBAL:?the run environment has not been sourced}")"
prompt_file="${EXERIS_PROMPT_FILE:?the task reaches this client on its command line}"
model="${EXERIS_MODEL_ID:?the provider table declares no model_id}"
agy_home="${run_dir}/agy-home"

if [ ! -d "${agy_home}" ]; then
    echo "exeris-agent: ${agy_home} does not exist; this client runs only under the home the" \
         "run opened with, never under the person's own" >&2
    exit 2
fi

if ! command -v bwrap > /dev/null 2>&1; then
    echo "exeris-agent: bwrap is not on PATH; this client's reading commands reach any path they" \
         "are given, and it runs only confined to the run's tree, never without" >&2
    exit 2
fi

HOME="${agy_home}" agy --version > "${run_dir}/agy.version" 2>&1 || true

exec python3 "$(dirname "$0")/sandbox.py" "${run_dir}" -- \
        agy --output-format stream-json \
            --model "${model}" \
            --mode accept-edits \
            --print="$(cat "${prompt_file}")" \
            --log-file "${agy_home}/agy.log" \
            "$@" >> "${run_dir}/agy.jsonl"
