from fakes import FakeSpotify, make_track

from codingmix.models import Play, SavedTrack
from codingmix.recorder import Recorder
from codingmix.spotify.auth import LoginRequired
from codingmix.spotify.client import SpotifyUnavailable
from codingmix.store import Store

DAY = 86400
NOW = 1000 * DAY


def make():
    sp = FakeSpotify()
    store = Store(":memory:")
    return Recorder(sp, store, recent_every=600, saved_every=1800, exclusion_days=7), sp, store


def test_sync_recent_stores_plays():
    rec, sp, store = make()
    sp.recent = [Play(make_track(1), NOW - 60)]
    assert rec.sync_recent(NOW) == 1
    assert store.is_excluded(make_track(1), NOW, 7)


def test_sync_saved_stops_at_the_cutoff():
    rec, sp, store = make()
    sp.saved_pages = [([SavedTrack(make_track(1), NOW - DAY),
                        SavedTrack(make_track(2), NOW - 8 * DAY)], True)]
    assert rec.sync_saved(NOW) == 1
    assert sp.saved_calls == [0]
    assert store.is_excluded(make_track(1), NOW, 7)
    assert not store.is_excluded(make_track(2), NOW, 7)


def test_sync_saved_follows_pages():
    rec, sp, _ = make()
    sp.saved_pages = [
        ([SavedTrack(make_track(i), NOW - DAY) for i in range(50)], True),
        ([SavedTrack(make_track(99), NOW - DAY)], False),
    ]
    assert rec.sync_saved(NOW) == 51
    assert sp.saved_calls == [0, 50]


def test_maybe_run_respects_intervals():
    rec, sp, _ = make()
    rec.maybe_run(NOW)
    assert (sp.recent_calls, len(sp.saved_calls)) == (1, 1)
    rec.maybe_run(NOW + 100)
    assert (sp.recent_calls, len(sp.saved_calls)) == (1, 1)
    rec.maybe_run(NOW + 600)
    assert (sp.recent_calls, len(sp.saved_calls)) == (2, 1)
    rec.maybe_run(NOW + 1800)
    assert (sp.recent_calls, len(sp.saved_calls)) == (3, 2)


def test_errors_are_recorded_not_raised():
    rec, sp, _ = make()
    sp.error = SpotifyUnavailable("Spotify is down")
    rec.maybe_run(NOW)
    assert rec.last_error == "Spotify is down"
    sp.error = LoginRequired("login again")
    rec.maybe_run(NOW + 600)
    assert rec.last_error == "login again"
    sp.error = None
    rec.maybe_run(NOW + 1200)
    assert rec.last_error is None
