"""Which arm a run is: the provider table, and what of it reaches the row.

An arm is a model behind a client, under a ledger, launched by an adapter. Those four travel
together — a model id without the client that ran it is not a model reference, and a client without
the ledger it was billed under is not an accounting row — so they are declared together, in one
`[providers.<name>]` table, and `--provider <name>` names the table rather than the vendor.

**The table is configuration and nothing else.** It says which arms exist on this machine; it says
nothing about which arm suits which workload, and no code here reads a workload. A harness that
ranked its own arms would be a router, and the layer this harness produces rows for observes before
it routes. What the arms are for is what a comparison of the rows may one day say, not what the
producer says while it writes them.

What a table declares:

    [providers.<name>]
    provider        = "…"   # the row's `agent.provider` — the vendor whose model took the turns
    model_id        = "…"   # what the adapter launches with; the row carries what it observed
    credential      = "…"   # `api`, `subscription` or `local` — the row's `accounting.mode`
    adapter         = "…"   # which adapter launches it and reads its session log
    weights         = "…"   # a local weights file, whose digest is the row's `model_snapshot`
    harness_client  = "…"   # what the adapter cannot read of `agent.harness`, declared
    harness_version = "…"
    [providers.<name>.env]  # what the adapter exports for the run, values or `file:<path>`
    …

`weights` and `credential = "local"` are one fact stated twice: a digest names weights on hardware
this organisation runs, and the row contract refuses such a snapshot on any other ledger. So a
table that declares weights under a provider's ledger is refused here rather than written into a
row a validator would reject at the far end.

Nothing in a table is a secret. An environment value that has to be one is written as
`file:<path>`, and the file is read when the run opens — the same treatment the run's own token
gets, in the same directory, at the same mode. A value pasted inline would be a credential in a
configuration file that otherwise holds none, and the file's own comment says it holds none.
"""

import dataclasses
import hashlib
import os
import re

from . import ROOT
from .runstate import RUN_ENV_KEYS, UNSET

#: The ledgers a run can belong to, as the row contract spells them. A fourth value is not a fourth
#: kind of ledger; it is a configuration nobody has decided the meaning of.
CREDENTIALS = ("api", "subscription", "local")

#: `agent.provider`'s own pattern, from the row contract. It is checked here because this is where
#: the value is declared, and a value the contract cannot carry is a run that opens and yields no
#: row for a reason the configuration could have stated at the start.
_PROVIDER = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: The name of a table, and of an environment variable. Both are narrow on purpose: the first
#: becomes part of what a person types, the second is exported into a shell.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: How an environment value names a file to be read rather than a value to be used.
FILE = "file:"

#: Adapters whose client is handed the task on its command line and reads nothing from a terminal.
#: A run under one of them has no prompt unless the harness passes it a file, and its stream
#: carries no prompt text afterwards — so the digest the model reference needs has exactly one
#: source, and a run opened without it could never be recorded.
PROMPTED = ("antigravity",)

#: The vendor CLIs, as defaults under their own names. They declare the vendor and the adapter,
#: which are properties of the client; they declare no ledger, because which ledger a vendor CLI
#: bills to is a property of the login it runs under and not of the client. A `[providers.<name>]`
#: table of the same name completes or replaces any of these.
BUILTIN = {
    "claude": {"provider": "anthropic", "adapter": "claude"},
    "codex": {"provider": "openai", "adapter": "codex"},
    "gemini": {"provider": "google", "adapter": "gemini"},
}

#: What a table may say. A key outside this set is a misspelling of one inside it, and a
#: misspelling that is ignored is a setting the person believes is in force.
_KEYS = ("provider", "model_id", "credential", "adapter", "weights", "env", "harness_client",
         "harness_version")


class ProviderError(Exception):
    """A provider table the harness will not open a run under."""


@dataclasses.dataclass(frozen=True)
class Provider:
    """One arm, resolved: the table as configured, with what had to be read already read."""

    name: str
    provider: str
    adapter: str
    credential: str | None = None
    model_id: str | None = None
    weights: str | None = None
    model_snapshot: str | None = None
    env: dict = dataclasses.field(default_factory=dict)
    harness_client: str | None = None
    harness_version: str | None = None

    def recorded(self) -> dict:
        """What the manifest keeps of this arm — and what it deliberately does not keep.

        The values of the launch environment are absent and the names of its variables are present.
        A manifest is the record of what a run was; a token read out of a file to be exported is a
        credential, and a credential belongs where the run's own token is, at that file's mode,
        not in the record of the run.
        """
        return {
            "name": self.name,
            "provider": self.provider,
            "adapter": self.adapter,
            "credential": self.credential,
            "model_id": self.model_id,
            "weights": self.weights,
            "model_snapshot": self.model_snapshot,
            "env_keys": sorted(self.env),
            "harness_client": self.harness_client,
            "harness_version": self.harness_version,
        }


def adapters() -> tuple[str, ...]:
    """The adapters this checkout carries, by name."""
    base = os.path.join(ROOT, "adapters")
    try:
        return tuple(sorted(name for name in os.listdir(base)
                            if os.path.isdir(os.path.join(base, name))))
    except OSError:
        return ()


def names(cfg) -> tuple[str, ...]:
    """Every provider `--provider` accepts: the built-in defaults and the configured tables."""
    return tuple(sorted(set(BUILTIN) | set(getattr(cfg, "providers", {}) or {})))


def digest(path: str) -> str:
    """The weights' SHA-256, as `model_snapshot` carries it.

    Read in blocks because a weights file is measured in gigabytes, and computed when the run opens
    rather than when it closes: the file a run loaded is the file that was there when it started,
    and a digest taken afterwards would name whatever replaced it.
    """
    body = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                body.update(block)
    except OSError as exc:
        raise ProviderError(f"the weights at {path} could not be read: {exc}") from None
    return body.hexdigest()


def _text(cfg, table: dict, key: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProviderError(f"{key} is not a string")
    return value.strip()


def _env(cfg, table: dict) -> dict:
    """The launch environment, with a `file:` value replaced by what the file holds.

    Read here rather than in the adapter because the adapter is a shell script the person may run
    by hand, and a value it had to resolve for itself would be a second implementation of this
    rule. What is exported is exported from the run's own environment file, which carries the run's
    token already and is written at that file's mode.
    """
    raw = table.get("env") or {}
    if not isinstance(raw, dict):
        raise ProviderError("env is not a table")
    out = {}
    for key, value in raw.items():
        if not _ENV_NAME.match(str(key)):
            raise ProviderError(f"env.{key} is not the name of an environment variable")
        if key in UNSET or key in RUN_ENV_KEYS:
            # The run's boundary is made of these names. A launch environment that set one would
            # be handing the run a credential the harness did not mint, or replacing the identity
            # the harness bound to the tree, from a file that says it holds no secret.
            raise ProviderError(f"env.{key} is part of the run's own boundary and cannot be set "
                                f"by a provider table")
        if not isinstance(value, str):
            raise ProviderError(f"env.{key} is not a string")
        if value.startswith(FILE):
            path = cfg.beside(value[len(FILE):])
            try:
                with open(path, encoding="utf-8") as handle:
                    value = handle.read().strip()
            except OSError as exc:
                raise ProviderError(f"env.{key} names {path}, which could not be read: "
                                    f"{exc}") from None
            if not value:
                raise ProviderError(f"env.{key} names {path}, which is empty")
        out[str(key)] = value
    return out


def resolve(cfg, name: str, *, prompt_file: str | None = None) -> Provider:
    """The arm `--provider <name>` names, checked and ready for the run's manifest.

    Everything a table can be wrong about is wrong here, before a run directory or a token exists:
    a run that opened under a table the contract cannot carry would be a run whose only possible
    end is a refusal to write its row.
    """
    if not _NAME.match(str(name)):
        raise ProviderError(f"--provider {name!r} is not the name of a provider table")
    configured = (getattr(cfg, "providers", {}) or {}).get(name)
    if configured is None and name not in BUILTIN:
        raise ProviderError(f"--provider {name!r} names no [providers.{name}] table and is not "
                            f"one of the built-in defaults; this machine has: "
                            f"{', '.join(names(cfg))}")
    if configured is not None and not isinstance(configured, dict):
        raise ProviderError(f"[providers.{name}] is not a table")
    unknown = sorted(set(configured or {}) - set(_KEYS))
    if unknown:
        raise ProviderError(f"[providers.{name}] declares {', '.join(unknown)}, which the harness "
                            f"does not read")

    table = dict(BUILTIN.get(name) or {})
    table.update(configured or {})

    provider = _text(cfg, table, "provider")
    if not provider:
        raise ProviderError(f"[providers.{name}] declares no provider; it is the row's "
                            f"`agent.provider` and there is no second source for it")
    if not _PROVIDER.match(provider):
        raise ProviderError(f"[providers.{name}].provider {provider!r} is not a value the row "
                            f"contract can carry")

    adapter = _text(cfg, table, "adapter")
    if not adapter:
        raise ProviderError(f"[providers.{name}] declares no adapter; a run has to say which "
                            f"launcher opens it and which adapter reads its session log")
    if adapter not in adapters():
        raise ProviderError(f"[providers.{name}].adapter {adapter!r} is not one of the adapters "
                            f"this checkout carries: {', '.join(adapters())}")

    credential = _text(cfg, table, "credential")
    if credential is not None and credential not in CREDENTIALS:
        raise ProviderError(f"[providers.{name}].credential {credential!r} is not one of "
                            f"{', '.join(CREDENTIALS)}")

    model_id = _text(cfg, table, "model_id")
    if adapter in PROMPTED:
        if not model_id:
            raise ProviderError(f"[providers.{name}].model_id is required: the {adapter} adapter "
                                f"names the model on the command line")
        if not prompt_file:
            raise ProviderError(f"--prompt-file is required under the {adapter} adapter: the task "
                                f"reaches the client on its command line, and its stream carries "
                                f"no prompt text afterwards")

    weights = _text(cfg, table, "weights")
    snapshot = None
    if weights:
        weights = cfg.beside(weights)
        if credential != "local":
            raise ProviderError(f"[providers.{name}] declares weights under credential "
                                f"{credential!r}; weights this machine can digest are weights it "
                                f"serves, and the row contract admits a digest only under `local`")
        if not os.path.isfile(weights):
            raise ProviderError(f"[providers.{name}].weights names {weights}, which is not a "
                                f"file; a run whose weights are absent has no snapshot to record")
        snapshot = f"sha256:{digest(weights)}"

    return Provider(
        name=name,
        provider=provider,
        adapter=adapter,
        credential=credential,
        model_id=model_id,
        weights=weights,
        model_snapshot=snapshot,
        env=_env(cfg, table),
        harness_client=_text(cfg, table, "harness_client"),
        harness_version=_text(cfg, table, "harness_version"),
    )
