import json
from pathlib import Path

import pytest

from codingmix.claude_settings import (
    HOOK_EVENTS,
    SettingsError,
    diff_text,
    hook_command,
    load_settings,
    plan_install,
    plan_uninstall,
    settings_path,
    write_settings,
)

CMD = '"/opt/bin/codingmix-hook"'
FOREIGN = {
    "model": "opus",
    "hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "python other.py"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}],
    },
}


def test_install_into_empty_settings():
    new = plan_install({}, CMD)
    assert set(new["hooks"]) == set(HOOK_EVENTS)
    pre = new["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "*"
    assert pre["hooks"][0] == {"type": "command", "command": CMD, "async": True, "timeout": 10}
    assert "matcher" not in new["hooks"]["UserPromptSubmit"][0]


def test_install_is_idempotent():
    once = plan_install({}, CMD)
    assert plan_install(once, CMD) == once


def test_install_preserves_foreign_hooks():
    new = plan_install(FOREIGN, CMD)
    assert new["model"] == "opus"
    assert new["hooks"]["SessionStart"] == FOREIGN["hooks"]["SessionStart"]
    assert new["hooks"]["PreToolUse"][0] == FOREIGN["hooks"]["PreToolUse"][0]
    assert len(new["hooks"]["PreToolUse"]) == 2
    assert FOREIGN["hooks"]["PreToolUse"] == [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}
    ]


def test_uninstall_restores_the_original():
    assert plan_uninstall(plan_install(FOREIGN, CMD)) == FOREIGN
    assert plan_uninstall(plan_install({}, CMD)) == {}


def test_invalid_json_is_not_touched(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="not valid JSON"):
        load_settings(path)
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_non_object_hooks_are_rejected():
    with pytest.raises(SettingsError):
        plan_install({"hooks": []}, CMD)


def test_missing_or_empty_file_is_empty_settings(tmp_path):
    assert load_settings(tmp_path / "missing.json") == {}
    empty = tmp_path / "empty.json"
    empty.write_text("  \n", encoding="utf-8")
    assert load_settings(empty) == {}


def test_write_creates_backup_and_valid_json(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(FOREIGN), encoding="utf-8")
    backup = write_settings(path, plan_install(FOREIGN, CMD))
    assert backup is not None and json.loads(backup.read_text(encoding="utf-8")) == FOREIGN
    assert "codingmix-hook" in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "opus"


def test_write_without_existing_file_has_no_backup(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    assert write_settings(path, {"a": 1}) is None
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_hook_command_quotes_paths_with_spaces():
    exe = Path("C:/Program Files/uv/codingmix-hook.exe")
    assert hook_command(exe) == '"C:/Program Files/uv/codingmix-hook.exe"'


def test_settings_path_and_diff(tmp_path):
    assert settings_path(tmp_path) == tmp_path / "settings.json"
    text = diff_text({}, {"a": 1})
    assert '+  "a": 1' in text
