"""Spotify data shapes used across the service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_BRACKETS = re.compile(r"[\(\[].*?[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+")
_NON_WORD = re.compile(r"[^\w\s]")


def _clean(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


def make_name_key(name: str, artist: str) -> str:
    """Identity of a song across editions: 'Song - Remastered' == 'Song (Live)' == 'Song'."""
    title = _BRACKETS.sub(" ", name.lower())
    title = _DASH_SUFFIX.split(title)[0]
    return f"{_clean(title)}|{_clean(artist)}"


def parse_iso(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


@dataclass(frozen=True)
class Track:
    id: str
    uri: str
    name: str
    artists: tuple[str, ...]
    duration_ms: int

    @property
    def name_key(self) -> str:
        return make_name_key(self.name, self.artists[0] if self.artists else "")

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> Track | None:
        if not data or data.get("type", "track") != "track" or not data.get("id"):
            return None
        track_id = data["id"]
        return cls(
            id=track_id,
            uri=data.get("uri") or f"spotify:track:{track_id}",
            name=data.get("name") or "",
            artists=tuple(a.get("name") or "" for a in data.get("artists") or []),
            duration_ms=int(data.get("duration_ms") or 0),
        )


@dataclass(frozen=True)
class Playback:
    is_playing: bool
    device_id: str | None
    device_name: str
    device_type: str
    item: Track | None
    progress_ms: int
    context_uri: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Playback:
        device = data.get("device") or {}
        is_track = data.get("currently_playing_type", "track") == "track"
        return cls(
            is_playing=bool(data.get("is_playing")),
            device_id=device.get("id"),
            device_name=device.get("name") or "",
            device_type=device.get("type") or "",
            item=Track.from_json(data.get("item")) if is_track else None,
            progress_ms=int(data.get("progress_ms") or 0),
            context_uri=(data.get("context") or {}).get("uri"),
        )


@dataclass(frozen=True)
class Play:
    track: Track
    played_at: int


@dataclass(frozen=True)
class SavedTrack:
    track: Track
    added_at: int


@dataclass(frozen=True)
class Device:
    id: str | None
    name: str
    type: str
    is_active: bool
