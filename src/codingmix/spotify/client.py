"""Thin Spotify Web API wrapper: token refresh, rate limits and error mapping."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from codingmix.models import Device, Play, Playback, SavedTrack, Track, parse_iso
from codingmix.spotify.auth import AuthError, LoginRequired, TokenStore, refresh_access

API_BASE = "https://api.spotify.com/v1"


class SpotifyError(Exception):
    pass


class RateLimited(SpotifyError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Spotify rate limit: retry in {retry_after:.0f}s")
        self.retry_after = retry_after


class SpotifyUnavailable(SpotifyError):
    pass


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("Retry-After", "30"))
    except ValueError:
        return 30.0


class SpotifyClient:
    def __init__(
        self,
        http: httpx.Client,
        client_id: str,
        tokens: TokenStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._tokens = tokens
        self._clock = clock
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _token(self, force: bool = False) -> str:
        with self._lock:
            now = self._clock()
            if not force and self._access and now < self._expires_at - 60:
                return self._access
            refresh = self._tokens.load()
            if not refresh:
                raise LoginRequired("no Spotify login stored; run `codingmix login`")
            try:
                tokens = refresh_access(self._http, self._client_id, refresh, now)
            except LoginRequired:
                raise
            except AuthError as exc:
                raise SpotifyUnavailable(str(exc)) from exc
            if tokens.refresh_token and tokens.refresh_token != refresh:
                self._tokens.save(tokens.refresh_token)
            self._access, self._expires_at = tokens.access_token, tokens.expires_at
            return self._access

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in (1, 2):
            token = self._token(force=attempt == 2)
            try:
                response = self._http.request(
                    method, API_BASE + path, params=params,
                    headers={"Authorization": f"Bearer {token}"}, timeout=15,
                )
            except httpx.HTTPError as exc:
                raise SpotifyUnavailable(f"Spotify unreachable: {exc}") from exc
            if response.status_code == 401 and attempt == 1:
                continue
            if response.status_code == 429:
                raise RateLimited(_retry_after(response))
            if response.status_code >= 500:
                raise SpotifyUnavailable(f"Spotify returned HTTP {response.status_code}")
            if response.status_code >= 400:
                raise SpotifyError(f"Spotify returned HTTP {response.status_code} for {path}")
            # Write calls return no data we use, and their bodies are not always
            # JSON (the queue call answers 200 with a plain text id).
            if method != "GET" or response.status_code in (202, 204) or not response.content:
                return None
            return response.json()
        raise LoginRequired("Spotify rejected a fresh access token; run `codingmix login`")

    def get_playback(self) -> Playback | None:
        data = self._request("GET", "/me/player", {"additional_types": "episode"})
        return Playback.from_json(data) if data else None

    def devices(self) -> list[Device]:
        data = self._request("GET", "/me/player/devices") or {}
        return [
            Device(d.get("id"), d.get("name") or "", d.get("type") or "", bool(d.get("is_active")))
            for d in data.get("devices") or []
        ]

    def add_to_queue(self, uri: str, device_id: str | None = None) -> None:
        params = {"uri": uri}
        if device_id:
            params["device_id"] = device_id
        self._request("POST", "/me/player/queue", params)

    def search_tracks(
        self, query: str, offset: int = 0, limit: int = 10
    ) -> tuple[list[Track], int]:
        params = {"q": query, "type": "track", "limit": limit, "offset": offset,
                  "market": "from_token"}
        block = (self._request("GET", "/search", params) or {}).get("tracks") or {}
        tracks = []
        for raw in block.get("items") or []:
            if raw and raw.get("is_playable", True):
                track = Track.from_json(raw)
                if track is not None:
                    tracks.append(track)
        return tracks, int(block.get("total") or 0)

    def recently_played(self, limit: int = 50) -> list[Play]:
        data = self._request("GET", "/me/player/recently-played", {"limit": limit}) or {}
        plays = []
        for item in data.get("items") or []:
            track = Track.from_json(item.get("track"))
            if track is not None and item.get("played_at"):
                plays.append(Play(track, parse_iso(item["played_at"])))
        return plays

    def saved_tracks(self, offset: int = 0, limit: int = 50) -> tuple[list[SavedTrack], bool]:
        data = self._request("GET", "/me/tracks", {"offset": offset, "limit": limit}) or {}
        saved = []
        for item in data.get("items") or []:
            track = Track.from_json(item.get("track"))
            if track is not None and item.get("added_at"):
                saved.append(SavedTrack(track, parse_iso(item["added_at"])))
        return saved, bool(data.get("next"))
