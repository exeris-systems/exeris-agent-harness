"""Identity only: this runtime's session log has not been read by an adapter.

The launcher binds identity at the worktree and at the environment, and that is the whole of what
this provider's runs claim. A session directory exists, but nothing here has read it, and an
adapter that assumed its shape would produce counts whose meaning nobody has checked — which is
worse than no counts, because a number in a column is indistinguishable from a measurement.

So `locate` refuses with the reason that says exactly that, and the refusal is counted. The day
this file reads that directory for real is the day the reason stops being written, and it is a
change to the instrument: it writes a fence, because rows with counts and rows without are not one
population.
"""

REASON = "adapter-identity-only"


def locate(manifest: dict, *, projects_root: str | None = None, override: str | None = None):
    """`(None, 'adapter-identity-only')` — there is nothing this adapter has read."""
    return None, REASON


def read(path: str):
    """The same answer in the same words; nothing reaches this except by mistake."""
    return None, REASON
