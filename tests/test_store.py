from spotifymix.models import Play, SavedTrack, Track
from spotifymix.store import Store

DAY = 86400
NOW = 1_000 * DAY


def t(track_id, name="Song", artist="Artist"):
    return Track(track_id, f"spotify:track:{track_id}", name, (artist,), 200000)


def test_recent_play_is_excluded_by_id_and_by_name():
    store = Store(":memory:")
    store.add_plays([Play(t("a"), NOW - 2 * DAY)])
    assert store.is_excluded(t("a"), NOW, 7)
    assert store.is_excluded(t("other-id", "Song - Remastered"), NOW, 7)
    assert not store.is_excluded(t("b", "Different"), NOW, 7)


def test_old_play_is_not_excluded():
    store = Store(":memory:")
    store.add_plays([Play(t("a"), NOW - 8 * DAY)])
    assert not store.is_excluded(t("a"), NOW, 7)


def test_saved_and_proposed_are_excluded():
    store = Store(":memory:")
    store.upsert_saved([SavedTrack(t("s", "Saved"), NOW - DAY)])
    store.add_proposed(t("p", "Proposed"), "coding", NOW - DAY)
    assert store.is_excluded(t("s", "Saved"), NOW, 7)
    assert store.is_excluded(t("p", "Proposed"), NOW, 7)
    assert store.recent_proposed_ids() == {"p"}


def test_duplicate_plays_are_ignored():
    store = Store(":memory:")
    assert store.add_plays([Play(t("a"), NOW)]) == 1
    assert store.add_plays([Play(t("a"), NOW)]) == 0


def test_prune_and_counts():
    store = Store(":memory:")
    store.add_plays([Play(t("old"), NOW - 9 * DAY), Play(t("new", "New"), NOW - DAY)])
    store.log_event(NOW - 9 * DAY, "PreToolUse", "coding")
    store.log_event(NOW, "PreToolUse", "debugging")
    store.prune(NOW)
    assert store.counts(NOW, 7) == {"plays": 1, "saved": 0, "proposed": 0}
    assert store.recent_events() == [(NOW, "PreToolUse", "debugging")]


def test_kv_round_trip(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    assert store.get_kv("x") is None
    store.set_kv("x", "1")
    store.set_kv("x", "2")
    assert store.get_kv("x") == "2"
    store.close()
