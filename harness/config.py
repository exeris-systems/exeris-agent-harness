"""The harness's configuration: who the execution identity is, and where its key lives.

`~/.config/exeris-agent/config.toml` holds identifiers and one path. None of it is a secret: the
client id, the installation id and the bot user id are readable from GitHub by anyone who can see
the App, and the private key is a path, not a value. A configuration file that leaks says which App
exists; it does not let anybody act as it.

Shape — the `[github]` table is what the App registration produces, the rest is what the harness
adds::

    [github]
    client_id       = "…"       # required — the App's client id, the JWT's `iss`
    installation_id = 0          # required — the installation tokens are minted against
    bot_user_id     = 0          # required — the App's BOT USER id, not its App id
    private_key     = "…"        # required — a PEM path, or a `.age` path when encrypted at rest
    age_identity    = "…"        # the age identity `private_key` is encrypted to, when it is
    bot_login       = "exeris-agent[bot]"
    owner_login     = "…"        # the organisation member accountable for what a run produces
    org             = "exeris-systems"
    execution_repo  = "…"        # the local clone rows are carried into
    streams_repo    = "…"        # the local clone session streams are carried into

    [oracle]
    docs_index      = "…"        # the central ADR registry a documentation checkout is judged
                                 # against, in the clone that publishes it
    bridge          = "…"        # the Exeris MCP server's `dist/server.js`, absolute: passed to
                                 # the oracle, pinned in each run's manifest, and the one server
                                 # an arm with `mcp = true` is given

    [pull_request]
    body_check      = "…"        # the organisation's `scripts/pr_body_check.py`, in a clone of
                                 # exeris-systems/.github: every body is checked with it first

    [registry]
    path            = "…"        # the local clone of the private task registry; a `reg:T-NNN` run
                                 # reads `registry/tasks/T-NNN.json` there for its oracle inputs

    [repos.<name>]
    path            = "…"        # the local clone, when it is not beside this checkout
    domain          = "…"        # the oracle domain of the work done there
    scope           = […]        # the vocabulary `--scope` is checked against
    routine         = "…"        # the routine file a run there follows, hashed into the record
    readable        = ["…"]      # directories every driven pass may read, passed as `--add-dir`
    mcp             = false      # whether an arm working here is given the pinned bridge

    [providers.<name>]           # one arm: a model behind a client, under a ledger — `providers`
    …                            # owns the shape, and this module only carries the tables through

A repository declares what is true of work done there; a provider table declares what is true of
the arm that did it. The ledger a run is billed under belongs to the second: one repository is
worked by a vendor arm and a local arm on the same day, and a credential class read from the
repository would have reported both under whichever was configured there.

`execution_repo` and `streams_repo` are **paths to local clones**, not repository slugs: a flush
copies files into them, commits and pushes under the person's own identity, and the repository each
one is a clone of is read from its own origin. A relative path is read beside the configuration
file, as a key path is.

`execution_repo` is also where the documentation oracle is imported from, which is why the harness
carries no copy of it. `[oracle] docs_index` is the other half: a checkout that is not itself the
registry is judged against the central one, and the path says which clone publishes it. Without it
that gate does not run — an unnamed registry would otherwise have to be fetched, and a gate that
reports the network is not a gate.

Scalars are read from `[github]` first and from the document root second, so a file that grew the
added fields at the top level is understood the same way as one that put them beside the App's own.

`readable` names the directories outside the worktree a driven pass is given to read — the
standards the work is judged against, say. They are part of what every pass of a run there was
allowed, so they are a property of the repository and not of the arm: two arms of one group read
the same directories. A relative path is read beside the configuration file.

`[oracle] bridge` names one server for two readers. The oracle is handed it to judge with, and an
arm working in a repository whose table says `mcp = true` is given the same server as its context
tools, so the instrument and the arm read one registry through one program. Its version (the
`package.json` beside `dist/`) and its commit (where the package directory is a git checkout of its
own) are recorded when a run opens, because a server rebuilt afterwards is a different instrument.

`[pull_request] body_check` is the organisation's template check, run locally over a pull request's
body before the execution identity sends it. It is named rather than copied, for the reason the
oracle is imported rather than copied: a second copy of a gate is a second answer to what it
requires. Without it no pull request is opened — a run can still be pushed with `--no-pr`.

`[registry] path` is where a registered task's own record is read from. What it contributes is the
task's `oracle_inputs` — for the documentation oracle, the files whose bodies the task must leave as
they were — and that is a property of the task, fixed when it was planned, not of the arm or of the
repository. With the registry configured, a `reg:` run whose task file cannot be read does not open:
the oracle would otherwise judge the run with less than the task said it should.

A `[repos.<name>]` table is addressed by the bare repository name, and a table written as
`owner/name` resolves to the same entry: the two spellings name one repository, and a configured
vocabulary that is silently unmatched is a check that reports nothing while appearing to be made.
"""

import dataclasses
import os
import tomllib

DEFAULT_PATH = "~/.config/exeris-agent/config.toml"
DEFAULT_BOT_LOGIN = "exeris-agent[bot]"
DEFAULT_ORG = "exeris-systems"

_REQUIRED = ("client_id", "installation_id", "bot_user_id", "private_key")


class ConfigError(Exception):
    """A configuration the harness will not run on. Messages name keys and paths, never values."""


@dataclasses.dataclass(frozen=True)
class Repo:
    """What the configuration knows about one repository runs are opened against."""

    name: str
    path: str | None = None
    domain: str | None = None
    scope: tuple[str, ...] = ()
    routine: str | None = None
    readable: tuple[str, ...] = ()
    #: Whether an arm working here is given the Exeris MCP server `[oracle] bridge` pins. It is a
    #: property of the repository for the reason `readable` is: every arm of a group reads the
    #: same context.
    mcp: bool = False


@dataclasses.dataclass(frozen=True)
class Config:
    path: str
    client_id: str
    installation_id: int
    bot_user_id: int
    private_key: str
    age_identity: str | None = None
    bot_login: str = DEFAULT_BOT_LOGIN
    owner_login: str | None = None
    org: str = DEFAULT_ORG
    execution_repo: str | None = None
    streams_repo: str | None = None
    #: The central ADR registry the documentation oracle checks a checkout against, where the
    #: configuration names one. It is a path into the clone that publishes it rather than a copy,
    #: for the reason the oracle itself is imported and not copied: a second registry on disk is a
    #: second answer to what is registered.
    docs_index: str | None = None
    #: The Exeris MCP server's `dist/server.js`, where the configuration names one.
    bridge: str | None = None
    #: The local clone of the private task registry, where the configuration names one.
    registry: str | None = None
    #: The organisation's pull-request template check, where the configuration names one.
    body_check: str | None = None
    repos: dict[str, Repo] = dataclasses.field(default_factory=dict)
    #: The `[providers.<name>]` tables as the file wrote them. They are carried rather than
    #: interpreted: what an arm may declare is `providers`' own, and a second opinion here would
    #: be a second place a table is checked and a first place it is checked differently.
    providers: dict[str, dict] = dataclasses.field(default_factory=dict)

    @property
    def noreply_email(self) -> str:
        """The git identity GitHub attributes to the App's own avatar.

        It is built from the App's *bot user* id; the App id in the same position produces a
        well-formed address that GitHub attributes to nobody.
        """
        return f"{self.bot_user_id}+{self.bot_login}@users.noreply.github.com"

    def beside(self, value: str) -> str:
        """A configured path, absolute. A relative one is read beside the configuration file."""
        raw = os.path.expanduser(value)
        if not os.path.isabs(raw):
            raw = os.path.join(os.path.dirname(os.path.abspath(self.path)), raw)
        return os.path.abspath(raw)

    @property
    def key_path(self) -> str:
        """The private key, absolute."""
        return self.beside(self.private_key)

    @property
    def execution_repo_path(self) -> str | None:
        """The clone run records are carried into, absolute."""
        return self.beside(self.execution_repo) if self.execution_repo else None

    @property
    def streams_repo_path(self) -> str | None:
        """The clone session streams are carried into, absolute."""
        return self.beside(self.streams_repo) if self.streams_repo else None

    @property
    def docs_index_path(self) -> str | None:
        """The central ADR registry, absolute, or nothing where none is configured."""
        return self.beside(self.docs_index) if self.docs_index else None

    @property
    def bridge_path(self) -> str | None:
        """The Exeris MCP server's entry point, absolute, or nothing where none is configured."""
        return self.beside(self.bridge) if self.bridge else None

    @property
    def registry_path(self) -> str | None:
        """The task registry's clone, absolute, or nothing where none is configured."""
        return self.beside(self.registry) if self.registry else None

    @property
    def body_check_path(self) -> str | None:
        """The pull-request template check, absolute, or nothing where none is configured."""
        return self.beside(self.body_check) if self.body_check else None

    @property
    def age_identity_path(self) -> str | None:
        """The age identity the key is decrypted with, absolute, or nothing when none is set."""
        return self.beside(self.age_identity) if self.age_identity else None

    def repo(self, name: str) -> Repo:
        """The `[repos.<name>]` entry, or an empty one — an unconfigured repository is usable.

        The bare name is the address; a table written as `owner/name` resolves to the same entry,
        because a vocabulary configured under the other spelling would otherwise be ignored rather
        than enforced, and a check that silently matches nothing reads exactly like one that passed.
        """
        found = self.repos.get(name)
        if found is None:
            for key, entry in self.repos.items():
                if key.rsplit("/", 1)[-1] == name:
                    return entry
        return found or Repo(name=name)


def _scalar(document: dict, table: dict, key: str, default=None):
    """`[github]` wins over the document root; the root is the fallback, not a second source."""
    if key in table:
        return table[key]
    value = document.get(key, default)
    return default if isinstance(value, dict) else value


def _repo(resolved: str, name: str, table) -> Repo:
    """One `[repos.<name>]` table, checked."""
    if not isinstance(table, dict):
        raise ConfigError(f"{resolved}: [repos.{name}] is not a table")
    scope = table.get("scope") or ()
    if isinstance(scope, str):
        scope = (scope,)
    readable = table.get("readable") or ()
    if isinstance(readable, str):
        readable = (readable,)
    if not all(isinstance(entry, str) and entry for entry in readable):
        raise ConfigError(f"{resolved}: [repos.{name}].readable is not a list of paths")
    mcp = table.get("mcp", False)
    if not isinstance(mcp, bool):
        raise ConfigError(f"{resolved}: [repos.{name}].mcp is not true or false")
    return Repo(name=name,
                path=table.get("path"),
                domain=table.get("domain"),
                scope=tuple(scope),
                routine=table.get("routine"),
                readable=tuple(readable),
                mcp=mcp)


def _table(resolved: str, document: dict, name: str, prefix: str = "") -> dict:
    """`[<prefix><name>]` as a mapping, empty where the file has none, `ConfigError` where it is not
    a table."""
    table = document.get(name) or {}
    if not isinstance(table, dict):
        raise ConfigError(f"{resolved}: [{prefix}{name}] is not a table")
    return table


def load(path: str | None = None) -> Config:
    """Read and check the configuration. Raises `ConfigError` with the key at fault."""
    resolved = os.path.expanduser(
        path or os.environ.get("EXERIS_AGENT_CONFIG") or DEFAULT_PATH)
    try:
        with open(resolved, "rb") as handle:
            document = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"no configuration at {resolved} — the execution identity is "
                          f"unregistered on this machine") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{resolved} could not be read: {exc}") from None

    github = document.get("github") or {}
    if not isinstance(github, dict):
        raise ConfigError(f"{resolved}: [github] is not a table")

    missing = [key for key in _REQUIRED if _scalar(document, github, key) in (None, "")]
    if missing:
        raise ConfigError(f"{resolved}: [github] is missing {', '.join(missing)}")

    repos = {name: _repo(resolved, name, table)
             for name, table in (document.get("repos") or {}).items()}

    oracle_table, registry_table = _table(resolved, document, "oracle"), \
        _table(resolved, document, "registry")
    pull_request_table = _table(resolved, document, "pull_request")
    for key, value in (("[oracle].bridge", oracle_table.get("bridge")),
                       ("[registry].path", registry_table.get("path")),
                       ("[pull_request].body_check", pull_request_table.get("body_check"))):
        if value is not None and (not isinstance(value, str) or not value):
            raise ConfigError(f"{resolved}: {key} is not a path")
    declared = _table(resolved, document, "providers")
    providers = {name: dict(_table(resolved, declared, name, "providers.")) for name in declared}

    try:
        return Config(
            path=resolved,
            client_id=str(_scalar(document, github, "client_id")),
            installation_id=int(_scalar(document, github, "installation_id")),
            bot_user_id=int(_scalar(document, github, "bot_user_id")),
            private_key=str(_scalar(document, github, "private_key")),
            age_identity=_scalar(document, github, "age_identity"),
            bot_login=str(_scalar(document, github, "bot_login", DEFAULT_BOT_LOGIN)),
            owner_login=_scalar(document, github, "owner_login"),
            org=str(_scalar(document, github, "org", DEFAULT_ORG)),
            execution_repo=_scalar(document, github, "execution_repo"),
            streams_repo=_scalar(document, github, "streams_repo"),
            docs_index=oracle_table.get("docs_index"),
            bridge=oracle_table.get("bridge"),
            registry=registry_table.get("path"),
            body_check=pull_request_table.get("body_check"),
            repos=repos,
            providers=providers,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{resolved}: installation_id and bot_user_id must be integers "
                          f"({exc})") from None
