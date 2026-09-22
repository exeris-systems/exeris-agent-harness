"""Reading the vendor client's per-session log: what a run record may take from it.

The log is the only place a local run's counts exist, and it was written for a terminal rather
than for a dataset, so the cases here are the ones that decide whether a row is written at all
and what it may contain.

* *One line is not one turn.* A client writes one assistant message over several lines when it
  streams, and each of those lines repeats the message's `usage` block. Counting lines inflates
  both the turn count and every token figure; the identity is `message.id`, and it is what the
  counts are taken over.
* *A subagent is part of the run, and a subagent on another model is not.* Tool calls a subagent
  makes were made by the run and are counted; a subagent whose model differs means the row's
  single `agent.model_id` would name a model that did not take all the turns, and the producer
  records nothing — the measurement ADR-086's withdrawn schema change is re-owed on.
* *The client's version is part of the model reference.* A version that moved mid-session leaves
  `agent.harness.version` unable to name one value, so that too is a refusal rather than a choice.
* *A count is metadata; the thing counted is not.* The first prompt is hashed in memory and the
  text never reaches the facts; the currency figure the client keeps is a price-list computation
  and is never read (ADR-086 §C.14, Engineering Protocol 8).

The fixtures are written by hand and carry placeholder text only: a real session line is content,
and content does not enter this repository in any form.
"""

import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SESSION = FIXTURES / "session.jsonl"
DIVERGENT = FIXTURES / "session-divergent.jsonl"
TWO_VERSIONS = FIXTURES / "session-two-versions.jsonl"
TWO_MODELS = FIXTURES / "session-two-models.jsonl"
#: A session carrying a value in `message.model` that the row contract's own pattern refuses. The
#: client writes one for a message the provider never produced, so it is a placeholder rather than
#: a model the run ran under.
PLACEHOLDER_MODEL = FIXTURES / "session-placeholder-model.jsonl"

MODEL = "claude-sonnet-5"
CLIENT_VERSION = "2.1.278"
FIXTURE_CWD = "/tmp/placeholder/wt"

# What the fixture holds, stated here rather than recomputed, so that a case fails against the
# number a reader can check by eye against the file.
TURNS = 4                 # four distinct `message.id` on the main chain, over six lines
TOOL_CALLS = 5            # `tool_use` blocks, the subagent's included
HUMAN_PROMPTS = 2         # three prompts on the main chain, less the one that opened the run
PERMISSION_DENIALS = 1
USAGE = {"input_tokens": 1500, "output_tokens": 150,
         "cache_read_tokens": 15000, "cache_write_tokens": 350}

#: The log's own spellings beside the row contract's. The totals are what this file pins; which
#: name the adapter hands them over under is settled where the row is assembled.
USAGE_ALIASES = {
    "input_tokens": ("input_tokens",),
    "output_tokens": ("output_tokens",),
    "cache_read_tokens": ("cache_read_tokens", "cache_read_input_tokens"),
    "cache_write_tokens": ("cache_write_tokens", "cache_creation_input_tokens"),
}


def _adapter():
    """`adapters/claude/session.py`, imported from the checkout rather than from a package.

    The adapters are not an installed package — `bin/exeris-agent` puts the checkout on the path
    and nothing else does — so the module is loaded by its path, which is also how the harness
    finds it.
    """
    path = _REPO_ROOT / "adapters" / "claude" / "session.py"
    if not path.is_file():
        raise ImportError(f"no session adapter at {path}")
    spec = importlib.util.spec_from_file_location("exeris_adapters_claude_session", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fact(facts, name, *holders):
    """One fact, at the top level or inside a holder the reader groups it under."""
    if isinstance(facts, dict):
        if name in facts:
            return facts[name]
        for holder in holders:
            inner = facts.get(holder)
            if isinstance(inner, dict) and name in inner:
                return inner[name]
    raise AssertionError(f"the session facts carry no {name!r}: "
                         f"{sorted(facts) if isinstance(facts, dict) else facts!r}")


def _usage(facts):
    """The four token totals, under whichever of the two spellings the reader used."""
    raw = _fact(facts, "usage", "accounting")
    if not isinstance(raw, dict):
        raise AssertionError(f"`usage` is not a mapping: {raw!r}")
    out = {}
    for name, spellings in USAGE_ALIASES.items():
        for spelling in spellings:
            if spelling in raw:
                out[name] = raw[spelling]
                break
        else:
            raise AssertionError(f"`usage` carries no {name} ({'/'.join(spellings)}): "
                                 f"{sorted(raw)}")
    return out


def _first_timestamp(path):
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            stamp = json.loads(line).get("timestamp")
            if stamp:
                return stamp
    raise AssertionError(f"{path} carries no timestamped line")


def _first_prompt(path):
    """The text of the first prompt, read here so the hash below is computed and not copied."""
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str) and content:
            return content
    raise AssertionError(f"{path} carries no prompt")


class SessionReadTest(unittest.TestCase):
    """`read(path)` — the counts, and the two cases that yield no row."""

    def setUp(self):
        self.session = _adapter()

    def _refusal(self, call):
        """The reason a refused session names, whether the reader raises it or returns it."""
        try:
            returned = call()
        except Exception as refused:
            return f"{type(refused).__name__}: {refused}"
        return repr(returned)

    def test_the_counts_are_taken_over_message_ids_not_over_lines(self):
        facts = self.session.read(str(SESSION))

        self.assertEqual(MODEL, _fact(facts, "model_id", "agent"))
        self.assertEqual(CLIENT_VERSION, str(_fact(facts, "version", "harness", "agent")))
        self.assertEqual(TURNS, _fact(facts, "turns", "execution"))
        # The subagent's calls are the run's: it ran inside the run, under the run's identity.
        self.assertEqual(TOOL_CALLS, _fact(facts, "tool_calls", "execution"))
        self.assertEqual(HUMAN_PROMPTS, _fact(facts, "human_prompts", "execution"))
        self.assertEqual(PERMISSION_DENIALS, _fact(facts, "permission_denials", "execution"))

        # Two of the four ids are written over two lines each, repeating their usage block. Every
        # figure here is the sum over ids; the sum over lines is larger, which is the defect.
        self.assertEqual(USAGE, _usage(facts))

    def test_event_count_is_every_line_of_the_log(self):
        lines = [line for line in SESSION.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), _fact(self.session.read(str(SESSION)),
                                           "event_count", "execution", "event_stream"))

    def test_a_value_the_contract_could_not_carry_is_not_a_second_model(self):
        # `agent.model_id` has to survive into the contract's own field, so a value that field
        # would refuse is not one of the models a run ran under. Admitting one would report a
        # session as having changed model on the strength of something no provider produced, and
        # almost every long session carries one.
        facts = self.session.read(str(PLACEHOLDER_MODEL))
        self.assertEqual(MODEL, _fact(facts, "model_id", "agent"))

    def test_two_models_on_the_main_chain_yield_no_row(self):
        # The inverse of the case above, and the reason the filter is not simply a wider pattern:
        # a second model that the contract would carry is a run whose single `agent.model_id`
        # names a model that did not take all the turns.
        self.assertIn("multi-model", self._refusal(lambda: self.session.read(str(TWO_MODELS))))

    def test_a_subagent_on_another_model_yields_no_row(self):
        # `agent.model_id` is singular and names the model that took the turns. Where a subagent
        # took some of them on a different model, no single value is true of the run.
        self.assertIn("subagent-model-divergence",
                      self._refusal(lambda: self.session.read(str(DIVERGENT))))

    def test_a_client_version_that_moved_mid_session_yields_no_row(self):
        self.assertIn("harness-version-moved",
                      self._refusal(lambda: self.session.read(str(TWO_VERSIONS))))

    def test_the_cost_figure_the_client_keeps_is_never_read(self):
        # A currency figure a runtime prints under a subscription is computed from a price list
        # that moves under it. The schema refuses it; a producer that carried it this far would
        # only have to drop it later, so it is not read at all.
        self.assertNotIn("totalCostUSD", repr(self.session.read(str(SESSION))))

    def test_the_first_prompt_is_hashed_and_not_carried(self):
        prompt = _first_prompt(SESSION)
        facts = self.session.read(str(SESSION))

        # The fixture's prompt ends with the newline §C.14 makes the component terminator, so the
        # digest is over exactly the bytes the log holds and no terminator has to be guessed at.
        self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                         _fact(facts, "system_prompt_sha256", "agent"))
        self.assertNotIn(prompt.strip(), repr(facts),
                         "the prompt text reached the facts; a count is metadata and the thing "
                         "counted is not")


class SessionLocateTest(unittest.TestCase):
    """`locate(manifest)` — one run, one session, or nothing.

    The client files its log under the working directory it was started in, so the run's worktree
    is the address and `open-run` already recorded the slug. Two sessions in one worktree are
    indistinguishable from outside, and a producer that picked one would be guessing which run a
    row describes.
    """

    def setUp(self):
        self.session = _adapter()
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-session-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        # The client derives the directory from the working directory and nothing moves it, so the
        # only root a test can place is the home the harness expands.
        self.home = self.tmp / "home"
        self.projects = self.home / ".claude" / "projects"
        self.slug = FIXTURE_CWD.replace("/", "-").replace(".", "-")
        self.dir = self.projects / self.slug
        self.dir.mkdir(parents=True)
        self._set_env("HOME", str(self.home))

        started = datetime.datetime.fromisoformat(
            _first_timestamp(SESSION).replace("Z", "+00:00")) - datetime.timedelta(seconds=30)
        self.manifest = {
            "run_id": "01M00000000000000000000000",
            "repo": "exeris-systems/exeris-agent-harness",
            "provider": "claude",
            "worktree": FIXTURE_CWD,
            "cwd_slug": self.slug,
            "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _set_env(self, name, value):
        previous = os.environ.get(name)
        os.environ[name] = value
        self.addCleanup(lambda: os.environ.__setitem__(name, previous)
                        if previous is not None else os.environ.pop(name, None))

    def _write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def _the_run_s_session(self):
        return self._write("00000000-0000-4000-8000-0000000000aa.jsonl",
                           SESSION.read_text(encoding="utf-8"))

    def _locate(self, override=None):
        """`locate`, with the override `--session` passes through, under either spelling."""
        if override is None:
            return self.session.locate(self.manifest)
        for attempt in (lambda: self.session.locate(self.manifest, override),
                        lambda: self.session.locate(self.manifest, override=override),
                        lambda: self.session.locate(self.manifest, session=override)):
            try:
                return attempt()
            except TypeError:
                continue
        raise AssertionError("locate() takes no session override")

    def test_the_session_of_this_run_is_the_one_that_matches_the_tree_and_the_hour(self):
        mine = self._the_run_s_session()
        # An earlier session in the same tree: the same directory, outside the run's window.
        self._write("00000000-0000-4000-8000-0000000000bb.jsonl",
                    SESSION.read_text(encoding="utf-8").replace("2026-09-20T", "2026-09-19T"))

        self.assertEqual(os.path.realpath(mine), os.path.realpath(str(self._locate())))

        # A session of another tree, filed here: in the window, but not this run's worktree.
        self._write("00000000-0000-4000-8000-0000000000cc.jsonl",
                    SESSION.read_text(encoding="utf-8").replace(FIXTURE_CWD,
                                                                "/tmp/placeholder/other-wt"))
        self.assertEqual(os.path.realpath(mine), os.path.realpath(str(self._locate())))

    def test_two_sessions_of_one_run_are_refused_rather_than_chosen_between(self):
        self._the_run_s_session()
        self._write("00000000-0000-4000-8000-0000000000dd.jsonl",
                    SESSION.read_text(encoding="utf-8"))
        try:
            found = self._locate()
        except Exception as refused:
            self.assertIn("multiple-sessions", f"{type(refused).__name__}: {refused}")
        else:
            self.fail(f"two matching sessions resolved to {found!r} instead of being refused")

    def test_the_session_override_answers_where_the_rule_cannot(self):
        # The override is the answer to a refusal, so it names the file outright and is not
        # re-checked against the rule that refused.
        self._the_run_s_session()
        self._write("00000000-0000-4000-8000-0000000000dd.jsonl",
                    SESSION.read_text(encoding="utf-8"))
        named = self.tmp / "named-by-hand.jsonl"
        named.write_text(SESSION.read_text(encoding="utf-8"), encoding="utf-8")

        self.assertEqual(os.path.realpath(named),
                         os.path.realpath(str(self._locate(override=str(named)))))


if __name__ == "__main__":
    unittest.main()
