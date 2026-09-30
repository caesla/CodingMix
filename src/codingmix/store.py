"""SQLite store: 7-day history of plays, saved tracks and proposals."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from codingmix.models import Play, SavedTrack, Track

DAY = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
    track_id TEXT NOT NULL, played_at INTEGER NOT NULL, name_key TEXT NOT NULL,
    PRIMARY KEY (track_id, played_at));
CREATE TABLE IF NOT EXISTS saved (
    track_id TEXT PRIMARY KEY, added_at INTEGER NOT NULL, name_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposed (
    track_id TEXT NOT NULL, proposed_at INTEGER NOT NULL, name_key TEXT NOT NULL,
    mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (ts INTEGER NOT NULL, event TEXT NOT NULL, mode TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS plays_key ON plays(name_key);
CREATE INDEX IF NOT EXISTS saved_key ON saved(name_key);
CREATE INDEX IF NOT EXISTS proposed_id ON proposed(track_id);
CREATE INDEX IF NOT EXISTS proposed_key ON proposed(name_key);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _write(self, sql: str, rows: Iterable[tuple]) -> int:
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(sql, rows)
            self._conn.commit()
            return self._conn.total_changes - before

    def add_plays(self, plays: Iterable[Play]) -> int:
        rows = [(p.track.id, int(p.played_at), p.track.name_key) for p in plays]
        return self._write("INSERT OR IGNORE INTO plays VALUES (?, ?, ?)", rows)

    def upsert_saved(self, saved: Iterable[SavedTrack]) -> None:
        rows = [(s.track.id, int(s.added_at), s.track.name_key) for s in saved]
        self._write("INSERT OR REPLACE INTO saved VALUES (?, ?, ?)", rows)

    def add_proposed(self, track: Track, mode: str, ts: float) -> None:
        self._write(
            "INSERT INTO proposed VALUES (?, ?, ?, ?)",
            [(track.id, int(ts), track.name_key, mode)],
        )

    def is_excluded(self, track: Track, now: float, days: int) -> bool:
        cutoff = int(now - days * DAY)
        args = (cutoff, track.id, track.name_key)
        sql = (
            "SELECT 1 FROM plays WHERE played_at >= ? AND (track_id = ? OR name_key = ?) "
            "UNION ALL SELECT 1 FROM saved WHERE added_at >= ? AND (track_id = ? OR name_key = ?) "
            "UNION ALL SELECT 1 FROM proposed WHERE proposed_at >= ? "
            "AND (track_id = ? OR name_key = ?) LIMIT 1"
        )
        with self._lock:
            return self._conn.execute(sql, args * 3).fetchone() is not None

    def recent_proposed_ids(self, limit: int = 100) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_id FROM proposed ORDER BY proposed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return {r[0] for r in rows}

    def log_event(self, ts: float, event: str, mode: str | None) -> None:
        self._write("INSERT INTO events VALUES (?, ?, ?)", [(int(ts), event, mode)])

    def recent_events(self, limit: int = 10) -> list[tuple[int, str, str | None]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, event, mode FROM events ORDER BY ts DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [tuple(r) for r in rows]

    def counts(self, now: float, days: int) -> dict[str, int]:
        cutoff = int(now - days * DAY)
        with self._lock:
            plays = self._conn.execute(
                "SELECT COUNT(*) FROM plays WHERE played_at >= ?", (cutoff,)
            ).fetchone()[0]
            saved = self._conn.execute(
                "SELECT COUNT(*) FROM saved WHERE added_at >= ?", (cutoff,)
            ).fetchone()[0]
            proposed = self._conn.execute(
                "SELECT COUNT(*) FROM proposed WHERE proposed_at >= ?", (cutoff,)
            ).fetchone()[0]
        return {"plays": plays, "saved": saved, "proposed": proposed}

    def prune(self, now: float, keep_days: int = 8) -> None:
        cutoff = int(now - keep_days * DAY)
        with self._lock:
            self._conn.execute("DELETE FROM plays WHERE played_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM saved WHERE added_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM proposed WHERE proposed_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def get_kv(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self._write("INSERT OR REPLACE INTO kv VALUES (?, ?)", [(key, value)])
