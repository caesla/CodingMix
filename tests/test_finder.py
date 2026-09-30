import random
import re

from fakes import FakeSpotify, make_track

from spotifymix.config import ModeConfig, load_config
from spotifymix.finder import Finder
from spotifymix.models import Play, Track
from spotifymix.store import Store

DAY = 86400
NOW = 20_000 * DAY
QUERY = re.compile(r'^genre:"[^"]+"( year:\d{4}-\d{4})?$')
# One primary genre keeps these tests independent of the random genre choice.
SINGLE = {"coding": ModeConfig("coding", "Coding", ("deep house",), ("house",))}


def make(pool=None, modes=None, **kw):
    sp = FakeSpotify()
    sp.search_pool = pool or {}
    store = Store(":memory:")
    finder = Finder(sp, store, modes or SINGLE, exclusion_days=7, rng=random.Random(1), **kw)
    return finder, sp, store


def pool(n, prefix):
    return [make_track(i, prefix) for i in range(n)]


def test_returns_fresh_track_for_mode():
    finder, sp, _ = make({"deep house": pool(30, "d")})
    track = finder.next_track("coding", NOW)
    assert track.id.startswith("d")
    assert all(QUERY.match(q) for q, _, _ in sp.search_calls)
    assert all(limit == 10 for _, _, limit in sp.search_calls)


def test_skips_tracks_too_short_for_background_music():
    short = Track("s0", "spotify:track:s0", "Interlude", ("Artist s0",), 23_000)
    finder, *_ = make({"deep house": [short, *pool(5, "d")]})
    tracks = [finder.next_track("coding", NOW) for _ in range(5)]
    assert "s0" not in {t.id for t in tracks if t}


def test_skips_excluded_tracks():
    tracks = pool(10, "d")
    finder, _, store = make({"deep house": tracks})
    store.add_plays([Play(t, NOW - DAY) for t in tracks[:9]])
    assert finder.next_track("coding", NOW).id == "d9"


def test_proposed_track_is_not_returned_again():
    tracks = pool(1, "d")
    finder, _, store = make({"deep house": tracks})
    first = finder.next_track("coding", NOW)
    store.add_proposed(first, "coding", NOW)
    assert finder.next_track("coding", NOW + 1) is None


def test_buffer_avoids_new_searches():
    finder, sp, _ = make({"deep house": pool(30, "d")})
    finder.next_track("coding", NOW)
    calls = len(sp.search_calls)
    finder.next_track("coding", NOW + 1)
    assert len(sp.search_calls) == calls


def test_buffered_tracks_are_rechecked_before_use():
    tracks = pool(12, "d")
    finder, _, store = make({"deep house": tracks})
    first = finder.next_track("coding", NOW)
    store.add_plays([Play(t, NOW) for t in tracks if t.id != first.id])
    store.add_proposed(first, "coding", NOW)
    assert finder.next_track("coding", NOW + 1) is None


def test_uses_fallback_when_primary_is_empty():
    finder, _, _ = make({"house": pool(20, "h")})
    track = finder.next_track("coding", NOW)
    assert track.id.startswith("h")
    assert "fallback" in finder.last_note


def test_gives_up_when_everything_is_excluded():
    tracks = pool(10, "d")
    finder, sp, store = make({"deep house": tracks}, max_queries=6)
    store.add_plays([Play(t, NOW - DAY) for t in tracks])
    assert finder.next_track("coding", NOW) is None
    assert len(sp.search_calls) <= 12
    assert finder.last_note is not None


def test_offsets_stay_inside_results():
    finder, sp, store = make(
        {"deep house": pool(25, "d"), "tech house": pool(25, "t")}, modes=load_config().modes)
    for step in range(15):
        track = finder.next_track("coding", NOW + step)
        if track:
            store.add_proposed(track, "coding", NOW + step)
    assert sp.search_calls
    assert all(offset + limit <= 25 for _, offset, limit in sp.search_calls)


def test_unknown_mode_returns_none():
    finder, _, _ = make()
    assert finder.next_track("dancing", NOW) is None
    assert "unknown mode" in finder.last_note
