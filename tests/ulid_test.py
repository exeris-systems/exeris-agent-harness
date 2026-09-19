"""The run identifier.

A run id is the only name a run has: it labels the state directory, the branch, the
`Exeris-Run:` trailer on every commit the run makes, and the run record written when the run
closes. Three properties have to hold for those uses to work at all — a fixed width so the
branch name can take a prefix of it, an alphabet with no character a reader can transcribe
two ways, and an ordering that follows the order runs were opened so that a directory listing
of the state root is a timeline.
"""

import pathlib
import re
import sys
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Crockford base32: the decimal digits and the uppercase letters, less I, L, O and U — the four
# that a reader confuses with 1, 1, 0 and V, or that spell something when the alphabet is unlucky.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _factory():
    """The module is the contract; the spelling of its factory is not pinned here."""
    import harness.ulid as mod

    for name in ("new", "new_ulid", "ulid", "generate"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise AssertionError(
        "harness.ulid exposes no callable named new/new_ulid/ulid/generate"
    )


class UlidTest(unittest.TestCase):
    def test_is_twenty_six_characters(self):
        new = _factory()
        self.assertEqual(26, len(new()))

    def test_uses_only_the_crockford_alphabet(self):
        new = _factory()
        pattern = re.compile("^[%s]{26}$" % CROCKFORD)
        for _ in range(64):
            value = new()
            self.assertRegex(value, pattern)
            # The excluded four are the point of the alphabet, so name them separately: a
            # regex that happened to be built from the wrong constant would still pass above.
            self.assertFalse(set(value) & set("ILOU"), value)

    def test_orders_by_the_moment_the_run_opened(self):
        new = _factory()
        # Lexicographic order is the only order a directory listing, or a sort on the id, gives,
        # so it has to agree with the order the runs opened. Two ids minted back to back land in
        # the same millisecond, where the timestamp half cannot separate them: the second one is
        # still the larger, or the state root stops being a timeline.
        first = new()
        second = new()
        self.assertLess(
            first, second,
            "two ids minted in the same millisecond came out unordered: the random half is drawn "
            "afresh each time instead of being carried forward and incremented",
        )

    def test_no_two_runs_share_an_id(self):
        new = _factory()
        values = [new() for _ in range(256)]
        self.assertEqual(len(set(values)), len(values))


if __name__ == "__main__":
    unittest.main()
