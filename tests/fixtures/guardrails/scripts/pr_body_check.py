#!/usr/bin/env python3
"""A stand-in for the organisation's pull-request template check, in its output format.

The harness runs the organisation's own script and carries no copy of it, so its tests run this one:
the same command line (`--body-file`, `--author`), the same `GUARDRAILS_ADR_TOUCHED` variable, the
same `::error …::<message>` lines and the same exit status. The rules are a subset — the sections,
an unanswered classification line, the `Owner:` line an application's pull request carries, and
the `Refs:` line a change to an ADR carries — which is what the harness's cases need to tell a
passing body from a failing one. Every invocation is recorded beside the script.
"""

import argparse
import json
import os
import re
import sys

HEADINGS = ("Motivation:", "Modification:", "Result:", "## Classification", "## Verification")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--author")
    a = ap.parse_args()
    with open(a.body_file, encoding="utf-8") as handle:
        body = handle.read()
    adr = os.environ.get("GUARDRAILS_ADR_TOUCHED") == "1"
    with open(STATE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"author": a.author, "adr_touched": adr, "body": body}) + "\n")
    errors = [f"missing template section '{h}'" for h in HEADINGS if h not in body]
    errors += [f"'{m.group(1)}:' still holds the placeholder"
               for m in re.finditer(r"^([A-Za-z ]+): <.*>$", body, re.M)]
    owners = re.findall(r"^Owner:", body, re.M)
    if a.author == "exeris-agent[bot]" and len(owners) != 1:
        errors.append(f"the body has {len(owners)} 'Owner:' lines — exactly one is required")
    if adr and not re.search(r"^Refs: ADR-\d{3}", body, re.M):
        errors.append("PR adds or amends an ADR but has no 'Refs: ADR-NNN' trailer")
    for error in errors:
        print(f"::error file=PR body,line=1,title=pr_body_check::{error}")
    print(f"## pr_body_check\n\nChecked **1** files — **{len(errors)} errors**, 0 warnings.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
