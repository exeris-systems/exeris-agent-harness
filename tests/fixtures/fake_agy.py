#!/usr/bin/env python3
"""An `agy` that lists the servers its home configures, and records how it was launched.

Copied onto a test's `PATH` as `agy`. `agy mcp list` answers in the client's own shape — a heading
line, then one server per line in blank-separated columns — from the server file under `$HOME`,
the way the client reads it, plus whatever lines a case wrote into `agy-mcp-list` beside this file:
servers the client would find somewhere other than its home, which a case states when that is what
it is about. A home with no server file, and no such lines, is a client with no servers configured.

`agy --version` prints a version. Any other invocation is a pass: its arguments and the `HOME` it
ran under are appended to `agy-calls.jsonl` beside this file, and it writes nothing else.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LISTING = os.path.join(HERE, "agy-mcp-list")
CALLS = os.path.join(HERE, "agy-calls.jsonl")
SERVERS = (".gemini", "config", "mcp_config.json")
HEADING = "NAME              TYPE   STATUS   COMMAND/URL\n"
VERSION = "1.9.9"


def _home_servers() -> str:
    try:
        with open(os.path.join(os.environ.get("HOME", ""), *SERVERS), encoding="utf-8") as handle:
            servers = json.load(handle).get("mcpServers") or {}
    except (OSError, ValueError):
        return ""
    lines = []
    for name, server in servers.items():
        status = "disabled" if server.get("disabled") else "enabled"
        command = " ".join([server.get("command", ""), *server.get("args", [])])
        lines.append(f"{name}  stdio  {status}  {command}\n")
    return "".join(lines)


def main(argv) -> int:
    if argv == ["--version"]:
        sys.stdout.write(f"{VERSION}\n")
        return 0
    if argv[:2] == ["mcp", "list"]:
        try:
            with open(LISTING, encoding="utf-8") as handle:
                elsewhere = handle.read()
        except OSError:
            elsewhere = ""
        sys.stdout.write(HEADING + _home_servers() + elsewhere)
        return 0
    with open(CALLS, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "home": os.environ.get("HOME")}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
