"""Spotify login with Authorization Code + PKCE. No client secret exists anywhere."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from spotifymix.paths import write_private

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = (
    "user-read-recently-played",
    "user-library-read",
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-modify-playback-state",
    "user-read-private",  # needed by search with market=from_token
)
KEYRING_SERVICE = "spotifymix"
KEYRING_USER = "refresh_token"


class AuthError(Exception):
    pass


class LoginRequired(AuthError):
    pass


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_at: float
    refresh_token: str


def make_verifier() -> str:
    return secrets.token_urlsafe(96)[:128]


def make_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def build_authorize_url(client_id: str, redirect: str, challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _post_token(
    http: httpx.Client, data: dict[str, str], now: float, previous_refresh: str | None = None
) -> TokenSet:
    try:
        response = http.post(TOKEN_URL, data=data, timeout=15)
    except httpx.HTTPError as exc:
        raise AuthError(f"Spotify accounts service unreachable: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 400 and body.get("error") == "invalid_grant":
        raise LoginRequired("Spotify login expired or revoked; run `spotifymix login`")
    if response.status_code != 200 or "access_token" not in body:
        raise AuthError(f"Spotify token request failed (HTTP {response.status_code})")
    return TokenSet(
        access_token=body["access_token"],
        expires_at=now + float(body.get("expires_in", 3600)),
        refresh_token=body.get("refresh_token") or previous_refresh or "",
    )


def exchange_code(
    http: httpx.Client, client_id: str, code: str, redirect: str, verifier: str, now: float
) -> TokenSet:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    return _post_token(http, data, now)


def refresh_access(http: httpx.Client, client_id: str, refresh_token: str, now: float) -> TokenSet:
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return _post_token(http, data, now, previous_refresh=refresh_token)


class TokenStore:
    """Refresh token in the OS keyring; private file only when no keyring is available."""

    def __init__(self, fallback_file: Path, backend: Any = "default") -> None:
        if backend == "default":
            import keyring

            backend = keyring
        self._backend = backend
        self._file = fallback_file

    # Keyring backends raise many unrelated exception types, hence the broad catches.
    def load(self) -> str | None:
        if self._backend is not None:
            try:
                value = self._backend.get_password(KEYRING_SERVICE, KEYRING_USER)
                if value:
                    return value
            except Exception:
                pass
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8")).get("refresh_token")
            except (OSError, ValueError):
                return None
        return None

    def save(self, refresh_token: str) -> str:
        if self._backend is not None:
            try:
                self._backend.set_password(KEYRING_SERVICE, KEYRING_USER, refresh_token)
                self._file.unlink(missing_ok=True)
                return "keyring"
            except Exception:
                pass
        write_private(self._file, json.dumps({"refresh_token": refresh_token}))
        return "file"

    def clear(self) -> None:
        if self._backend is not None:
            try:
                self._backend.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass
        self._file.unlink(missing_ok=True)


_DONE_PAGE = b"<h1>SpotifyMix: login complete. You can close this tab.</h1>"
_FAILED_PAGE = b"<h1>SpotifyMix: login failed. Go back to the terminal.</h1>"


def login_interactive(
    http: httpx.Client,
    client_id: str,
    port: int,
    store: TokenStore,
    open_browser: Callable[[str], Any] = webbrowser.open,
    timeout: float = 300.0,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    verifier = make_verifier()
    state = secrets.token_urlsafe(16)
    redirect = redirect_uri(port)
    result: dict[str, str] = {}
    done = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
            ok = result.get("state") == state and "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE if ok else _FAILED_PAGE)
            done.set()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        open_browser(build_authorize_url(client_id, redirect, make_challenge(verifier), state))
        if not done.wait(timeout):
            raise AuthError("timed out waiting for the Spotify login")
    finally:
        server.shutdown()
        server.server_close()
    if result.get("state") != state:
        raise AuthError("state mismatch: login aborted")
    if "error" in result or "code" not in result:
        raise AuthError(f"Spotify refused the login: {result.get('error', 'no code')}")
    tokens = exchange_code(http, client_id, result["code"], redirect, verifier, clock())
    store.save(tokens.refresh_token)
    return tokens
