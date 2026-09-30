"""Configuration: packaged defaults merged with the user's config.toml."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

RULE_KEYS = {
    "mode", "events", "tools", "permission_mode", "skill", "command", "path",
    "subagent_type", "agent_type", "prompt", "weight",
}
REGEX_KEYS = ("skill", "command", "path", "subagent_type", "agent_type", "prompt")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModeConfig:
    id: str
    label: str
    genres: tuple[str, ...]
    fallback: tuple[str, ...]


@dataclass(frozen=True)
class Rule:
    mode: str
    events: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    permission_mode: str | None = None
    skill: str | None = None
    command: str | None = None
    path: str | None = None
    subagent_type: str | None = None
    agent_type: str | None = None
    prompt: str | None = None
    weight: float = 1.0


@dataclass(frozen=True)
class Config:
    client_id: str | None
    redirect_port: int
    device_name: str | None
    service_port: int
    window_seconds: int
    switch_after_seconds: int
    queue_lead_seconds: int
    exclusion_days: int
    recent_poll_seconds: int
    saved_poll_seconds: int
    manual_default_seconds: int
    modes: dict[str, ModeConfig]
    rules: tuple[Rule, ...]


def _defaults() -> dict[str, Any]:
    text = resources.files("spotifymix").joinpath("defaults.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key == "rules":
            out[key] = value
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_user(user_file: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(user_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{user_file}: {exc}") from exc


def load_config(user_file: Path | None = None) -> Config:
    data = _defaults()
    if user_file is not None and user_file.exists():
        data = _merge(data, _read_user(user_file))
    return _build(data)


def _build(data: dict[str, Any]) -> Config:
    spotify = data.get("spotify", {})
    service = data.get("service", {})
    modes: dict[str, ModeConfig] = {}
    for mode_id, raw in data.get("modes", {}).items():
        genres = tuple(raw.get("genres", ()))
        if not genres:
            raise ConfigError(f"mode {mode_id!r} has no genres")
        modes[mode_id] = ModeConfig(
            id=mode_id,
            label=str(raw.get("label", mode_id)),
            genres=genres,
            fallback=tuple(raw.get("fallback", ())),
        )
    rules = tuple(_build_rule(i, raw, modes) for i, raw in enumerate(data.get("rules", [])))
    return Config(
        client_id=spotify.get("client_id") or None,
        redirect_port=int(spotify.get("redirect_port", 47616)),
        device_name=spotify.get("device_name") or None,
        service_port=int(service.get("port", 47615)),
        window_seconds=int(service.get("window_seconds", 180)),
        switch_after_seconds=int(service.get("switch_after_seconds", 180)),
        queue_lead_seconds=int(service.get("queue_lead_seconds", 20)),
        exclusion_days=int(service.get("exclusion_days", 7)),
        recent_poll_seconds=int(service.get("recent_poll_seconds", 600)),
        saved_poll_seconds=int(service.get("saved_poll_seconds", 1800)),
        manual_default_seconds=int(service.get("manual_default_seconds", 3600)),
        modes=modes,
        rules=rules,
    )


def _build_rule(index: int, raw: dict[str, Any], modes: dict[str, ModeConfig]) -> Rule:
    label = f"rule #{index + 1}"
    unknown = set(raw) - RULE_KEYS
    if unknown:
        raise ConfigError(f"{label}: unknown keys {sorted(unknown)}")
    if raw.get("mode") not in modes:
        raise ConfigError(f"{label}: unknown mode {raw.get('mode')!r}")
    for key in REGEX_KEYS:
        if raw.get(key) is not None:
            try:
                re.compile(raw[key])
            except re.error as exc:
                raise ConfigError(f"{label}: invalid regex in {key!r}: {exc}") from exc
    return Rule(
        mode=raw["mode"],
        events=tuple(raw.get("events", ())),
        tools=tuple(raw.get("tools", ())),
        permission_mode=raw.get("permission_mode"),
        skill=raw.get("skill"),
        command=raw.get("command"),
        path=raw.get("path"),
        subagent_type=raw.get("subagent_type"),
        agent_type=raw.get("agent_type"),
        prompt=raw.get("prompt"),
        weight=float(raw.get("weight", 1.0)),
    )


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # JSON string escapes are valid TOML basic-string escapes.
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def dump_toml(data: dict[str, Any]) -> str:
    """Minimal TOML writer for our own config shapes (tables, arrays of flat tables)."""
    lines: list[str] = []

    def emit(name: str | None, table: dict[str, Any]) -> None:
        scalars = [
            (k, v) for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)
        ]
        if name is not None and scalars:
            lines.append(f"[{name}]")
        for k, v in scalars:
            lines.append(f"{_key(k)} = {_value(v)}")
        if scalars:
            lines.append("")
        for k, v in table.items():
            if isinstance(v, dict):
                emit(f"{name}.{_key(k)}" if name else _key(k), v)
        for k, v in table.items():
            if _is_table_array(v):
                full = f"{name}.{_key(k)}" if name else _key(k)
                for item in v:
                    lines.append(f"[[{full}]]")
                    for ik, iv in item.items():
                        lines.append(f"{_key(ik)} = {_value(iv)}")
                    lines.append("")

    emit(None, data)
    return "\n".join(lines).rstrip() + "\n"


def save_user_settings(user_file: Path, **spotify_values: str | int | None) -> None:
    """Update the [spotify] table of the user config, keeping everything else."""
    data = _read_user(user_file) if user_file.exists() else {}
    section = data.setdefault("spotify", {})
    section.update({k: v for k, v in spotify_values.items() if v is not None})
    user_file.parent.mkdir(parents=True, exist_ok=True)
    user_file.write_text(dump_toml(data), encoding="utf-8")
