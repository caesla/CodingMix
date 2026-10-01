import random
import threading

import pytest
from fakes import FakeSpotify, make_track, playing

from codingmix import service as service_module
from codingmix.classifier import Classifier
from codingmix.config import load_config
from codingmix.director import Director
from codingmix.finder import Finder
from codingmix.recorder import Recorder
from codingmix.service import Service
from codingmix.store import Store

NOW = 2_000_000_000.0
CUR = make_track(1, "cur")
EDIT = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": "C:/Users/someone/secret-project/app.py"},
        "prompt": "top secret words", "session_id": "s1"}


def build(store=None):
    cfg = load_config()
    sp = FakeSpotify()
    sp.search_pool = {
        genre: [make_track(i, genre.replace(" ", "")[:4]) for i in range(30)]
        for genre in ("deep house", "tech house", "french house", "disco house", "funky house")
    }
    store = store or Store(":memory:")
    clock = [NOW]
    classifier = Classifier(cfg.rules, cfg.window_seconds, cfg.switch_after_seconds)
    finder = Finder(sp, store, cfg.modes, cfg.exclusion_days, rng=random.Random(3))
    director = Director(sp, finder, store, classifier, "MY-PC", cfg.queue_lead_seconds)
    recorder = Recorder(sp, store, cfg.recent_poll_seconds, cfg.saved_poll_seconds,
                        cfg.exclusion_days)
    svc = Service(cfg, store, classifier, director, recorder, clock=lambda: clock[0])
    return svc, sp, store, clock


def queued_prefixes(sp):
    return [uri.rsplit(":", 1)[1][:4] for uri, _ in sp.queued]


def test_event_leads_to_a_queued_track_of_the_mode():
    svc, sp, _, _ = build()
    svc.handle_event(EDIT)
    sp.playback = playing(CUR, 185_000)
    svc.step()
    assert queued_prefixes(sp) in (["deep"], ["tech"])


def test_events_are_persisted_without_payload_details(tmp_path):
    db = tmp_path / "db.sqlite"
    svc, _, store, _ = build(Store(db))
    svc.handle_event(EDIT)
    assert store.recent_events() == [(int(NOW), "PreToolUse", "coding")]
    store.close()
    content = db.read_bytes()
    assert b"secret" not in content
    assert b"someone" not in content


def test_manual_mode_control():
    svc, sp, _, _ = build()
    svc.handle_event(EDIT)
    reply = svc.control({"action": "mode", "mode": "debugging", "seconds": 600})
    assert reply["mode"] == "debugging"
    assert svc.status()["manual"] == "debugging"
    sp.playback = playing(CUR, 185_000)
    svc.step()
    # Track ids start with the first 4 letters of the genre, e.g. "fren..." or "disc...".
    assert queued_prefixes(sp)[0].startswith(("fren", "disc", "funk"))


def test_unknown_mode_and_action_are_rejected():
    svc, *_ = build()
    with pytest.raises(ValueError, match="unknown mode"):
        svc.control({"action": "mode", "mode": "dancing"})
    with pytest.raises(ValueError, match="unknown action"):
        svc.control({"action": "dance"})


def test_pause_and_resume():
    svc, sp, _, _ = build()
    svc.step()  # the recorder runs now and is not due again for 10 minutes
    svc.control({"action": "pause", "seconds": 600})
    assert svc.status()["paused_until"] == NOW + 600
    sp.error = AssertionError("Spotify must not be called while paused")
    svc.step()
    sp.error = None
    svc.control({"action": "resume"})
    assert svc.status()["paused_until"] is None


def test_status_shape():
    svc, *_ = build()
    assert set(svc.status()) == {
        "mode", "leader", "candidate", "manual", "manual_until", "paused_until",
        "aside_mode", "last_action", "last_error", "device_name", "history", "recent_events",
    }


def test_run_forever_survives_errors_and_stops(monkeypatch):
    svc, *_ = build()
    stop = threading.Event()
    calls = []

    def failing_step():
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service_module, "LOOP_MAX_SLEEP", 0.01)
    monkeypatch.setattr(svc, "step", failing_step)
    svc.run_forever(stop)
    assert len(calls) == 2
