#!/usr/bin/env python3
"""An `agy` that answers `mcp list` with the servers a case wrote beside it.

Copied onto a test's `PATH` as `agy`. The listing is the client's own shape — a heading line, then
one server per line in blank-separated columns — and the servers are read from `agy-mcp-list`
beside this file, so a case states the user-level configuration it is about and nothing else. A
listing nobody wrote is a client with no servers configured.
"""

import os
import sys

LISTING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agy-mcp-list")
HEADING = "NAME              TYPE   STATUS   COMMAND/URL\n"


def main(argv) -> int:
    if argv[:2] != ["mcp", "list"]:
        sys.stderr.write("this stand-in answers `agy mcp list` and nothing else\n")
        return 2
    try:
        with open(LISTING, encoding="utf-8") as handle:
            servers = handle.read()
    except OSError:
        servers = ""
    sys.stdout.write(HEADING + servers)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
