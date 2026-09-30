from spotifymix.models import Playback, Track, make_name_key, parse_iso


def track_json(track_id="t1", name="Song", artist="Artist", **extra):
    data = {
        "type": "track", "id": track_id, "uri": f"spotify:track:{track_id}", "name": name,
        "artists": [{"name": artist}], "duration_ms": 200000,
    }
    data.update(extra)
    return data


def test_name_key_ignores_versions_and_features():
    base = make_name_key("Song", "Artist")
    assert make_name_key("Song (feat. Someone) - Remastered 2011", "Artist") == base
    assert make_name_key("SONG - Radio Edit", "artist") == base
    assert make_name_key("Song [Live]", "Artist") == base
    assert make_name_key("Other", "Artist") != base


def test_track_from_json():
    track = Track.from_json(track_json())
    assert track.id == "t1"
    assert track.artists == ("Artist",)
    assert track.name_key == "song|artist"


def test_track_from_json_rejects_episodes_and_missing():
    assert Track.from_json(None) is None
    assert Track.from_json({"type": "episode", "id": "e1"}) is None
    assert Track.from_json({"type": "track", "id": None}) is None


def test_playback_from_json_track():
    pb = Playback.from_json({
        "is_playing": True, "progress_ms": 1000, "currently_playing_type": "track",
        "device": {"id": "d1", "name": "MY-PC", "type": "Computer"},
        "item": track_json(), "context": {"uri": "spotify:playlist:x"},
    })
    assert pb.is_playing and pb.device_type == "Computer" and pb.item.id == "t1"
    assert pb.context_uri == "spotify:playlist:x"


def test_playback_from_json_ad_has_no_item():
    pb = Playback.from_json({
        "is_playing": True, "currently_playing_type": "ad", "item": None,
        "device": {"id": "d1", "name": "MY-PC", "type": "Computer"},
    })
    assert pb.item is None
    assert pb.context_uri is None


def test_parse_iso():
    assert parse_iso("1970-01-01T00:01:00Z") == 60
    assert parse_iso("1970-01-01T00:01:00.500Z") == 60
