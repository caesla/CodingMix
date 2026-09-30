"""The CodingMix background service: wiring, main loop and entry point."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler

from codingmix import paths
from codingmix.classifier import Classifier
from codingmix.config import Config, load_config
from codingmix.director import Director
from codingmix.finder import Finder
from codingmix.recorder import Recorder
from codingmix.server import make_server
from codingmix.store import Store

log = logging.getLogger("codingmix")

LOOP_MAX_SLEEP = 5.0
PRUNE_EVERY = 3600.0


class Service:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        classifier: Classifier,
        director: Director,
        recorder: Recorder,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._classifier = classifier
        self._director = director
        self._recorder = recorder
        self._clock = clock
        self._last_prune = 0.0

    def handle_event(self, payload: dict) -> None:
        now = self._clock()
        mode = self._classifier.observe(payload, now)
        event = str(payload.get("hook_event_name") or "unknown")[:40]
        self._store.log_event(now, event, mode)

    def control(self, command: dict) -> dict:
        now = self._clock()
        action = command.get("action")
        if action == "mode":
            mode = command.get("mode")
            if mode not in self._cfg.modes:
                raise ValueError(f"unknown mode {mode!r}; choose from {sorted(self._cfg.modes)}")
            seconds = float(command.get("seconds") or self._cfg.manual_default_seconds)
            self._classifier.set_manual(mode, now + seconds)
            self._director.clear_aside()
            return {"ok": True, "mode": mode, "until": now + seconds}
        if action == "pause":
            seconds = float(command["seconds"])
            self._director.pause(now + seconds)
            return {"ok": True, "paused_until": now + seconds}
        if action == "resume":
            self._classifier.clear_manual()
            self._director.resume()
            return {"ok": True}
        raise ValueError(f"unknown action {action!r}")

    def status(self) -> dict:
        now = self._clock()
        snap = self._classifier.snapshot(now)
        return {
            "mode": snap["stable"],
            "leader": snap["leader"],
            "candidate": snap["candidate"],
            "manual": snap["manual"],
            "manual_until": snap["manual_until"],
            "paused_until": self._director.paused_until,
            "aside_mode": self._director.aside_mode,
            "last_action": self._director.last_action,
            "last_error": self._director.last_error or self._recorder.last_error,
            "device_name": self._cfg.device_name,
            "history": self._store.counts(now, self._cfg.exclusion_days),
            "recent_events": [
                {"ts": ts, "event": event, "mode": mode}
                for ts, event, mode in self._store.recent_events(5)
            ],
        }

    def step(self) -> float:
        now = self._clock()
        self._recorder.maybe_run(now)
        delay = self._director.tick(now)
        if now - self._last_prune >= PRUNE_EVERY:
            self._store.prune(now)
            self._last_prune = now
        return delay

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                delay = self.step()
            except Exception:
                log.exception("unexpected error in the main loop")
                delay = 30.0
            stop.wait(min(max(delay, 1.0), LOOP_MAX_SLEEP))


def setup_logging() -> None:
    handler = RotatingFileHandler(
        paths.log_dir() / "codingmix.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)


def build(cfg: Config) -> Service:
    import httpx

    from codingmix.spotify.auth import TokenStore
    from codingmix.spotify.client import SpotifyClient

    store = Store(paths.db_file())
    tokens = TokenStore(paths.token_fallback_file())
    client = SpotifyClient(httpx.Client(), cfg.client_id or "", tokens)
    classifier = Classifier(cfg.rules, cfg.window_seconds, cfg.switch_after_seconds)
    finder = Finder(client, store, cfg.modes, cfg.exclusion_days)
    director = Director(client, finder, store, classifier, cfg.device_name, cfg.queue_lead_seconds)
    recorder = Recorder(
        client, store, cfg.recent_poll_seconds, cfg.saved_poll_seconds, cfg.exclusion_days
    )
    return Service(cfg, store, classifier, director, recorder)


def run() -> int:
    setup_logging()
    cfg = load_config(paths.config_file())
    if not cfg.client_id:
        # Exit 0 so launchd/systemd do not restart-loop an unconfigured install.
        log.error("CodingMix is not configured; run `codingmix setup`")
        return 0
    service = build(cfg)
    try:
        server = make_server(
            cfg.service_port, paths.ensure_service_token(),
            service.handle_event, service.status, service.control,
        )
    except OSError:
        log.info("port %s is busy: CodingMix is probably already running", cfg.service_port)
        return 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    log.info("CodingMix service listening on 127.0.0.1:%s", cfg.service_port)
    try:
        service.run_forever(stop)
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main() -> None:
    sys.exit(run())
