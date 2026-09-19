"""Minting the execution identity's installation token.

The harness mints its own token because the identity that commits has to be the App and not the
person running the harness; a run that cannot mint fails rather than borrowing a human
credential. Two properties are what these cases exist for.

*The call is the App's own.* An installation token is issued to a JWT signed by the App's private
key and presented as a bearer credential — `gh` cannot make this call, because it does not send
that header. So the request is asserted whole: the endpoint, the header, the algorithm, the
issuer, the lifetime, and a signature that verifies against the public half of the key.

*The key leaves no trace.* The private key is the one secret on the machine whose loss is not
recoverable by rotation alone, and an agent runs as the same user. Two paths are checked here
because they are the two an agent can read without any privilege at all: the argument vector of
every process the mint spawns, which `/proc` publishes to the whole user, and any file the mint
leaves behind under the run's own directory.
"""

import base64
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CLIENT_ID = "Iv23liTESTclientid"
INSTALLATION_ID = 87654321
BOT_USER_ID = 330540144
BOT_LOGIN = "exeris-agent[bot]"
FAKE_TOKEN = "ghs_TESTtokenTESTtokenTESTtoken0123"
FAKE_EXPIRY = "2026-01-01T00:00:00Z"


class _Response(dict):
    """Accepts whichever shape the caller expects of an HTTP result.

    The mint owns how it speaks to the API; a test that pins that plumbing pins the wrong thing.
    This is a mapping, a file-like object and a context manager at once, so the assertions below
    are about the request that went out, never about how the answer was unwrapped.
    """

    def read(self):
        return json.dumps(dict(self)).encode()

    def json(self):
        return dict(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHttp:
    """Records the one App-token request instead of making it."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        url, headers, method, data = _dissect(args, kwargs)
        self.calls.append({"url": url, "headers": headers, "method": method, "data": data})
        return _Response(token=FAKE_TOKEN, expires_at=FAKE_EXPIRY)

    @property
    def only_call(self):
        assert len(self.calls) == 1, f"expected exactly one request, saw {len(self.calls)}"
        return self.calls[0]


def _dissect(args, kwargs):
    """Pull url/headers/method/body out of the call, however it was spelled."""
    request = args[0] if args else kwargs.get("request")
    if hasattr(request, "full_url") and hasattr(request, "header_items"):
        return (
            request.full_url,
            {k.lower(): v for k, v in request.header_items()},
            request.get_method(),
            request.data,
        )
    url = kwargs.get("url") if "url" in kwargs else (args[0] if args else None)
    headers = kwargs.get("headers")
    if headers is None:
        headers = next((a for a in args[1:] if isinstance(a, dict)), {})
    return (
        url,
        {str(k).lower(): v for k, v in dict(headers).items()},
        kwargs.get("method", "POST"),
        kwargs.get("data") if "data" in kwargs else kwargs.get("body"),
    )


def _b64url_decode(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _token_value(result):
    """The minted token, whether the mint returns a mapping, an object or the string itself."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("token") or result.get("access_token")
    return getattr(result, "token", None) or getattr(result, "access_token", None)


def _load_config(path):
    import harness.config as mod

    for name in ("load", "load_config", "read"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn(str(path))
    raise AssertionError("harness.config exposes no callable named load/load_config/read")


class _ArgvRecorder:
    """Every argument vector the mint hands to the kernel, delegating to the real call."""

    NAMES = ("run", "Popen", "call", "check_call", "check_output")

    def __init__(self):
        self.vectors = []
        self._saved = {}

    def __enter__(self):
        for name in self.NAMES:
            original = getattr(subprocess, name)
            self._saved[name] = original
            setattr(subprocess, name, self._spy(original))
        return self

    def __exit__(self, *exc):
        for name, original in self._saved.items():
            setattr(subprocess, name, original)
        return False

    def _spy(self, original):
        def spy(*args, **kwargs):
            vector = args[0] if args else kwargs.get("args")
            self.vectors.append(vector)
            return original(*args, **kwargs)

        return spy

    def flattened(self):
        out = []
        for vector in self.vectors:
            if vector is None:
                continue
            items = vector if isinstance(vector, (list, tuple)) else [vector]
            for item in items:
                out.append(item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item))
        return out


def _secret_fragments(pem_path):
    """The base64 body of the key, line by line — what a leak would actually look like."""
    text = pathlib.Path(pem_path).read_text()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body = [line for line in lines if not line.startswith("-----") and len(line) > 40]
    assert body, "the throwaway key has no base64 body to look for"
    return body


class TokenMintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="exeris-token-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.key_path = self.tmp / "throwaway.pem"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
             "-out", str(self.key_path)],
            check=True, capture_output=True,
        )
        self.key_path.chmod(0o600)
        self.public_key = self.tmp / "throwaway.pub.pem"
        subprocess.run(
            ["openssl", "rsa", "-in", str(self.key_path), "-pubout", "-out", str(self.public_key)],
            check=True, capture_output=True,
        )

        self.state_root = self.tmp / "state"
        self.state_root.mkdir()
        self.config_path = self.tmp / "config.toml"
        self.config_path.write_text(
            "[github]\n"
            f'client_id = "{CLIENT_ID}"\n'
            f"installation_id = {INSTALLATION_ID}\n"
            f"bot_user_id = {BOT_USER_ID}\n"
            f'bot_login = "{BOT_LOGIN}"\n'
            f'private_key = "{self.key_path}"\n'
            'owner_login = "a-maintainer"\n'
            'org = "exeris-systems"\n'
            'execution_repo = "exeris-systems/exeris-ai-execution"\n'
            'streams_repo = "exeris-systems/exeris-ai-execution-streams"\n'
        )
        # The real configuration directory is never read, written or consulted by a test. HOME
        # and the XDG roots move with it so that a default path cannot reach outside the tempdir.
        for name, value in {
            "EXERIS_AGENT_CONFIG": str(self.config_path),
            "EXERIS_AGENT_STATE": str(self.state_root),
            "HOME": str(self.tmp),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "XDG_STATE_HOME": str(self.tmp / "state-xdg"),
        }.items():
            self._set_env(name, value)

    def _set_env(self, name, value):
        previous = os.environ.get(name)
        os.environ[name] = value
        self.addCleanup(
            lambda n=name, p=previous: os.environ.__setitem__(n, p)
            if p is not None
            else os.environ.pop(n, None)
        )

    def _mint(self):
        import harness.token

        config = _load_config(self.config_path)
        http = _FakeHttp()
        with _ArgvRecorder() as recorder:
            result = harness.token.mint(config, http=http)
        return result, http, recorder

    def _bearer_jwt(self, http):
        authorization = http.only_call["headers"].get("authorization")
        self.assertIsNotNone(authorization, "the App-token request carries no Authorization header")
        self.assertTrue(
            authorization.startswith("Bearer "),
            f"the App authenticates with its own JWT, not {authorization.split()[0]!r}",
        )
        return authorization[len("Bearer "):]

    def test_the_call_is_the_installation_token_endpoint(self):
        result, http, _ = self._mint()
        call = http.only_call
        self.assertTrue(
            str(call["url"]).endswith(f"/app/installations/{INSTALLATION_ID}/access_tokens"),
            call["url"],
        )
        self.assertEqual("POST", str(call["method"]).upper())
        self.assertEqual(FAKE_TOKEN, _token_value(result))

    def test_the_authorization_header_is_a_bearer_jwt(self):
        _, http, _ = self._mint()
        jwt = self._bearer_jwt(http)
        self.assertEqual(3, len(jwt.split(".")), "a JWT is three dot-separated segments")

    def test_the_jwt_header_and_claims(self):
        _, http, _ = self._mint()
        header_segment, claims_segment, _signature = self._bearer_jwt(http).split(".")
        header = json.loads(_b64url_decode(header_segment))
        claims = json.loads(_b64url_decode(claims_segment))

        self.assertEqual("RS256", header.get("alg"))
        # The issuer is the App's *client id*. The app id is the other number GitHub shows on the
        # same page and is rejected here, where the failure is one assertion rather than a 401.
        self.assertEqual(CLIENT_ID, claims.get("iss"))
        self.assertEqual(600, claims["exp"] - claims["iat"], "GitHub caps an App JWT at ten minutes")
        self.assertLessEqual(abs(claims["iat"] - int(time.time())), 300, "iat is not now")

    def test_the_jwt_signature_verifies_against_the_public_key(self):
        _, http, _ = self._mint()
        header_segment, claims_segment, signature_segment = self._bearer_jwt(http).split(".")
        signing_input = self.tmp / "signing-input"
        signing_input.write_bytes(f"{header_segment}.{claims_segment}".encode())
        signature = self.tmp / "signature.bin"
        signature.write_bytes(_b64url_decode(signature_segment))

        verified = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(self.public_key),
             "-signature", str(signature), str(signing_input)],
            capture_output=True, text=True,
        )
        self.assertEqual(
            0, verified.returncode,
            f"the JWT is not signed by the configured key: {verified.stdout}{verified.stderr}",
        )

    def test_key_bytes_never_reach_a_subprocess_argv(self):
        _, _, recorder = self._mint()
        rendered = "\n".join(recorder.flattened())
        for fragment in _secret_fragments(self.key_path):
            self.assertNotIn(
                fragment, rendered,
                "the private key was passed as a command-line argument, where /proc publishes it "
                "to every process of this user",
            )
        self.assertTrue(recorder.vectors, "nothing was signed; the mint spawned no process")

    def test_key_bytes_never_land_under_the_run_directory(self):
        self._mint()
        fragments = _secret_fragments(self.key_path)
        for path in self.state_root.rglob("*"):
            if not path.is_file():
                continue
            blob = path.read_bytes().decode("utf-8", "replace")
            for fragment in fragments:
                self.assertNotIn(fragment, blob, f"the private key was copied into {path}")

    def test_a_key_readable_beyond_its_owner_is_refused(self):
        # V0 custody accepts a plain PEM, so the file mode is the whole of the protection: a key
        # any process of any other account can open is not the App's key any more. Refusal may
        # come from reading the configuration or from the mint; both are the same closed door.
        import harness.token

        self.key_path.chmod(0o644)
        self.assertEqual(0o644, stat.S_IMODE(self.key_path.stat().st_mode))
        with self.assertRaises(Exception):
            harness.token.mint(_load_config(self.config_path), http=_FakeHttp())


if __name__ == "__main__":
    unittest.main()
