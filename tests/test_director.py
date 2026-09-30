import pytest
from fakes import FakeFinder, FakeModes, FakeSpotify, make_track, playing

from codingmix.director import Director
from codingmix.models import Playback
from codingmix.spotify.auth import LoginRequired
from codingmix.spotify.client import RateLimited, SpotifyUnavailable
from codingmix.store import Store

NOW = 1_000_000.0
CUR = make_track(99, "cur")  # 200 s long


def make(mode="coding", device_name="MY-PC"):
    sp = FakeSpotify()
    store = Store(":memory:")
    modes = FakeModes(mode)
    finder = FakeFinder()
    finder.pools = {
        "coding": [make_track(i, "c") for i in range(5)],
        "debugging": [make_track(i, "d") for i in range(5)],
    }
    director = Director(sp, finder, store, modes, device_name, lead_seconds=20)
    return director, sp, store, modes, finder


def queued_ids(sp):
    return [uri.rsplit(":", 1)[1] for uri, _ in sp.queued]


def test_ignores_paused_playback():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 195_000, is_playing=False)
    d.tick(NOW)
    assert sp.queued == []


def test_ignores_ads_and_episodes():
    d, sp, *_ = make()
    sp.playback = Playback(True, "dev1", "MY-PC", "Computer", None, 0, None)
    d.tick(NOW)
    assert sp.queued == []


def test_ignores_other_devices():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 195_000, device_type="Smartphone", device_name="PHONE")
    d.tick(NOW)
    sp.playback = playing(CUR, 195_000, device_name="OTHER-PC")
    d.tick(NOW + 100)
    assert sp.queued == []


def test_any_computer_when_device_name_not_set():
    d, sp, *_ = make(device_name=None)
    sp.playback = playing(CUR, 185_000, device_name="WHATEVER")
    d.tick(NOW)
    assert queued_ids(sp) == ["c0"]


def test_waits_until_lead_time():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 100_000)
    assert d.tick(NOW) == 30
    sp.playback = playing(CUR, 170_000)
    assert d.tick(NOW + 30) == 10
    assert sp.queued == []


def test_queues_one_track_near_the_end():
    d, sp, store, *_ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    d.tick(NOW + 100)
    assert queued_ids(sp) == ["c0"]
    assert sp.queued[0][1] == "dev1"
    assert store.recent_proposed_ids() == {"c0"}
    assert "c0" in d.last_action


def test_mode_change_applies_to_next_track():
    d, sp, _, modes, _ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    sp.playback = playing(make_track(0, "c"), 185_000)
    modes.mode = "debugging"
    d.tick(NOW + 100)
    assert queued_ids(sp) == ["c0", "d0"]


def test_steps_aside_when_user_picks_own_music():
    d, sp, _, modes, _ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    own = make_track(1, "own")
    sp.playback = playing(own, 185_000, context="spotify:playlist:mine")
    d.tick(NOW + 100)
    assert queued_ids(sp) == ["c0"]
    assert d.aside_mode == "coding"
    modes.mode = "debugging"
    d.tick(NOW + 200)
    assert queued_ids(sp) == ["c0", "d0"]
    assert d.aside_mode is None


def test_resume_clears_aside():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    sp.playback = playing(make_track(1, "own"), 185_000)
    d.tick(NOW + 100)
    d.resume()
    d.tick(NOW + 101)
    assert queued_ids(sp) == ["c0", "c1"]


def test_pause_skips_spotify_calls():
    d, sp, *_ = make()
    sp.error = AssertionError("Spotify must not be called while paused")
    d.pause(NOW + 600)
    assert d.tick(NOW) <= 30
    assert d.paused_until == NOW + 600


def test_rate_limit_backs_off():
    d, sp, *_ = make()
    sp.error = RateLimited(42)
    assert d.tick(NOW) == 42
    assert "rate limit" in d.last_error


def test_unavailable_backs_off():
    d, sp, *_ = make()
    sp.error = SpotifyUnavailable("offline")
    assert d.tick(NOW) == 30


def test_login_required_slows_polling():
    d, sp, *_ = make()
    sp.error = LoginRequired("run codingmix login")
    assert d.tick(NOW) == 60
    assert "login" in d.last_error


def test_no_track_found_is_reported_once():
    d, sp, _, _, finder = make()
    finder.pools = {}
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    d.tick(NOW + 100)
    assert sp.queued == []
    assert finder.calls == 1
    assert d.last_error


def test_queue_failure_is_retried():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 185_000)
    sp.queue_error = SpotifyUnavailable("hiccup")
    assert d.tick(NOW) == 10
    sp.queue_error = None
    d.tick(NOW + 10)
    assert len(sp.queued) == 1


def test_unexpected_queue_error_is_not_retried():
    # The request may have reached Spotify: a second attempt could queue twice.
    d, sp, store, *_ = make()
    sp.playback = playing(CUR, 185_000)
    calls = []

    def add_to_queue(uri, device_id=None):
        calls.append(uri)
        raise ValueError("unreadable response")

    sp.add_to_queue = add_to_queue
    d.tick(NOW)
    d.tick(NOW + 5)
    assert len(calls) == 1
    assert store.recent_proposed_ids() == {"c0"}
    assert d.last_error


def test_error_after_a_queued_track_does_not_queue_again():
    d, sp, store, *_ = make()
    sp.playback = playing(CUR, 185_000)
    store.add_proposed = lambda *args: (_ for _ in ()).throw(RuntimeError("disk full"))
    with pytest.raises(RuntimeError):
        d.tick(NOW)
    d.tick(NOW + 5)
    assert queued_ids(sp) == ["c0"]


def test_restart_near_the_end_does_not_queue_again():
    d, sp, store, modes, finder = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    restarted = Director(sp, finder, store, modes, "MY-PC", lead_seconds=20)
    restarted.tick(NOW + 5)
    assert queued_ids(sp) == ["c0"]


def test_polls_only_when_due():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 100_000)
    assert d.tick(NOW) == 30
    sp.error = AssertionError("not due yet")
    assert d.tick(NOW + 10) == 20
