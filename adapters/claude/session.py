"""What the client wrote down about one run, and where it wrote it.

The client keeps one newline-delimited JSON file per session, under a directory named after the
working directory the session was started in — the path with its separators and dots turned into
dashes. A run's worktree is a directory nothing else starts a session in, which is what makes the
file findable: the harness records the slug when it opens the run, and this module matches on the
tree the session ran in and on the moment it opened.

Everything below is read and nothing is written. The client's own state is the person's, including
their subscription login, and the harness has no business in it.

**What is deliberately not read: the cost line.** The client writes a running total in dollars.
Under a subscription no per-run price exists, so that figure is a price list applied to token
counts — imputed, not reported — and a row carrying it would put an imputed cost in the same column
as a reported one, where the two are indistinguishable. The token counts are read; the dollars are
not, and no code path here can reach them.

**What is deliberately not returned: text.** The first prompt is hashed where it is read, because
the digest is what the model reference needs and the text may be private material. No prompt, file
or tool argument leaves this module.

**What is counted as steering: a person's prompts, never the oracle's.** A run driven with the
oracle in the loop resumes its session with prompts the harness wrote from failing gates, and the
harness passes their digests in. A prompt whose digest is one of those is the instrument speaking
and is not counted; every one of them has to be found, because a recorded prompt the log does not
hold leaves the remaining prompts unattributable, and the count is then refused rather than guessed.
"""

import collections
import datetime
import hashlib
import json
import os
import re

from harness.capture import NoRow

#: The client whose log this reads, as `agent.harness.client` spells it. One model under two
#: clients is two harnesses, so this name is part of the model reference and not decoration.
CLIENT = "claude-code"

DEFAULT_PROJECTS_ROOT = "~/.claude/projects"

#: A session may open slightly before the harness stamps the run — the client starts, the run's
#: manifest is written — so the window opens a minute early and closes when the run does.
WINDOW_BEFORE = datetime.timedelta(seconds=60)

#: `agent.model_id`'s own pattern, from the row contract. A value the contract could not carry is
#: not a model this run ran under: the client writes a placeholder in that field for a message the
#: provider never produced, and admitting one would report every long session as having changed
#: model. The contract's shape is the test because it is the shape the value has to survive into.
_MODEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:/@+-]*[A-Za-z0-9])?$")
_MODEL_MAX = 128


def _slug(path: str) -> str:
    """The per-worktree identity a session log is filed under."""
    return str(path).replace("/", "-").replace(".", "-")


def _stamp(text) -> datetime.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=datetime.UTC)


def _same_tree(one, other) -> bool:
    if not one or not other:
        return False
    return os.path.realpath(str(one)) == os.path.realpath(str(other))


def _opening(path: str) -> tuple[str | None, datetime.datetime | None]:
    """The tree a session ran in and the moment it opened, from the head of its log.

    Both are read from the first line that carries each: the line types the client writes first are
    not fixed, and a session that opens with an injected line carries the same two facts one line
    later.
    """
    cwd = None
    first = None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                if cwd is None and isinstance(entry.get("cwd"), str):
                    cwd = entry["cwd"]
                if first is None:
                    first = _stamp(entry.get("timestamp"))
                if cwd is not None and first is not None:
                    break
    except OSError:
        return None, None
    return cwd, first


def locate(manifest: dict, *, projects_root: str | None = None, override: str | None = None) -> str:
    """The run's session log, or `NoRow` where there is not exactly one.

    A candidate is a log in the directory the run's tree is filed under, opened in the tree the run
    owns, within the run's own window — from a minute before it was stamped, because the client
    starts before the manifest is written, to the moment it closed. A run still open has no closing
    moment yet, and the window then runs to now.

    `override` is the person's own answer where the rule cannot give one: a session started before
    the run, a log moved, a tree the client recorded under another path. It is taken as given,
    because a person naming a file is evidence and the rule below is only a search.
    """
    if override:
        if not os.path.isfile(override):
            raise NoRow("session-not-found", override)
        return override

    worktree = manifest.get("worktree")
    slug = manifest.get("cwd_slug") or (_slug(worktree) if worktree else None)
    opened = _stamp(manifest.get("started_at"))
    closed = _stamp(manifest.get("ended_at")) or datetime.datetime.now(datetime.UTC)
    if not slug or opened is None:
        raise NoRow("session-not-found", "the run says neither when nor where it ran")

    directory = os.path.join(os.path.abspath(os.path.expanduser(
        projects_root or DEFAULT_PROJECTS_ROOT)), slug)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        raise NoRow("session-not-found", directory) from None

    window = opened - WINDOW_BEFORE
    found = []
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(directory, name)
        cwd, first = _opening(path)
        if first is None or not _same_tree(cwd, worktree):
            continue
        if window <= first <= closed:
            found.append(path)
    if not found:
        raise NoRow("session-not-found", directory)
    if len(found) > 1:
        # Two sessions opened in one run's tree inside the run's window: the counts of one of them
        # are not the counts of this run, and nothing in either log says which.
        raise NoRow("multiple-sessions", ", ".join(os.path.basename(p) for p in found))
    return found[0]


def _is_model(value) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _MODEL_MAX
            and bool(_MODEL.match(value)))


def _blocks(content):
    return content if isinstance(content, list) else []


def _tool_uses(content) -> int:
    return sum(1 for block in _blocks(content)
               if isinstance(block, dict) and block.get("type") == "tool_use")


def _usage(reported) -> dict:
    """The four token counts under the row contract's own names.

    The client's names for the two cache counts are its own; the record's are the record's, and the
    mapping is stated here rather than left to whoever reads the column later.
    """
    if not isinstance(reported, dict):
        return {}
    pairs = (("input_tokens", "input_tokens"),
             ("output_tokens", "output_tokens"),
             ("cache_read_tokens", "cache_read_input_tokens"),
             ("cache_write_tokens", "cache_creation_input_tokens"))
    out = {}
    for ours, theirs in pairs:
        value = reported.get(theirs)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[ours] = value
    return out


def _prompt_text(content) -> str | None:
    """The text of a prompt a person submitted, or nothing where the line is not one.

    A `user` line carries either what a person typed or what a tool returned, and the second is the
    client speaking to itself. A line carrying any tool result is not a prompt however much text
    sits beside it.
    """
    if isinstance(content, str):
        return content or None
    parts = []
    for block in _blocks(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_result":
            return None
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    text = "".join(parts)
    return text or None


def _entries(raw: bytes):
    """Every line of the log that parses as a JSON object, in order.

    A line that does not parse, or parses as something else, is not an entry. The count of what is
    yielded is `event_count`: what keeps the row summarisable by once the stream itself has been
    taken away by a retention window.
    """
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(entry, dict):
            yield entry


class _Tally:
    """What `read` accumulates over one log, one entry at a time."""

    def __init__(self, oracle_prompts):
        self.event_count = 0
        self.versions: set[str] = set()
        self.main_models: set[str] = set()
        self.side_models: set[str] = set()
        self.turns: set[str] = set()
        self.tool_calls = 0
        self.usage: dict[str, dict] = {}
        self.prompts = 0
        self.denials = 0
        self.first_prompt_sha256 = None
        self.pending = collections.Counter(oracle_prompts)
        self.from_oracle = 0

    def add(self, entry: dict) -> None:
        self.event_count += 1
        version = entry.get("version")
        if isinstance(version, str) and version:
            self.versions.add(version)
        kind = entry.get("type")
        sidechain = bool(entry.get("isSidechain"))
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        if kind == "assistant":
            self._assistant(message, sidechain)
        elif kind == "user":
            self._user(entry, message, sidechain)

    def _assistant(self, message: dict, sidechain: bool) -> None:
        model = message.get("model")
        if _is_model(model):
            (self.side_models if sidechain else self.main_models).add(model)
        # A turn is a message, and one message reaches the log as several lines when it carries
        # several content blocks. The id is what collapses them; a line without one contributes no
        # turn and no usage, because a measurement that cannot be de-duplicated would be counted as
        # often as the client happened to write it.
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            if not sidechain:
                self.turns.add(message_id)
            if message_id not in self.usage:
                self.usage[message_id] = _usage(message.get("usage"))
        self.tool_calls += _tool_uses(message.get("content"))

    def _user(self, entry: dict, message: dict, sidechain: bool) -> None:
        if entry.get("toolDenialKind") is not None:
            self.denials += 1
        if sidechain or entry.get("isMeta"):
            return
        text = _prompt_text(message.get("content"))
        if text is None:
            return
        self.prompts += 1
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if self.first_prompt_sha256 is None:
            self.first_prompt_sha256 = digest
        elif self.pending[digest] > 0:
            self.pending[digest] -= 1
            self.from_oracle += 1

    def model_id(self, path: str) -> str:
        """The one model the session ran under, or `NoRow` where the log states anything else."""
        if not self.versions:
            raise NoRow("harness-version-absent", path)
        if len(self.versions) > 1:
            raise NoRow("harness-version-moved", ", ".join(sorted(self.versions)))
        if not self.main_models:
            raise NoRow("model-absent", path)
        if len(self.main_models) > 1:
            raise NoRow("multi-model", ", ".join(sorted(self.main_models)))
        model_id = sorted(self.main_models)[0]
        divergent = sorted(self.side_models - {model_id})
        if divergent:
            raise NoRow("subagent-model-divergence", ", ".join(divergent))
        unmatched = sum(self.pending.values())
        if unmatched:
            raise NoRow("oracle-prompt-unmatched",
                        f"{unmatched} prompt(s) the oracle loop recorded sending are not in {path}")
        return model_id

    def totals(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for counted in self.usage.values():
            for name, value in counted.items():
                out[name] = out.get(name, 0) + value
        return out


def read(path: str, *, oracle_prompts=()) -> dict:
    """Everything the row needs from one session log, in counts and digests.

    `oracle_prompts` are the digests of the prompts an oracle loop sent into this session, one per
    feedback round and repeated where two rounds sent the same text. Each is matched against one
    prompt after the first; the ones matched are not steering.

    Raises `NoRow` where the log describes something the record cannot state: two models on the
    main chain, a subagent on a third, or a client version that moved under the session. Each of
    those is a run whose model reference is not one thing, and a row asserting one would be a claim
    about conditions that did not hold.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise NoRow("session-unreadable", str(exc)) from None

    tally = _Tally(oracle_prompts)
    for entry in _entries(raw):
        tally.add(entry)
    model_id = tally.model_id(path)

    return {
        "path": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": tally.event_count,
        "model_id": model_id,
        "client": CLIENT,
        "version": min(tally.versions),
        "turns": len(tally.turns),
        "tool_calls": tally.tool_calls,
        "usage": tally.totals(),
        # The first prompt is what started the run; what is counted here is steering, which is why
        # a run asked once and left to finish is `0` rather than `1`, and the oracle's prompts are
        # not steering at all.
        "human_prompts": max(tally.prompts - 1 - tally.from_oracle, 0),
        "permission_denials": tally.denials,
        # The prompt's own digest, which the record composes with the rest of what instructed the
        # run. The text was hashed where it was read and is not here.
        "system_prompt_sha256": tally.first_prompt_sha256,
        "capture_level": "full",
    }
