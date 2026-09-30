"""Turns Claude Code hook payloads into a stable activity mode."""

from __future__ import annotations

import fnmatch
import re
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spotifymix.config import REGEX_KEYS, Rule


@dataclass(frozen=True)
class Vote:
    ts: float
    mode: str
    weight: float


def _field(payload: dict[str, Any], name: str) -> str:
    tool_input = payload.get("tool_input")
    ti = tool_input if isinstance(tool_input, dict) else {}
    if name == "path":
        return str(ti.get("file_path") or ti.get("notebook_path") or "")
    if name in ("agent_type", "prompt"):
        return str(payload.get(name) or "")
    return str(ti.get(name) or "")


class Classifier:
    def __init__(
        self, rules: Sequence[Rule], window_seconds: float, switch_after_seconds: float
    ) -> None:
        self._rules = [
            (rule, {k: re.compile(getattr(rule, k), re.IGNORECASE)
                    for k in REGEX_KEYS if getattr(rule, k) is not None})
            for rule in rules
        ]
        self._window = window_seconds
        self._switch_after = switch_after_seconds
        self._votes: deque[Vote] = deque()
        self._stable: str | None = None
        self._candidate: str | None = None
        self._candidate_since: float | None = None
        self._manual: str | None = None
        self._manual_until = 0.0
        self._lock = threading.Lock()

    def classify(self, payload: dict[str, Any]) -> tuple[str, float] | None:
        for rule, regexes in self._rules:
            if self._matches(rule, regexes, payload):
                return rule.mode, rule.weight
        return None

    @staticmethod
    def _matches(rule: Rule, regexes: dict[str, re.Pattern[str]], payload: dict[str, Any]) -> bool:
        if rule.events and payload.get("hook_event_name") not in rule.events:
            return False
        if rule.tools:
            tool = str(payload.get("tool_name") or "")
            if not any(fnmatch.fnmatchcase(tool, pattern) for pattern in rule.tools):
                return False
        if rule.permission_mode and payload.get("permission_mode") != rule.permission_mode:
            return False
        return all(rx.search(_field(payload, name)) for name, rx in regexes.items())

    def observe(self, payload: dict[str, Any], now: float) -> str | None:
        result = self.classify(payload)
        if result is None:
            return None
        mode, weight = result
        with self._lock:
            self._votes.append(Vote(now, mode, weight))
        self.stable_mode(now)
        return mode

    def _prune(self, now: float) -> None:
        while self._votes and self._votes[0].ts < now - self._window:
            self._votes.popleft()

    def _leader_locked(self, now: float) -> str | None:
        self._prune(now)
        totals: dict[str, float] = {}
        latest: dict[str, float] = {}
        for vote in self._votes:
            totals[vote.mode] = totals.get(vote.mode, 0.0) + vote.weight
            latest[vote.mode] = vote.ts
        if not totals:
            return None
        return max(totals, key=lambda m: (totals[m], latest[m]))

    def leader(self, now: float) -> str | None:
        with self._lock:
            return self._leader_locked(now)

    def stable_mode(self, now: float) -> str | None:
        with self._lock:
            if self._manual is not None and now < self._manual_until:
                return self._manual
            lead = self._leader_locked(now)
            if lead is None or lead == self._stable:
                self._candidate = None
                self._candidate_since = None
            elif self._stable is None:
                self._stable = lead
            elif self._candidate != lead:
                self._candidate = lead
                self._candidate_since = now
            elif now - (self._candidate_since or now) >= self._switch_after:
                self._stable = lead
                self._candidate = None
                self._candidate_since = None
            return self._stable

    def set_manual(self, mode: str, until: float) -> None:
        with self._lock:
            self._manual = mode
            self._manual_until = until

    def clear_manual(self) -> None:
        with self._lock:
            self._manual = None
            self._manual_until = 0.0

    def snapshot(self, now: float) -> dict[str, Any]:
        stable = self.stable_mode(now)
        with self._lock:
            manual_active = self._manual is not None and now < self._manual_until
            return {
                "stable": stable,
                "leader": self._leader_locked(now),
                "candidate": self._candidate,
                "candidate_since": self._candidate_since,
                "manual": self._manual if manual_active else None,
                "manual_until": self._manual_until if manual_active else None,
            }
