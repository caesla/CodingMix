import tomllib

import pytest

from codingmix.config import ConfigError, dump_toml, load_config, save_user_settings


def test_defaults_define_eleven_modes():
    cfg = load_config()
    assert len(cfg.modes) == 11
    assert cfg.modes["coding"].genres == ("deep house", "tech house")
    assert cfg.modes["writing"].genres == cfg.modes["coding"].genres
    assert cfg.modes["orchestrating"].genres == cfg.modes["coding"].genres
    assert cfg.modes["debugging"].genres == ("hip hop", "rap")
    assert cfg.client_id is None
    assert cfg.service_port == 47615
    assert cfg.exclusion_days == 7
    assert cfg.rules[0].mode == "debugging"


def test_user_file_overrides_one_mode(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text('[modes.debugging]\ngenres = ["drum and bass"]\n', encoding="utf-8")
    cfg = load_config(user)
    assert cfg.modes["debugging"].genres == ("drum and bass",)
    assert cfg.modes["debugging"].fallback == ("trap",)
    assert cfg.modes["coding"].genres == ("deep house", "tech house")


def test_user_rules_replace_default_rules(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text('[[rules]]\nmode = "coding"\nevents = ["PreToolUse"]\n', encoding="utf-8")
    cfg = load_config(user)
    assert len(cfg.rules) == 1


def test_rule_with_unknown_mode_is_rejected(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text('[[rules]]\nmode = "dancing"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown mode"):
        load_config(user)


def test_rule_with_invalid_regex_is_rejected(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text('[[rules]]\nmode = "coding"\ncommand = "(["\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid regex"):
        load_config(user)


def test_broken_user_file_is_reported(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text("this is = = not toml", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(user)


def test_save_user_settings_keeps_mode_overrides(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text('[modes.debugging]\ngenres = ["drum and bass"]\n', encoding="utf-8")
    save_user_settings(user, client_id="abc123", device_name="MY-PC")
    cfg = load_config(user)
    assert cfg.client_id == "abc123"
    assert cfg.device_name == "MY-PC"
    assert cfg.modes["debugging"].genres == ("drum and bass",)


def test_dump_toml_round_trips():
    data = {
        "spotify": {"client_id": 'we"ird\\id', "redirect_port": 1},
        "modes": {"coding": {"genres": ["deep house", "città"], "label": "Coding"}},
        "rules": [{"mode": "coding", "weight": 0.5, "events": ["PreToolUse"]}],
    }
    assert tomllib.loads(dump_toml(data)) == data
