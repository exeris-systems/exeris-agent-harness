"""ULIDs — the run identifier, and the only identifier a run has.

A run id is written into a commit trailer, a branch name, a state directory and a record, so it has
to sort by time (a maintainer reading a directory listing wants the newest last), survive being
typed, and collide with nothing. ULID gives all three: 48 bits of millisecond timestamp followed by
80 bits of randomness, in Crockford's base32, which drops the four characters that look like each
other.

Standard library only, so the encoding is written out rather than imported.
"""

import os
import threading
import time

#: Crockford's base32: I, L, O and U are absent, so no pair of characters is confusable by eye.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

LENGTH = 26
_TIME_BITS = 48
_RANDOM_BITS = 80
_RANDOM_BYTES = _RANDOM_BITS // 8

#: The last id's two halves. Lexicographic order is the only order a directory listing or a sort
#: on the id gives, so it has to agree with the order runs opened — and inside one millisecond the
#: timestamp half cannot separate two ids. Carrying the random half forward and incrementing it is
#: what keeps the state root a timeline; drawing it afresh would order half of such pairs wrongly.
_last: tuple[int, int] = (0, 0)
_lock = threading.Lock()


def new(now=None, entropy=None) -> str:
    """A fresh ULID, never smaller than the one before it.

    `now` and `entropy` exist so a test can pin both halves; a pinned call is pinned for the first
    id of its millisecond, after which the monotonic carry above governs.
    """
    global _last
    with _lock:
        milliseconds = int((now or time.time)() * 1000)
        previous_milliseconds, previous_random = _last
        if milliseconds > previous_milliseconds:
            randomness = int.from_bytes((entropy or os.urandom)(_RANDOM_BYTES), "big")
        else:
            # The same millisecond, or a clock that stepped backwards: either way the previous id
            # is the floor, because an id that sorted below its predecessor would be a run that
            # appeared to open before one that opened first.
            milliseconds = previous_milliseconds
            randomness = previous_random + 1
            if randomness >> _RANDOM_BITS:
                # 2^80 ids in one millisecond is not reachable, but an overflow that silently wrote
                # into the timestamp would be, so it spends a millisecond instead.
                milliseconds += 1
                randomness = int.from_bytes((entropy or os.urandom)(_RANDOM_BYTES), "big")
        if not 0 <= milliseconds < (1 << _TIME_BITS):
            raise ValueError("the timestamp does not fit a ULID's 48 bits")
        _last = (milliseconds, randomness)

    value = (milliseconds << _RANDOM_BITS) | randomness
    # 26 characters carry 130 bits and a ULID is 128, so the first character's top two bits are
    # always zero; the loop walks the value from its most significant group down.
    return "".join(ALPHABET[(value >> shift) & 0x1F]
                   for shift in range(5 * (LENGTH - 1), -1, -5))


def is_ulid(text: str) -> bool:
    """Whether `text` is a well-formed ULID — the check a run id gets before it names a path."""
    return len(text) == LENGTH and all(c in ALPHABET for c in text)


def short(run_id: str) -> str:
    """The branch-name form: the low eight characters, lowercased.

    The low end is the random half, so two runs opened in the same millisecond still differ there,
    and lowercase because that is how every other branch in these repositories is spelled.
    """
    return run_id[-8:].lower()
