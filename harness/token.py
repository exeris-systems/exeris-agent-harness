"""Minting the execution identity's own installation token.

Three properties the rest of the harness rests on.

*The key never reaches a run's disk and never reaches an argument list.* It is handed to `openssl`
on a pipe and named to it as `/dev/fd/<n>`, so the widest thing a process listing exposes is a
descriptor number. A run sees the hour-long installation token and nothing else.

*The signature is RS256 with no library.* `openssl dgst -sha256 -sign` on an RSA key emits exactly
the PKCS#1 v1.5 signature RS256 is defined as, so the JWT is three base64url segments and no
re-encoding step that could get the padding wrong.

*The App authenticates as itself.* The assertion goes out as `Authorization: Bearer`, which is why
this is `urllib` and not `gh api`: `gh` sends `token <value>`, and an App JWT presented that way is
rejected.

A run that cannot mint this token fails. It does not degrade to a person's account, a shared
secret, or any other identity to get the work done — the harness has no code path that could.

Key custody in V0: a plain PEM is accepted only at mode 0600 and owned by the invoking user, and a
`.age` path is decrypted against a configured identity file held to the same rule. The plain-PEM
branch is a relaxation of the encrypt-at-rest rule, stated as a trade-off in `policy/README.md`.
"""

import base64
import json
import os
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

from . import VERSION

API = "https://api.github.com"
USER_AGENT = f"exeris-agent-harness/{VERSION}"

#: GitHub accepts an App assertion whose lifetime is at most ten minutes, measured from `iat`. The
#: backdated `iat` absorbs clock skew between this machine and GitHub without spending that budget
#: twice: `exp - iat` is exactly the ten minutes allowed.
CLOCK_SKEW_SECONDS = 60
LIFETIME_SECONDS = 540

KEY_MODE = 0o600


class TokenError(Exception):
    """Minting failed. The run fails with it; nothing falls back to another identity."""


def _owner_only(path: str, what: str) -> None:
    """Refuse a secret on disk that anybody but the invoking user could read."""
    try:
        info = os.stat(path)
    except OSError as exc:
        raise TokenError(f"{what} is unreadable at {path}: {exc}") from None
    if info.st_uid != os.getuid():
        raise TokenError(f"{path} is owned by another user; {what} is the invoking user's or it "
                         f"is not used")
    if stat.S_IMODE(info.st_mode) != KEY_MODE:
        raise TokenError(f"{path} is mode {stat.S_IMODE(info.st_mode):04o}; {what} is accepted "
                         f"only at {KEY_MODE:04o}")


def _key_bytes(path: str, identity: str | None = None) -> bytes:
    """The private key in memory, under the custody rule its filename declares.

    An encrypted key is decrypted to a pipe against a configured identity. There is no passphrase
    branch: a passphrase is read from a terminal, and a mint invoked without one — from a
    dispatcher, or from `--launch` inside another process — would block on a question nobody can
    answer, so the absence of an identity is refused here rather than discovered there.
    """
    if path.endswith(".age"):
        if shutil.which("age") is None:
            raise TokenError(f"{path} is encrypted but `age` is not installed, so the key cannot "
                             f"be read")
        if not identity:
            raise TokenError(f"{path} is encrypted and no `age_identity` is configured; the "
                             f"harness decrypts against an identity file, never a passphrase "
                             f"typed at a terminal")
        _owner_only(identity, "the age identity")
        done = subprocess.run(["age", "--decrypt", "-i", identity, path],
                              stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if done.returncode != 0:
            raise TokenError(f"age could not decrypt {path}: "
                             f"{done.stderr.decode('utf-8', 'replace').strip()}")
        return done.stdout
    _owner_only(path, "an unencrypted private key")
    with open(path, "rb") as handle:
        return handle.read()


def _feed(write_fd: int, payload: bytes) -> None:
    """Push the key into the pipe and close it, in a thread, so a key larger than one pipe buffer
    cannot deadlock against a child that has not started reading yet."""
    try:
        with os.fdopen(write_fd, "wb") as pipe:
            pipe.write(payload)
    except OSError:
        pass


def _sign(key: bytes, signing_input: bytes) -> bytes:
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    writer = threading.Thread(target=_feed, args=(write_fd, key), daemon=True)
    writer.start()
    try:
        done = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{read_fd}"],
            input=signing_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(read_fd,))
    finally:
        os.close(read_fd)
        writer.join(timeout=5)
    if done.returncode != 0 or not done.stdout:
        raise TokenError("openssl refused to sign the assertion: "
                         + done.stderr.decode("utf-8", "replace").strip())
    return done.stdout


def _segment(payload: dict) -> bytes:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")).rstrip(b"=")


def assertion(cfg, *, now=None) -> str:
    """The App's JWT. `iss` is the client id — the App id also works and the client id is what the
    registration page shows, so it is what the configuration carries."""
    issued = int((now or time.time)())
    signing_input = _segment({"alg": "RS256", "typ": "JWT"}) + b"." + _segment({
        "iat": issued - CLOCK_SKEW_SECONDS,
        "exp": issued + LIFETIME_SECONDS,
        "iss": cfg.client_id,
    })
    signature = _sign(_key_bytes(cfg.key_path, cfg.age_identity_path), signing_input)
    return (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode("ascii")


def _post(url: str, headers: dict, body: bytes) -> dict:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise TokenError(f"GitHub refused the installation token ({exc.code}): {detail}") from None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise TokenError(f"the installation token request failed: {exc}") from None


def _delete(url: str, headers: dict) -> int:
    request = urllib.request.Request(url, headers=headers, method="DELETE")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status


def mint(cfg, *, http=None, now=None) -> dict:
    """An installation token for `cfg.installation_id`.

    `http` is a callable `(url, headers, body) -> dict` and `now` a callable returning epoch
    seconds, so a test can exercise the whole path — custody, signature, request shape — without a
    network and without a clock.
    """
    url = f"{API}/app/installations/{cfg.installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {assertion(cfg, now=now)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
        "Content-Length": "0",
    }
    body = (http or _post)(url, headers, b"")
    if not isinstance(body, dict) or not body.get("token"):
        raise TokenError("the installation token response carried no token")
    return {
        "token": body["token"],
        "expires_at": body.get("expires_at") or "",
        "permissions": body.get("permissions") or {},
    }


def revoke(minted: dict, *, http=None) -> bool:
    """Give an installation token back before its hour is up.

    A token outlives the thing it was minted for: an `open-run` that fails after the mint would
    otherwise leave an hour of valid credential behind with no run to spend it. Revocation is the
    mint's own responsibility, so that the one place that obtains a credential is the one place
    that ends it. It reports rather than raises — a revocation that failed must not replace the
    failure that called for it.

    The token authenticates its own revocation, so the header here is `token`, not the `Bearer`
    the App's assertion goes out under.
    """
    value = (minted or {}).get("token")
    if not value:
        return False
    headers = {
        "Authorization": f"token {value}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    try:
        status = (http or _delete)(f"{API}/installation/token", headers)
    except Exception:
        return False
    return status in (None, 204)


def write(run_dir: str, minted: dict) -> str:
    """`<run>/token`: the token on line one, its expiry on line two.

    One value per line rather than JSON, because the credential helper that reads it is a shell
    one-liner and a parser in that position is a dependency the run does not need. 0600 because the
    file is a bearer credential for as long as the hour lasts.
    """
    path = os.path.join(run_dir, "token")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, KEY_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{minted['token']}\n{minted.get('expires_at', '')}\n")
    return path
