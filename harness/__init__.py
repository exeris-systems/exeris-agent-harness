"""The Exeris agent harness — one execution contract, N surfaces.

V0 is the first surface: a CLI that opens a run. A run is a worktree, a one-hour installation
token for the organisation's execution identity, a git configuration bound to that tree, and a
`prepare-commit-msg` hook that stamps every commit with the run's id. Everything an agent does
inside that tree is the run's; everything it needs to act on GitHub is the identity's own, never a
person's.

The package is standard library only. It runs on a maintainer's machine before anything is
installed, and a harness that needed a package index to bind an identity would be one more thing
to trust.
"""

import os

#: The checkout this package was imported from. Adapters and identity files are found relative to
#: it, so a clone anywhere on disk is self-contained.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Written onto run records as `instrument.harness`; a change to what the harness measures moves
#: it, because rows either side of such a change do not sum.
VERSION = "0.1.0"
