from codingmix import paths


def test_dirs_live_under_override(isolated_home):
    assert paths.config_dir() == isolated_home / "config"
    assert paths.data_dir() == isolated_home / "data"
    assert paths.log_dir() == isolated_home / "logs"
    assert paths.config_dir().is_dir()
    assert paths.config_file() == isolated_home / "config" / "config.toml"
    assert paths.db_file() == isolated_home / "data" / "codingmix.db"


def test_claude_config_dir_honours_env(tmp_path):
    assert paths.claude_config_dir() == tmp_path / "claude"


def test_claude_config_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.claude_config_dir() == tmp_path / ".claude"


def test_service_token_is_created_once():
    first = paths.ensure_service_token()
    second = paths.ensure_service_token()
    assert first == second
    assert len(first) >= 32
    assert paths.service_token_file().read_text(encoding="utf-8").strip() == first
