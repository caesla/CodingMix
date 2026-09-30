"""Filesystem locations. Everything personal lives in user directories, never in the repo."""

from __future__ import annotations

import os
import secrets
import tomllib
from pathlib import Path

from platformdirs import PlatformDirs

_DIRS = PlatformDirs(appname="CodingMix", appauthor=False, roaming=True)


def _home_override() -> Path | None:
    value = os.environ.get("CODINGMIX_HOME")
    return Path(value) if value else None


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    home = _home_override()
    return _ensure(home / "config" if home else Path(_DIRS.user_config_dir))


def data_dir() -> Path:
    home = _home_override()
    return _ensure(home / "data" if home else Path(_DIRS.user_data_dir))


def log_dir() -> Path:
    home = _home_override()
    return _ensure(home / "logs" if home else Path(_DIRS.user_log_dir))


def config_file() -> Path:
    return config_dir() / "config.toml"


def db_file() -> Path:
    return data_dir() / "codingmix.db"


def token_fallback_file() -> Path:
    return config_dir() / "spotify-token.json"


def service_token_file() -> Path:
    return config_dir() / "service-token"


def claude_config_dir() -> Path:
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(value) if value else Path.home() / ".claude"


def write_private(path: Path, text: str) -> None:
    """Write a file readable only by the current user (best effort on Windows)."""
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_service_token() -> str:
    """Shared secret between the hook, the CLI and the service."""
    path = service_token_file()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    write_private(path, token)
    return token


DEFAULT_SERVICE_PORT = 47615


def read_service_port(config_file: Path) -> int:
    """Service port from config.toml without loading the full configuration."""
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        return int(data.get("service", {}).get("port", DEFAULT_SERVICE_PORT))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_SERVICE_PORT
