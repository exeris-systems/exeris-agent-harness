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

    [repos.<name>]
    path            = "…"        # the local clone, when it is not beside this checkout
    domain          = "…"        # the oracle domain of the work done there
    scope           = […]        # the vocabulary `--scope` is checked against
    routine         = "…"        # the routine file a run there follows, hashed into the record

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

Scalars are read from `[github]` first and from the document root second, so a file that grew the
added fields at the top level is understood the same way as one that put them beside the App's own.

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

    repos = {}
    for name, table in (document.get("repos") or {}).items():
        if not isinstance(table, dict):
            raise ConfigError(f"{resolved}: [repos.{name}] is not a table")
        scope = table.get("scope") or ()
        if isinstance(scope, str):
            scope = (scope,)
        repos[name] = Repo(name=name,
                           path=table.get("path"),
                           domain=table.get("domain"),
                           scope=tuple(scope),
                           routine=table.get("routine"))

    providers = {}
    for name, table in (document.get("providers") or {}).items():
        if not isinstance(table, dict):
            raise ConfigError(f"{resolved}: [providers.{name}] is not a table")
        providers[name] = dict(table)

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
            repos=repos,
            providers=providers,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{resolved}: installation_id and bot_user_id must be integers "
                          f"({exc})") from None
