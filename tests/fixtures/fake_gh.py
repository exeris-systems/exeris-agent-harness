#!/usr/bin/env python3
"""A `gh` that answers the REST calls the harness makes, and records every one of them.

Copied onto a test's `PATH` as `gh`. It answers the three endpoints the harness reaches for — the
repository, for the visibility recorded at capture time; a pull-request creation; and the open
pull requests of a head branch, which is what makes a second flush open nothing — and it records
every invocation so a case can assert what was sent rather than what was meant.

Its state lives beside the script rather than behind an environment variable, because the harness
builds the environment a run's subprocesses see and a variable this file needed would be one the
harness has to be asked not to drop.

Standard input is read only for `--input -`, the one form that carries a body that way: a `gh`
that read an inherited pipe on every call would block on the one nobody wrote to.
"""

import json
import os
import re
import sys
import urllib.parse

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh-state")

#: Options that take a value, so the endpoint is not mistaken for one of them.
WITH_VALUE = ("-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header",
              "--input", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p",
              "--preview")


def _read(name, default):
    try:
        with open(os.path.join(STATE, name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _write(name, value):
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, name), "w", encoding="utf-8") as handle:
        json.dump(value, handle)


def _visibility():
    try:
        with open(os.path.join(STATE, "visibility"), encoding="utf-8") as handle:
            return handle.read().strip() or "public"
    except OSError:
        return "public"


def _parse(argv):
    """`(endpoint, method, fields)` out of a `gh api` invocation."""
    endpoint, method, fields = None, None, {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("-X", "--method"):
            method = argv[index + 1] if index + 1 < len(argv) else None
            index += 2
            continue
        if token in ("-f", "--raw-field", "-F", "--field"):
            pair = argv[index + 1] if index + 1 < len(argv) else ""
            key, _, value = pair.partition("=")
            fields[key] = value
            index += 2
            continue
        if token.startswith("-X"):
            method = token[2:]
            index += 1
            continue
        if token in WITH_VALUE:
            index += 2
            continue
        if not token.startswith("-") and token not in ("api",) and endpoint is None:
            endpoint = token
        index += 1
    return endpoint, method, fields


def main(argv):
    body = None
    if "--input" in argv and argv[argv.index("--input") + 1:argv.index("--input") + 2] == ["-"]:
        body = sys.stdin.read()

    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "calls.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": argv, "stdin": body}) + "\n")

    endpoint, method, fields = _parse(argv)
    if body:
        try:
            sent = json.loads(body)
        except json.JSONDecodeError:
            sent = None
        if isinstance(sent, dict):
            fields.update(sent)
    if method is None:
        method = "POST" if fields else "GET"
    if not endpoint:
        print("{}")
        return 0

    path, _, query = endpoint.partition("?")
    pulls = re.fullmatch(r"repos/([^/]+)/([^/]+)/pulls", path)
    repo = re.fullmatch(r"repos/([^/]+)/([^/]+)", path)

    if pulls and method.upper() == "POST":
        open_pulls = _read("pulls.json", [])
        number = 1000 + len(open_pulls) + 1
        created = {"number": number, "state": "open", "draft": bool(fields.get("draft")),
                   "head": {"ref": fields.get("head", "")},
                   "html_url": f"https://github.com/{pulls.group(1)}/{pulls.group(2)}"
                               f"/pull/{number}"}
        open_pulls.append(created)
        _write("pulls.json", open_pulls)
        print(json.dumps(created))
        return 0

    if pulls:
        open_pulls = _read("pulls.json", [])
        wanted = urllib.parse.parse_qs(query).get("head", [None])[0]
        if wanted:
            branch = wanted.split(":", 1)[-1]
            open_pulls = [p for p in open_pulls if p.get("head", {}).get("ref") == branch]
        print(json.dumps(open_pulls))
        return 0

    if repo:
        visibility = _visibility()
        print(json.dumps({"name": repo.group(2), "full_name": f"{repo.group(1)}/{repo.group(2)}",
                          "visibility": visibility, "private": visibility != "public",
                          "default_branch": "main"}))
        return 0

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
