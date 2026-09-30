"""Watches Spotify playback and queues the next track of the current mode."""

from __future__ import annotations

import logging
from typing import Protocol

from codingmix.models import Playback, Track
from codingmix.spotify.auth import AuthError, LoginRequired
from codingmix.spotify.client import RateLimited, SpotifyError
from codingmix.store import Store

log = logging.getLogger(__name__)

IDLE_SECONDS = 15.0
MAX_WAIT = 30.0
# Survives a restart, so the service does not cover the same track twice.
QUEUED_FOR_KEY = "director.queued_for"


class ModeSource(Protocol):
    def stable_mode(self, now: float) -> str | None: ...


class Director:
    def __init__(
        self,
        client,
        finder,
        store: Store,
        modes: ModeSource,
        device_name: str | None,
        lead_seconds: float,
    ) -> None:
        self._client = client
        self._finder = finder
        self._store = store
        self._modes = modes
        self._device_name = device_name
        self._lead = lead_seconds
        self._last_seen: str | None = None
        self._queued_for: str | None = store.get_kv(QUEUED_FOR_KEY)
        self._expected_next: str | None = None
        self._aside_mode: str | None = None
        self._paused_until = 0.0
        self._next_poll = 0.0
        self.last_action: str | None = None
        self.last_error: str | None = None

    @property
    def aside_mode(self) -> str | None:
        return self._aside_mode

    @property
    def paused_until(self) -> float | None:
        return self._paused_until or None

    def pause(self, until: float) -> None:
        self._paused_until = until

    def resume(self) -> None:
        self._paused_until = 0.0
        self._aside_mode = None
        self._next_poll = 0.0

    def clear_aside(self) -> None:
        self._aside_mode = None
        self._next_poll = 0.0

    def tick(self, now: float) -> float:
        mode = self._modes.stable_mode(now)
        if now < self._paused_until:
            return min(MAX_WAIT, self._paused_until - now)
        if now < self._next_poll:
            return self._next_poll - now
        delay = max(1.0, self._poll(now, mode))
        self._next_poll = now + delay
        return delay

    def _on_this_device(self, playback: Playback) -> bool:
        if playback.device_type.lower() != "computer":
            return False
        if not self._device_name:
            return True
        return playback.device_name.lower() == self._device_name.lower()

    def _failure_delay(self, exc: Exception, default: float) -> float:
        self.last_error = str(exc)
        if isinstance(exc, RateLimited):
            return max(exc.retry_after, 5.0)
        if isinstance(exc, LoginRequired):
            return 60.0
        return default

    def _poll(self, now: float, mode: str | None) -> float:
        try:
            playback = self._client.get_playback()
        except (SpotifyError, AuthError) as exc:
            return self._failure_delay(exc, MAX_WAIT)
        if playback is None or not playback.is_playing or playback.item is None:
            return IDLE_SECONDS
        if not self._on_this_device(playback):
            return MAX_WAIT
        track_id = playback.item.id
        if track_id != self._last_seen:
            if self._expected_next is not None and track_id != self._expected_next:
                self._aside_mode = mode
                self.last_action = "stepped aside: you picked your own music"
            self._expected_next = None
            self._last_seen = track_id
        if self._aside_mode is not None:
            if mode is None or mode == self._aside_mode:
                return MAX_WAIT
            self._aside_mode = None
        if mode is None:
            return IDLE_SECONDS
        remaining = max(0.0, (playback.item.duration_ms - playback.progress_ms) / 1000)
        if remaining > self._lead:
            return min(MAX_WAIT, remaining - self._lead)
        if self._queued_for == track_id:
            return remaining + 1
        return self._queue_next(now, mode, playback, track_id, remaining)

    def _queue_next(
        self, now: float, mode: str, playback: Playback, track_id: str, remaining: float
    ) -> float:
        try:
            track = self._finder.next_track(mode, now)
        except (SpotifyError, AuthError) as exc:
            return self._failure_delay(exc, 10.0)
        if track is None:
            self.last_error = self._finder.last_note or f"no fresh track found for {mode}"
            self._cover(track_id)
            return remaining + 1
        try:
            self._client.add_to_queue(track.uri, playback.device_id)
        except (SpotifyError, AuthError) as exc:
            return self._failure_delay(exc, 10.0)
        except Exception as exc:
            # Unknown outcome: the track may be in the queue already. Skipping
            # one track is better than queueing it twice, and recording it keeps
            # it out of the next seven days.
            log.exception("queueing failed with an unexpected error; not retrying")
            self._mark_queued(track_id, track, mode, now)
            self.last_error = f"queueing may have failed: {exc}"
            return remaining + 1
        self._mark_queued(track_id, track, mode, now)
        self.last_action = f"queued {track.id} {track.name} by {', '.join(track.artists)} ({mode})"
        self.last_error = None
        log.info("queued a %s track", mode)
        return remaining + 1

    def _mark_queued(self, track_id: str, track: Track, mode: str, now: float) -> None:
        # State first, so a failure while recording cannot trigger a second queue.
        self._cover(track_id)
        self._expected_next = track.id
        self._store.add_proposed(track, mode, now)

    def _cover(self, track_id: str) -> None:
        self._queued_for = track_id
        self._store.set_kv(QUEUED_FOR_KEY, track_id)
