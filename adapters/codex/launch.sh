#!/bin/sh
# Launch-only adapter for Codex CLI.
#
# Identity binds at the worktree and at the environment the harness has already set, which is the
# whole of what this adapter provides. It reads no session log, so a run launched through it can
# only ever be capture_level identity-only — an adapter assumes nothing about a runtime it has not
# actually read.
set -eu

exec codex "$@"
