import argparse
import json

import pytest
from fakes import FakeSpotify, free_port, make_track

from spotifymix import cli, paths


@pytest.fixture
def port():
    value = free_port()
    paths.config_file().write_text(f"[service]\nport = {value}\n", encoding="utf-8")
    return value


@pytest.mark.parametrize("text, seconds", [
    ("30m", 1800), ("1h", 3600), ("1h30m", 5400), ("45s", 45), ("90", 5400), (" 2H ", 7200),
])
def test_parse_duration(text, seconds):
    assert cli.parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["abc", "0m", "", "1d"])
def test_parse_duration_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_duration(text)


def test_format_status():
    text = cli.format_status({
        "mode": "coding", "leader": "debugging", "candidate": "debugging", "manual": None,
        "manual_until": None, "paused_until": None, "aside_mode": None,
        "last_action": "queued x Song by Artist (coding)", "last_error": None,
        "device_name": "MY-PC", "history": {"plays": 120, "saved": 8, "proposed": 30},
        "recent_events": [],
    })
    assert "coding" in text and "debugging" in text
    assert "120 plays" in text and "MY-PC" in text


def test_status_when_service_is_down(port, capsys):
    assert cli.run(["status"]) == 1
    assert "not running" in capsys.readouterr().out


def test_mode_rejects_unknown_modes(port, capsys):
    assert cli.run(["mode", "dancing"]) == 2
    assert "coding" in capsys.readouterr().err


def test_mode_pause_resume_send_control(port, monkeypatch):
    sent = []
    monkeypatch.setattr(cli, "call_service",
                        lambda method, path, body=None, **kw: sent.append((path, body)) or {})
    assert cli.run(["mode", "debugging", "--for", "30m"]) == 0
    assert cli.run(["pause", "1h"]) == 0
    assert cli.run(["resume"]) == 0
    assert sent == [
        ("/control", {"action": "mode", "mode": "debugging", "seconds": 1800}),
        ("/control", {"action": "pause", "seconds": 3600}),
        ("/control", {"action": "resume"}),
    ]


def test_hooks_install_and_uninstall(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/spotifymix-hook")
    settings = tmp_path / "claude" / "settings.json"
    assert cli.run(["hooks", "install", "--yes"]) == 0
    assert "spotifymix-hook" in settings.read_text(encoding="utf-8")
    assert cli.run(["hooks", "uninstall", "--yes"]) == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {}


def test_hooks_install_refuses_invalid_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/spotifymix-hook")
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ broken", encoding="utf-8")
    assert cli.run(["hooks", "install", "--yes"]) == 2
    assert settings.read_text(encoding="utf-8") == "{ broken"


def test_hooks_install_asks_before_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/spotifymix-hook")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert cli.run(["hooks", "install"]) == 1
    assert not (tmp_path / "claude" / "settings.json").exists()


def test_modes_lists_all(capsys):
    assert cli.run(["modes"]) == 0
    out = capsys.readouterr().out
    assert "debugging" in out and "hip hop" in out


def test_check_genres_reports_weak_labels(monkeypatch, capsys):
    fake = FakeSpotify()
    fake.search_pool = {"deep house": [make_track(i) for i in range(60)]}
    monkeypatch.setattr(cli, "_client", lambda cfg: fake)
    assert cli.run(["check-genres"]) == 1
    out = capsys.readouterr().out
    assert "deep house (60)" in out
    assert "fewer than 50" in out
