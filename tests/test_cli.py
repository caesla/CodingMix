import argparse
import json

import pytest
from fakes import FakeSpotify, free_port, make_track

from codingmix import cli, paths


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
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/codingmix-hook")
    settings = tmp_path / "claude" / "settings.json"
    assert cli.run(["hooks", "install", "--yes"]) == 0
    assert "codingmix-hook" in settings.read_text(encoding="utf-8")
    assert cli.run(["hooks", "uninstall", "--yes"]) == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {}


def test_hooks_install_refuses_invalid_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/codingmix-hook")
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ broken", encoding="utf-8")
    assert cli.run(["hooks", "install", "--yes"]) == 2
    assert settings.read_text(encoding="utf-8") == "{ broken"


def test_hooks_install_asks_before_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/codingmix-hook")
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


def test_spotify_errors_become_readable_messages(monkeypatch, capsys):
    from codingmix.spotify.client import SpotifyError

    fake = FakeSpotify()
    fake.error = SpotifyError("Spotify returned HTTP 403 for /search")
    monkeypatch.setattr(cli, "_client", lambda cfg: fake)
    assert cli.run(["check-genres"]) == 1
    err = capsys.readouterr().err
    assert "HTTP 403" in err
    assert "Traceback" not in err


def _fake_setup(monkeypatch, answers, hooks_result=0, service_result=0):
    replies = iter(["my-client-id", *answers])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    monkeypatch.setattr(cli, "cmd_login", lambda args: 0)
    monkeypatch.setattr(cli, "_choose_device", lambda cfg: "MY-PC")
    done = []
    monkeypatch.setattr(cli, "cmd_hooks", lambda args, apply_by_default=False: done.append(
        ("hooks", apply_by_default)) or hooks_result)
    monkeypatch.setattr(cli, "cmd_service", lambda args: done.append(
        ("service", args.action)) or service_result)
    return done


def test_setup_installs_hooks_and_autostart_by_default(monkeypatch, capsys):
    done = _fake_setup(monkeypatch, [""])
    assert cli.run(["setup"]) == 0
    assert done == [("hooks", True), ("service", "install")]
    assert "Setup complete" in capsys.readouterr().out


def test_setup_lists_what_was_skipped(monkeypatch, capsys):
    done = _fake_setup(monkeypatch, ["n"], hooks_result=1)
    assert cli.run(["setup"]) == 0
    assert done == [("hooks", True)]
    out = capsys.readouterr().out
    assert "Setup complete" not in out
    assert "codingmix hooks install" in out
    assert "codingmix service install" in out


def test_setup_reports_a_failed_step(monkeypatch, capsys):
    _fake_setup(monkeypatch, ["y"], service_result=1)
    assert cli.run(["setup"]) == 0
    out = capsys.readouterr().out
    assert "Setup complete" not in out
    assert "codingmix service install" in out
    assert "codingmix hooks install" not in out


def test_hooks_apply_by_default_writes_on_enter(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/codingmix-hook")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    args = argparse.Namespace(action="install", yes=False)
    assert cli.cmd_hooks(args, apply_by_default=True) == 0
    assert "codingmix-hook" in (tmp_path / "claude" / "settings.json").read_text(encoding="utf-8")
    assert "Changes to" in capsys.readouterr().out


def test_confirm_defaults(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert cli._confirm("Go?", default=True) is True
    assert cli._confirm("Go?") is False
    monkeypatch.setattr("builtins.input", lambda prompt="": "N")
    assert cli._confirm("Go?", default=True) is False


def test_uninstall_prints_a_shared_folder_once(monkeypatch, tmp_path, capsys):
    shared = tmp_path / "CodingMix"
    monkeypatch.setattr(cli.paths, "config_dir", lambda: shared)
    monkeypatch.setattr(cli.paths, "data_dir", lambda: shared)
    monkeypatch.setattr(cli, "cmd_hooks", lambda args: 0)
    monkeypatch.setattr(cli, "cmd_service", lambda args: 0)
    assert cli.run(["uninstall", "--yes"]) == 0
    assert capsys.readouterr().out.count(str(shared)) == 1
