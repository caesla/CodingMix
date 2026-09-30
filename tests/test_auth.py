import re
import threading
import urllib.request
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fakes import FakeKeyring, free_port

from codingmix.spotify import auth


def token_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pkce_challenge_matches_rfc7636_example():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert auth.make_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_verifier_length_and_charset():
    verifier = auth.make_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)


def test_authorize_url_contains_pkce_and_scopes():
    url = auth.build_authorize_url("cid", auth.redirect_uri(47616), "chal", "st")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["cid"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == ["chal"]
    assert query["state"] == ["st"]
    assert query["redirect_uri"] == ["http://127.0.0.1:47616/callback"]
    assert set(auth.SCOPES) == set(query["scope"][0].split())


def test_refresh_keeps_old_refresh_token_when_not_rotated():
    seen = {}

    def handler(request):
        seen.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "A", "expires_in": 3600})

    tokens = auth.refresh_access(token_client(handler), "cid", "R1", now=100)
    assert tokens == auth.TokenSet("A", 3700, "R1")
    assert seen["grant_type"] == ["refresh_token"]
    assert seen["client_id"] == ["cid"]
    assert "client_secret" not in seen


def test_refresh_returns_rotated_token():
    client = token_client(lambda r: httpx.Response(
        200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R2"}))
    assert auth.refresh_access(client, "cid", "R1", now=0).refresh_token == "R2"


def test_invalid_grant_raises_login_required():
    client = token_client(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(auth.LoginRequired):
        auth.refresh_access(client, "cid", "R1", now=0)


def test_token_endpoint_down_raises_auth_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(auth.AuthError) as info:
        auth.refresh_access(token_client(handler), "cid", "R1", now=0)
    assert not isinstance(info.value, auth.LoginRequired)


def test_token_store_prefers_keyring(tmp_path):
    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring())
    assert store.save("R") == "keyring"
    assert store.load() == "R"
    assert not (tmp_path / "t.json").exists()


def test_token_store_falls_back_to_file(tmp_path):
    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring(broken=True))
    assert store.save("R") == "file"
    assert store.load() == "R"
    store.clear()
    assert store.load() is None


def _browser_that_calls_back(port, code="the-code", state=None):
    def open_browser(url):
        real_state = parse_qs(urlparse(url).query)["state"][0]
        target = (f"http://127.0.0.1:{port}/callback?code={code}"
                  f"&state={state or real_state}")
        threading.Thread(
            target=lambda: urllib.request.urlopen(target, timeout=5).read(), daemon=True
        ).start()
        return True

    return open_browser


def test_interactive_login_saves_refresh_token(tmp_path):
    port = free_port()

    def handler(request):
        body = parse_qs(request.content.decode())
        assert body["code"] == ["the-code"]
        assert body["code_verifier"][0]
        assert body["redirect_uri"] == [f"http://127.0.0.1:{port}/callback"]
        return httpx.Response(
            200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R"})

    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring())
    tokens = auth.login_interactive(
        token_client(handler), "cid", port, store,
        open_browser=_browser_that_calls_back(port), timeout=10,
    )
    assert tokens.refresh_token == "R"
    assert store.load() == "R"


def test_interactive_login_rejects_wrong_state(tmp_path):
    port = free_port()
    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring())
    with pytest.raises(auth.AuthError, match="state"):
        auth.login_interactive(
            token_client(lambda r: httpx.Response(500)), "cid", port, store,
            open_browser=_browser_that_calls_back(port, state="forged"), timeout=10,
        )


def test_scopes_allow_market_from_token():
    # Search with market=from_token returns 403 "Insufficient client scope" without it.
    assert "user-read-private" in auth.SCOPES
