"""Keeps the local 7-day history in sync with Spotify."""

from __future__ import annotations

import logging
from collections.abc import Callable

from spotifymix.spotify.auth import AuthError
from spotifymix.spotify.client import SpotifyError
from spotifymix.store import DAY, Store

log = logging.getLogger(__name__)


class Recorder:
    def __init__(
        self, client, store: Store, recent_every: float, saved_every: float, exclusion_days: int
    ) -> None:
        self._client = client
        self._store = store
        self._recent_every = recent_every
        self._saved_every = saved_every
        self._days = exclusion_days
        self._next_recent = 0.0
        self._next_saved = 0.0
        self.last_error: str | None = None

    def maybe_run(self, now: float) -> None:
        if now >= self._next_recent:
            self._next_recent = now + self._recent_every
            self._run(self.sync_recent, now)
        if now >= self._next_saved:
            self._next_saved = now + self._saved_every
            self._run(self.sync_saved, now)

    def _run(self, job: Callable[[float], int], now: float) -> None:
        try:
            job(now)
            self.last_error = None
        except (SpotifyError, AuthError) as exc:
            self.last_error = str(exc)
            log.warning("history sync failed: %s", exc)

    def sync_recent(self, now: float) -> int:
        return self._store.add_plays(self._client.recently_played(limit=50))

    def sync_saved(self, now: float, max_pages: int = 20) -> int:
        cutoff = now - self._days * DAY
        offset = 0
        stored = 0
        for _ in range(max_pages):
            items, has_next = self._client.saved_tracks(offset=offset, limit=50)
            fresh = [s for s in items if s.added_at >= cutoff]
            self._store.upsert_saved(fresh)
            stored += len(fresh)
            if len(fresh) < len(items) or not has_next:
                break
            offset += 50
        return stored
