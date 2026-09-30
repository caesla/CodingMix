"""Test doubles shared by several test modules. Never touches network or OS keyring."""

from __future__ import annotations

import re
import socket

import httpx

from spotifymix import paths
from spotifymix.models import Playback, Track
from spotifymix.spotify.auth import TokenStore


class FakeKeyring:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self.broken = broken

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError("no keyring backend available")

    def get_password(self, service: str, user: str) -> str | None:
        self._check()
        return self.data.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self._check()
        self.data[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self._check()
        self.data.pop((service, user), None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def track_json(track_id="t1", name=None, artist=None, **extra):
    data = {
        "type": "track", "id": track_id, "uri": f"spotify:track:{track_id}",
        "name": name or f"Song {track_id}", "artists": [{"name": artist or f"Artist {track_id}"}],
        "duration_ms": 200000,
    }
    data.update(extra)
    return data


class FakeApi:
    """httpx MockTransport handler emulating the Spotify endpoints SpotifyMix uses."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[httpx.Request] = []
        self.token_responses: list = [
            httpx.Response(200, json={"access_token": "A1", "expires_in": 3600})
        ]

    def on(self, method: str, path: str, *responses) -> None:
        self.routes[(method, path)] = list(responses)

    @staticmethod
    def _next(queue: list):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "accounts.spotify.com":
            return self._next(self.token_responses)
        queue = self.routes.get((request.method, request.url.path.removeprefix("/v1")))
        if not queue:
            return httpx.Response(404, json={"error": "no fake route"})
        return self._next(queue)

    def token_calls(self) -> list[httpx.Request]:
        return [c for c in self.calls if c.url.host == "accounts.spotify.com"]

    def api_calls(self, path: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.url.path == "/v1" + path]


def make_client(api: FakeApi, refresh: str | None = "R1", clock=lambda: 1000.0):
    from spotifymix.spotify.client import SpotifyClient

    tokens = TokenStore(paths.token_fallback_file(), backend=FakeKeyring())
    if refresh:
        tokens.save(refresh)
    http = httpx.Client(transport=httpx.MockTransport(api))
    return SpotifyClient(http, "cid", tokens, clock=clock), tokens


def make_track(i: int, prefix: str = "t") -> Track:
    track_id = f"{prefix}{i}"
    return Track(track_id, f"spotify:track:{track_id}", f"Song {track_id}",
                 (f"Artist {track_id}",), 200000)


def playing(track, progress_ms, *, is_playing=True, device_type="Computer",
            device_name="MY-PC", context=None) -> Playback:
    return Playback(is_playing, "dev1", device_name, device_type, track, progress_ms, context)


class FakeSpotify:
    """In-memory stand-in for SpotifyClient."""

    def __init__(self) -> None:
        self.playback: Playback | None = None
        self.queued: list[tuple[str, str | None]] = []
        self.search_pool: dict[str, list[Track]] = {}
        self.search_calls: list[tuple[str, int, int]] = []
        self.recent: list = []
        self.recent_calls = 0
        self.saved_pages: list[tuple[list, bool]] = []
        self.saved_calls: list[int] = []
        self.error: Exception | None = None
        self.queue_error: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    def get_playback(self):
        self._maybe_fail()
        return self.playback

    def devices(self):
        self._maybe_fail()
        return []

    def add_to_queue(self, uri, device_id=None):
        self._maybe_fail()
        if self.queue_error is not None:
            raise self.queue_error
        self.queued.append((uri, device_id))

    def search_tracks(self, query, offset=0, limit=10):
        self._maybe_fail()
        self.search_calls.append((query, offset, limit))
        genre = re.search(r'genre:"([^"]+)"', query).group(1)
        pool = self.search_pool.get(genre, [])
        return pool[offset:offset + limit], len(pool)

    def recently_played(self, limit=50):
        self._maybe_fail()
        self.recent_calls += 1
        return list(self.recent[:limit])

    def saved_tracks(self, offset=0, limit=50):
        self._maybe_fail()
        self.saved_calls.append(offset)
        index = offset // limit
        return self.saved_pages[index] if index < len(self.saved_pages) else ([], False)


class FakeModes:
    def __init__(self, mode):
        self.mode = mode

    def stable_mode(self, now):
        return self.mode


class FakeFinder:
    def __init__(self):
        self.pools: dict[str, list[Track]] = {}
        self.calls = 0
        self.last_note = None

    def next_track(self, mode, now):
        self.calls += 1
        pool = self.pools.get(mode, [])
        return pool.pop(0) if pool else None
