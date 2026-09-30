"""Finds tracks of a genre that were not heard, saved or proposed in the last days."""

from __future__ import annotations

import random
from collections import deque
from datetime import UTC, datetime

from spotifymix.config import ModeConfig
from spotifymix.models import Track
from spotifymix.store import Store

MAX_OFFSET = 1000


class Finder:
    def __init__(
        self,
        client,
        store: Store,
        modes: dict[str, ModeConfig],
        exclusion_days: int,
        rng: random.Random | None = None,
        buffer_size: int = 10,
        refill_below: int = 3,
        max_queries: int = 6,
        page_size: int = 10,
    ) -> None:
        self._client = client
        self._store = store
        self._modes = modes
        self._days = exclusion_days
        self._rng = rng or random.Random()
        self._size = buffer_size
        self._refill_below = refill_below
        self._max_queries = max_queries
        self._page = page_size
        self._buffers: dict[str, deque[Track]] = {}
        self._totals: dict[str, int] = {}
        self.last_note: str | None = None

    def next_track(self, mode_id: str, now: float) -> Track | None:
        mode = self._modes.get(mode_id)
        if mode is None:
            self.last_note = f"unknown mode {mode_id!r}"
            return None
        buffer = self._buffers.setdefault(mode_id, deque())
        if len(buffer) < self._refill_below:
            self._refill(mode, buffer, now)
        while buffer:
            track = buffer.popleft()
            if not self._store.is_excluded(track, now, self._days):
                return track
        self.last_note = f"no fresh track found for {mode_id}"
        return None

    @staticmethod
    def _year_ranges(now: float) -> list[str | None]:
        year = datetime.fromtimestamp(now, tz=UTC).year
        return [
            f"{year - 2}-{year}", f"{year - 6}-{year - 3}", f"{year - 11}-{year - 7}",
            f"{year - 18}-{year - 12}", None,
        ]

    @staticmethod
    def _query(genre: str, years: str | None) -> str:
        return f'genre:"{genre}"' + (f" year:{years}" if years else "")

    def _refill(self, mode: ModeConfig, buffer: deque[Track], now: float) -> None:
        seen_ids = {t.id for t in buffer}
        seen_keys = {t.name_key for t in buffer}
        self.last_note = None
        for label, genres in (("primary", mode.genres), ("fallback", mode.fallback)):
            if not genres or len(buffer) >= self._refill_below:
                continue
            attempts = 0
            while len(buffer) < self._size and attempts < self._max_queries:
                attempts += 1
                genre = self._rng.choice(genres)
                query = self._query(genre, self._rng.choice(self._year_ranges(now)))
                known_total = self._totals.get(query)
                if known_total == 0:
                    continue
                if known_total is None:
                    offset = 0
                else:
                    offset = self._rng.randrange(
                        0, max(1, min(known_total, MAX_OFFSET) - self._page + 1))
                tracks, total = self._client.search_tracks(query, offset=offset, limit=self._page)
                self._totals[query] = total
                for track in tracks:
                    if track.id in seen_ids or track.name_key in seen_keys:
                        continue
                    if self._store.is_excluded(track, now, self._days):
                        continue
                    buffer.append(track)
                    seen_ids.add(track.id)
                    seen_keys.add(track.name_key)
            if label == "fallback" and buffer:
                self.last_note = f"used fallback genres for {mode.id}"
