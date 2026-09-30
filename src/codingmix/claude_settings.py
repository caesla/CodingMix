"""Adds and removes the CodingMix hooks in Claude Code's settings.json."""

from __future__ import annotations

import copy
import difflib
import json
import os
import time
from pathlib import Path
from typing import Any

HOOK_EVENTS = ("PreToolUse", "PostToolUseFailure", "UserPromptSubmit", "SubagentStart")
TOOL_EVENTS = {"PreToolUse", "PostToolUseFailure"}
MARKER = "codingmix-hook"


class SettingsError(Exception):
    pass


def settings_path(config_dir: Path) -> Path:
    return config_dir / "settings.json"


def hook_command(executable: Path) -> str:
    return f'"{executable.as_posix()}"'


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SettingsError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} does not contain a JSON object")
    return data


def _is_ours(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    return any(
        isinstance(h, dict) and MARKER in str(h.get("command", ""))
        for h in entry.get("hooks", [])
    )


def _hooks_table(settings: dict[str, Any], create: bool) -> dict[str, Any] | None:
    hooks = settings.get("hooks")
    if hooks is None:
        if not create:
            return None
        hooks = settings["hooks"] = {}
    if not isinstance(hooks, dict):
        raise SettingsError('"hooks" in settings.json is not an object')
    return hooks


def plan_install(settings: dict[str, Any], command: str) -> dict[str, Any]:
    new = copy.deepcopy(settings)
    hooks = _hooks_table(new, create=True)
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise SettingsError(f'"hooks.{event}" in settings.json is not a list')
        entries[:] = [e for e in entries if not _is_ours(e)]
        entry: dict[str, Any] = {
            "hooks": [{"type": "command", "command": command, "async": True, "timeout": 10}]
        }
        if event in TOOL_EVENTS:
            entry = {"matcher": "*", **entry}
        entries.append(entry)
    return new


def plan_uninstall(settings: dict[str, Any]) -> dict[str, Any]:
    new = copy.deepcopy(settings)
    hooks = _hooks_table(new, create=False)
    if hooks is None:
        return new
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not _is_ours(e)]
        if len(kept) != len(entries):
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not hooks:
        del new["hooks"]
    return new


def write_settings(path: Path, data: dict[str, Any]) -> Path | None:
    backup = None
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.codingmix-backup-{stamp}")
        backup.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".codingmix-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def diff_text(old: dict[str, Any], new: dict[str, Any]) -> str:
    before = json.dumps(old, indent=2, ensure_ascii=False).splitlines()
    after = json.dumps(new, indent=2, ensure_ascii=False).splitlines()
    return "\n".join(difflib.unified_diff(before, after, "before", "after", lineterm=""))
