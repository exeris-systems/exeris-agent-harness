#!/usr/bin/env python3
"""A `bwrap` that records how it was asked to confine a program, and runs nothing.

Copied onto a test's `PATH` as `bwrap`. Each invocation's arguments are appended to
`bwrap-calls.jsonl` beside this file, and one placeholder line is written to standard output, so a
case can see where the launcher sent the confined program's stream.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CALLS = os.path.join(HERE, "bwrap-calls.jsonl")
LINE = '{"event": "placeholder"}\n'


def main(argv) -> int:
    with open(CALLS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv}) + "\n")
    sys.stdout.write(LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
