import httpx
import pytest
from fakes import FakeApi, make_client, track_json

from codingmix.spotify.auth import LoginRequired
from codingmix.spotify.client import RateLimited, SpotifyUnavailable


def test_access_token_is_refreshed_once_and_reused():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(204))
    client, _ = make_client(api)
    assert client.get_playback() is None
    assert client.get_playback() is None
    assert len(api.token_calls()) == 1


def test_expired_token_is_refreshed_again():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(204))
    now = [1000.0]
    client, _ = make_client(api, clock=lambda: now[0])
    client.get_playback()
    now[0] += 3600
    client.get_playback()
    assert len(api.token_calls()) == 2


def test_401_triggers_refresh_and_retry():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(401), httpx.Response(204))
    client, _ = make_client(api)
    assert client.get_playback() is None
    assert len(api.token_calls()) == 2
    assert len(api.api_calls("/me/player")) == 2


def test_429_raises_rate_limited():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(429, headers={"Retry-After": "12"}))
    client, _ = make_client(api)
    with pytest.raises(RateLimited) as info:
        client.get_playback()
    assert info.value.retry_after == 12


def test_429_with_unreadable_retry_after_defaults_to_30():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(429, headers={"Retry-After": "soon"}))
    client, _ = make_client(api)
    with pytest.raises(RateLimited) as info:
        client.get_playback()
    assert info.value.retry_after == 30


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.ConnectError("offline")])
def test_server_and_network_errors_raise_unavailable(response):
    api = FakeApi()
    api.on("GET", "/me/player", response)
    client, _ = make_client(api)
    with pytest.raises(SpotifyUnavailable):
        client.get_playback()


def test_missing_login_raises_login_required():
    client, _ = make_client(FakeApi(), refresh=None)
    with pytest.raises(LoginRequired):
        client.get_playback()


def test_invalid_grant_raises_login_required():
    api = FakeApi()
    api.token_responses = [httpx.Response(400, json={"error": "invalid_grant"})]
    client, _ = make_client(api)
    with pytest.raises(LoginRequired):
        client.get_playback()


def test_rotated_refresh_token_is_saved():
    api = FakeApi()
    api.token_responses = [httpx.Response(
        200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R2"})]
    api.on("GET", "/me/player", httpx.Response(204))
    client, tokens = make_client(api)
    client.get_playback()
    assert tokens.load() == "R2"


def test_get_playback_parses_track():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(200, json={
        "is_playing": True, "progress_ms": 5000, "currently_playing_type": "track",
        "device": {"id": "d1", "name": "MY-PC", "type": "Computer"}, "item": track_json("x"),
    }))
    client, _ = make_client(api)
    playback = client.get_playback()
    assert playback.item.id == "x" and playback.device_name == "MY-PC"
    assert api.api_calls("/me/player")[0].headers["Authorization"] == "Bearer A1"


def test_search_filters_unplayable_and_returns_total():
    api = FakeApi()
    api.on("GET", "/search", httpx.Response(200, json={"tracks": {
        "total": 321,
        "items": [track_json("ok"), track_json("blocked", is_playable=False), None],
    }}))
    client, _ = make_client(api)
    tracks, total = client.search_tracks('genre:"techno"', offset=20)
    assert [t.id for t in tracks] == ["ok"]
    assert total == 321
    params = api.api_calls("/search")[0].url.params
    assert params["q"] == 'genre:"techno"'
    assert params["type"] == "track"
    assert params["limit"] == "10"
    assert params["offset"] == "20"
    assert params["market"] == "from_token"


def test_recently_played_and_saved_tracks_parse():
    api = FakeApi()
    api.on("GET", "/me/player/recently-played", httpx.Response(200, json={
        "items": [{"track": track_json("a"), "played_at": "1970-01-01T00:01:00Z"}]}))
    api.on("GET", "/me/tracks", httpx.Response(200, json={
        "items": [{"track": track_json("s"), "added_at": "1970-01-01T00:02:00Z"}],
        "next": "https://api.spotify.com/v1/me/tracks?offset=50"}))
    client, _ = make_client(api)
    plays = client.recently_played()
    assert plays[0].track.id == "a" and plays[0].played_at == 60
    saved, has_next = client.saved_tracks()
    assert saved[0].added_at == 120 and has_next is True


def test_add_to_queue_sends_uri_and_device():
    api = FakeApi()
    api.on("POST", "/me/player/queue", httpx.Response(204))
    client, _ = make_client(api)
    client.add_to_queue("spotify:track:x", "d1")
    params = api.api_calls("/me/player/queue")[0].url.params
    assert params["uri"] == "spotify:track:x" and params["device_id"] == "d1"


def test_add_to_queue_accepts_the_real_200_with_a_plain_text_body():
    # Documented as 204, but on 2026-09-30 Spotify answered 200 with a
    # 27 byte opaque id, no content type.
    api = FakeApi()
    api.on("POST", "/me/player/queue", httpx.Response(200, content=b"x" * 27))
    client, _ = make_client(api)
    assert client.add_to_queue("spotify:track:x") is None


def test_devices():
    api = FakeApi()
    api.on("GET", "/me/player/devices", httpx.Response(200, json={"devices": [
        {"id": "d", "name": "MY-PC", "type": "Computer", "is_active": True}]}))
    client, _ = make_client(api)
    assert client.devices()[0].name == "MY-PC"
