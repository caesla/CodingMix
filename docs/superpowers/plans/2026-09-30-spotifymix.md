# SpotifyMix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. If those skills are not installed in your environment (for example in a cloud session), execute the tasks in order yourself, following every step exactly, and tick the checkboxes in this file as you go.

**Goal:** A local background service that infers the current Claude Code activity from hook events and queues fresh Spotify tracks of a matching genre, never replaying anything listened to, saved or proposed in the last 7 days.

**Architecture:** Claude Code runs an async hook (`spotifymix-hook`) on every relevant event; the hook forwards the JSON payload to a service listening on `127.0.0.1`. The service classifies events into activity modes with data-driven rules, keeps a stable mode over a 3 minute window, watches Spotify playback and queues one track at a time about 20 seconds before the current track ends. A SQLite store keeps 7 days of listening history, saved tracks and proposals.

**Tech Stack:** Python 3.11+, uv, httpx, keyring, platformdirs, stdlib `http.server` and `sqlite3`, pytest, ruff, GitHub Actions (Windows, macOS, Linux), gitleaks.

**Spec:** `docs/superpowers/specs/2026-09-30-spotifymix-design.md` (Italian). Read it before starting.

## Global Constraints

- Python `>=3.11`. Runtime dependencies: only `httpx`, `keyring`, `platformdirs`. Dev: `pytest`, `ruff`.
- Code, comments, CLI output, README: English. CLI commands are English (`status`, `mode`, `pause`, `resume`); the spec's Italian names are superseded by Task 15.
- Personal data (config, database, logs, tokens) lives only in platformdirs user directories. Env var `SPOTIFYMIX_HOME` overrides all of them (used by tests).
- Never write prompt text, file paths or raw hook payloads to disk or logs. Only event name, mode and timestamp are persisted.
- Tests never touch the network, the real OS keyring, the real Claude Code settings or the real user directories (`tests/conftest.py` enforces isolation).
- The hook always exits 0, uses only the stdlib plus `spotifymix.paths`, and gives up after a 1 second network timeout.
- The service binds `127.0.0.1` only and rejects requests without a matching `X-SpotifyMix-Token` header.
- Spotify search: `limit` at most 10, `offset + limit` at most 1000, `market=from_token`.
- Default numbers: window 180 s, switch after 180 s, queue lead 20 s, exclusion 7 days, recently-played poll 600 s, saved poll 1800 s, manual override 3600 s, service port 47615, OAuth redirect port 47616.
- Login: Authorization Code with PKCE, redirect `http://127.0.0.1:<port>/callback`. No client secret anywhere.
- No `shell=True`; use `pathlib`; must work on Windows, macOS and Linux.
- Git identity for this repo: `caesla <219047344+caesla@users.noreply.github.com>`. Never commit with another identity. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never use the em dash or en dash as punctuation in any file.
- If ruff reports a style issue (for example line length) in code copied from this plan, fix the formatting without changing behavior.
- Tests import shared doubles with `from fakes import ...` (pytest puts `tests/` on `sys.path`; there is no `tests/__init__.py`).

## Review Focus

1. Service stopped or hung while Claude Code runs: the hook must return exit 0 within about 2 s (1 s network timeout plus process overhead) and never raise. Pinned in Task 10 (`test_hook_gives_up_quickly_when_service_hangs`, `test_hook_exits_zero_when_service_down`).
2. Existing Claude Code settings file that is invalid JSON, or a config dir moved with `CLAUDE_CONFIG_DIR`: installing hooks must not write anything and must not lose other hooks. Pinned in Task 12 (`test_invalid_json_is_not_touched`, `test_install_preserves_foreign_hooks`) and Task 1 (`test_claude_config_dir_honours_env`).
3. Playback that is an ad, a podcast episode, paused, or on a phone: the director must do nothing and not crash. Pinned in Task 9 (`test_ignores_ads_and_episodes`, `test_ignores_other_devices`, `test_ignores_paused_playback`).
4. A genre label with zero Spotify results, or every result already heard: the finder must fall back, then give up without looping. Pinned in Task 8 (`test_uses_fallback_when_primary_is_empty`, `test_gives_up_when_everything_is_excluded`).
5. Revoked refresh token: surface "login required" in status, slow polling to 60 s, never crash-loop. Pinned in Task 6 (`test_invalid_grant_raises_login_required`) and Task 9 (`test_login_required_slows_polling`).

---

## File Structure

```
pyproject.toml                      package metadata, scripts, tool config
CLAUDE.md                           rules for any Claude session working in this repo
.gitattributes                      LF line endings
.pre-commit-config.yaml             gitleaks before each commit
.github/workflows/ci.yml            tests + ruff on 3 OSes, gitleaks
src/spotifymix/
  __init__.py                       version
  paths.py                          user directories, service token, Claude config dir
  defaults.toml                     modes, genres, classification rules, service numbers
  config.py                         load defaults + user overrides, write user settings
  models.py                         Track, Playback, Play, SavedTrack, Device, name keys
  store.py                          SQLite: plays, saved, proposed, events, kv
  classifier.py                     hook payload -> vote -> stable mode, manual override
  spotify/__init__.py
  spotify/auth.py                   PKCE, token exchange/refresh, TokenStore, interactive login
  spotify/client.py                 Web API wrapper with refresh, 429 and error mapping
  recorder.py                       sync recently played and saved tracks
  finder.py                         genre search, exclusion filter, per-mode buffer
  director.py                       watches playback, queues one track before the end
  server.py                         local HTTP server + ServiceClient used by the CLI
  hook.py                           spotifymix-hook entry point
  service.py                        wiring, main loop, logging, spotifymix-service entry
  claude_settings.py                add/remove our hooks in Claude Code settings.json
  autostart.py                      Task Scheduler / launchd / systemd user service
  cli.py                            spotifymix command
tests/
  conftest.py                       isolation fixtures
  fakes.py                          FakeKeyring, FakeSpotify, FakeModes, helpers
  test_*.py                         one file per module
```

---

### Task 1: Project scaffold, paths, CI

**Files:**
- Create: `pyproject.toml`, `CLAUDE.md`, `.gitattributes`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- Create: `src/spotifymix/__init__.py`, `src/spotifymix/paths.py`
- Test: `tests/conftest.py`, `tests/test_paths.py`

**Interfaces:**
- Produces: `paths.config_dir() -> Path`, `paths.data_dir() -> Path`, `paths.log_dir() -> Path`, `paths.config_file() -> Path`, `paths.db_file() -> Path`, `paths.token_fallback_file() -> Path`, `paths.service_token_file() -> Path`, `paths.claude_config_dir() -> Path`, `paths.write_private(path: Path, text: str) -> None`, `paths.ensure_service_token() -> str`. Fixture `isolated_home` (autouse).

- [x] **Step 1: Set the git identity and verify it**

```bash
git config user.name "caesla"
git config user.email "219047344+caesla@users.noreply.github.com"
git config user.name && git config user.email
```
Expected: `caesla` and `219047344+caesla@users.noreply.github.com`.

- [x] **Step 2: Create the project files**

`pyproject.toml`:
```toml
[project]
name = "spotifymix"
version = "0.1.0"
description = "Fresh Spotify music that follows what you are doing in Claude Code."
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "keyring>=25",
    "platformdirs>=4",
]

[project.scripts]
spotifymix = "spotifymix.cli:main"
spotifymix-hook = "spotifymix.hook:main"

[project.gui-scripts]
spotifymix-service = "spotifymix.service:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/spotifymix"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

`.gitattributes`:
```
* text=auto eol=lf
*.png binary
```

`CLAUDE.md`:
```markdown
# Working in this repository

- Public repository. Never commit personal data: no emails, no local paths, no tokens, no real hook payloads.
- Git identity must be `caesla <219047344+caesla@users.noreply.github.com>`. Check with `git config user.email` before the first commit of a session.
- Code, comments, CLI output and README in English. The design spec in docs/superpowers/specs is Italian.
- Never use the em dash or en dash as punctuation.
- Run `uv run pytest` and `uv run ruff check` before every commit.
- Tests must not touch the network, the OS keyring, real user directories or real Claude Code settings.
```

`.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.28.0
    hooks:
      - id: gitleaks
```

`.github/workflows/ci.yml`:
```yaml
name: CI
on:
  push:
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync
      - run: uv run ruff check
      - run: uv run pytest -q

  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

`src/spotifymix/__init__.py`:
```python
"""SpotifyMix: Spotify music that follows your Claude Code activity."""

__version__ = "0.1.0"
```

- [x] **Step 3: Write the failing tests**

`tests/conftest.py`:
```python
import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep every test away from real user dirs, real Claude settings and proxies."""
    home = tmp_path / "home"
    monkeypatch.setenv("SPOTIFYMIX_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    return home
```

`tests/test_paths.py`:
```python
from spotifymix import paths


def test_dirs_live_under_override(isolated_home):
    assert paths.config_dir() == isolated_home / "config"
    assert paths.data_dir() == isolated_home / "data"
    assert paths.log_dir() == isolated_home / "logs"
    assert paths.config_dir().is_dir()
    assert paths.config_file() == isolated_home / "config" / "config.toml"
    assert paths.db_file() == isolated_home / "data" / "spotifymix.db"


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
```

- [x] **Step 4: Run tests to verify they fail**

Run: `uv sync && uv run pytest tests/test_paths.py -v`
Expected: FAIL with `ImportError: cannot import name 'paths'`.

- [x] **Step 5: Implement `src/spotifymix/paths.py`**

```python
"""Filesystem locations. Everything personal lives in user directories, never in the repo."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from platformdirs import PlatformDirs

_DIRS = PlatformDirs(appname="SpotifyMix", appauthor=False, roaming=True)


def _home_override() -> Path | None:
    value = os.environ.get("SPOTIFYMIX_HOME")
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
    return data_dir() / "spotifymix.db"


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
```

- [x] **Step 6: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: 4 passed, ruff `All checks passed!`.

- [x] **Step 7: Pin gitleaks to its latest release and commit**

```bash
uv tool install pre-commit
pre-commit autoupdate --repo https://github.com/gitleaks/gitleaks
pre-commit install
git add -A
git commit -m "chore: project scaffold, paths, CI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Expected: the commit runs the gitleaks hook and reports `Passed`. If pre-commit cannot bootstrap gitleaks in this environment, commit anyway and note it in the final report; the CI gitleaks job remains the enforced gate.

---

### Task 2: Configuration and default modes

**Files:**
- Create: `src/spotifymix/defaults.toml`, `src/spotifymix/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `paths.write_private`.
- Produces: dataclasses `ModeConfig(id: str, label: str, genres: tuple[str, ...], fallback: tuple[str, ...])`, `Rule(mode: str, events: tuple[str, ...] = (), tools: tuple[str, ...] = (), permission_mode: str | None = None, skill: str | None = None, command: str | None = None, path: str | None = None, subagent_type: str | None = None, agent_type: str | None = None, prompt: str | None = None, weight: float = 1.0)`, `Config(client_id: str | None, redirect_port: int, device_name: str | None, service_port: int, window_seconds: int, switch_after_seconds: int, queue_lead_seconds: int, exclusion_days: int, recent_poll_seconds: int, saved_poll_seconds: int, manual_default_seconds: int, modes: dict[str, ModeConfig], rules: tuple[Rule, ...])`; `ConfigError(Exception)`; `load_config(user_file: Path | None = None) -> Config`; `save_user_settings(user_file: Path, **spotify_values: str | int | None) -> None`; `dump_toml(data: dict) -> str`.

- [x] **Step 1: Create `src/spotifymix/defaults.toml`**

```toml
# SpotifyMix defaults. Override any value in the user config.toml
# (run `spotifymix status` to see where it lives).

[spotify]
client_id = ""
redirect_port = 47616
device_name = ""

[service]
port = 47615
window_seconds = 180
switch_after_seconds = 180
queue_lead_seconds = 20
exclusion_days = 7
recent_poll_seconds = 600
saved_poll_seconds = 1800
manual_default_seconds = 3600

[modes.planning]
label = "Planning"
genres = ["ambient"]
fallback = ["chill"]

[modes.brainstorming]
label = "Brainstorming"
genres = ["post-rock", "nu jazz"]
fallback = ["jazz"]

[modes.coding]
label = "Coding"
genres = ["deep house", "tech house"]
fallback = ["house"]

[modes.debugging]
label = "Debugging"
genres = ["hip hop", "rap"]
fallback = ["trap"]

[modes.testing]
label = "Testing"
genres = ["techno"]
fallback = ["minimal techno"]

[modes.reviewing]
label = "Code review"
genres = ["lo-fi hip hop"]
fallback = ["chillhop"]

[modes.exploring]
label = "Exploring and research"
genres = ["downtempo", "trip hop"]
fallback = ["chill"]

[modes.writing]
label = "Writing"
genres = ["deep house", "tech house"]
fallback = ["house"]

[modes.ui]
label = "User interfaces"
genres = ["synthwave", "nu disco"]
fallback = ["electronic"]

[modes.release]
label = "Release"
genres = ["funk", "disco"]
fallback = ["soul"]

[modes.orchestrating]
label = "Orchestrating agents"
genres = ["deep house", "tech house"]
fallback = ["house"]

# Classification rules. For each hook event the FIRST matching rule votes.
# All regexes are case-insensitive. Keys: events, tools (glob), permission_mode,
# skill, command, path, subagent_type, agent_type, prompt, weight.

[[rules]]
mode = "debugging"
events = ["PostToolUseFailure"]

[[rules]]
mode = "debugging"
events = ["PreToolUse"]
tools = ["Skill"]
skill = "debug"

[[rules]]
mode = "planning"
permission_mode = "plan"

[[rules]]
mode = "planning"
events = ["PreToolUse"]
tools = ["EnterPlanMode", "ExitPlanMode"]

[[rules]]
mode = "planning"
events = ["PreToolUse"]
tools = ["Skill"]
skill = "plan"

[[rules]]
mode = "brainstorming"
events = ["PreToolUse"]
tools = ["Skill"]
skill = "brainstorm"

[[rules]]
mode = "brainstorming"
events = ["PreToolUse"]
tools = ["AskUserQuestion"]

[[rules]]
mode = "release"
events = ["PreToolUse"]
tools = ["Bash", "PowerShell"]
command = '\bgit\s+(commit|push|tag|merge)\b|\bgh\s+(pr\s+(create|merge)|release)\b|\bdeploy\b'

[[rules]]
mode = "testing"
events = ["PreToolUse"]
tools = ["Bash", "PowerShell"]
command = '\b(pytest|vitest|jest|mocha|playwright\s+test|go\s+test|cargo\s+test|dotnet\s+test|(npm|pnpm|yarn|bun)\s+(run\s+)?test)\b'

[[rules]]
mode = "reviewing"
events = ["PreToolUse"]
tools = ["Skill"]
skill = "review|simplify"

[[rules]]
mode = "reviewing"
events = ["PreToolUse"]
tools = ["Bash", "PowerShell"]
command = '\bgit\s+(diff|show)\b|\bgh\s+pr\s+(diff|view)\b'

[[rules]]
mode = "exploring"
events = ["SubagentStart"]
agent_type = '^explore$'

[[rules]]
mode = "orchestrating"
events = ["SubagentStart"]

[[rules]]
mode = "orchestrating"
events = ["PreToolUse"]
tools = ["Workflow"]

[[rules]]
mode = "ui"
events = ["PreToolUse"]
tools = ["mcp__claude-in-chrome__*", "mcp__*playwright*"]

[[rules]]
mode = "ui"
events = ["PreToolUse"]
tools = ["Edit", "Write", "MultiEdit"]
path = '\.(css|scss|sass|less|html|jsx|tsx|vue|svelte|astro)$'

[[rules]]
mode = "writing"
events = ["PreToolUse"]
tools = ["Edit", "Write", "MultiEdit"]
path = '\.(md|mdx|txt|rst|adoc|docx)$'

[[rules]]
mode = "coding"
events = ["PreToolUse"]
tools = ["Edit", "Write", "MultiEdit", "NotebookEdit"]

[[rules]]
mode = "exploring"
events = ["PreToolUse"]
tools = ["Agent", "Task"]
subagent_type = 'explore'

[[rules]]
mode = "orchestrating"
events = ["PreToolUse"]
tools = ["Agent", "Task"]

[[rules]]
mode = "exploring"
events = ["PreToolUse"]
tools = ["Read", "Grep", "Glob", "WebSearch", "WebFetch", "LSP"]

[[rules]]
mode = "debugging"
events = ["UserPromptSubmit"]
prompt = "\\b(bug|errore|error|non funziona|doesn'?t work|broken|crash|traceback|stack ?trace)\\b"
weight = 0.5

[[rules]]
mode = "planning"
events = ["UserPromptSubmit"]
prompt = "\\b(piano|plan|pianifica|roadmap)\\b"
weight = 0.5

[[rules]]
mode = "brainstorming"
events = ["UserPromptSubmit"]
prompt = "\\b(idea|idee|brainstorm\\w*)\\b"
weight = 0.5

[[rules]]
mode = "reviewing"
events = ["UserPromptSubmit"]
prompt = "\\b(review|revisiona|rivedi)\\b"
weight = 0.5
```

- [x] **Step 2: Write the failing tests**

`tests/test_config.py`:
```python
import tomllib

import pytest

from spotifymix.config import ConfigError, dump_toml, load_config, save_user_settings


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
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.config'`.

- [x] **Step 4: Implement `src/spotifymix/config.py`**

```python
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
```

- [x] **Step 5: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: configuration with default modes, genres and rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Models and SQLite store

**Files:**
- Create: `src/spotifymix/models.py`, `src/spotifymix/store.py`
- Test: `tests/test_models.py`, `tests/test_store.py`

**Interfaces:**
- Produces (models): `make_name_key(name: str, artist: str) -> str`; `parse_iso(ts: str) -> int`; frozen dataclasses `Track(id, uri, name, artists: tuple[str, ...], duration_ms: int)` with property `name_key` and `Track.from_json(d: dict | None) -> Track | None`; `Playback(is_playing: bool, device_id: str | None, device_name: str, device_type: str, item: Track | None, progress_ms: int, context_uri: str | None)` with `Playback.from_json(d: dict) -> Playback`; `Play(track: Track, played_at: int)`; `SavedTrack(track: Track, added_at: int)`; `Device(id: str | None, name: str, type: str, is_active: bool)`.
- Produces (store): `Store(path: str | Path)` with `add_plays(plays: Iterable[Play]) -> int`, `upsert_saved(saved: Iterable[SavedTrack]) -> None`, `add_proposed(track: Track, mode: str, ts: float) -> None`, `is_excluded(track: Track, now: float, days: int) -> bool`, `recent_proposed_ids(limit: int = 100) -> set[str]`, `log_event(ts: float, event: str, mode: str | None) -> None`, `recent_events(limit: int = 10) -> list[tuple[int, str, str | None]]`, `counts(now: float, days: int) -> dict[str, int]`, `prune(now: float, keep_days: int = 8) -> None`, `get_kv(key: str) -> str | None`, `set_kv(key: str, value: str) -> None`, `close() -> None`.

- [x] **Step 1: Write the failing tests**

`tests/test_models.py`:
```python
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
```

`tests/test_store.py`:
```python
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Implement `src/spotifymix/models.py`**

```python
"""Spotify data shapes used across the service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_BRACKETS = re.compile(r"[\(\[].*?[\)\]]")
_DASH_SUFFIX = re.compile(r"\s+-\s+")
_NON_WORD = re.compile(r"[^\w\s]")


def _clean(text: str) -> str:
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


def make_name_key(name: str, artist: str) -> str:
    """Identity of a song across editions: 'Song - Remastered' == 'Song (Live)' == 'Song'."""
    title = _BRACKETS.sub(" ", name.lower())
    title = _DASH_SUFFIX.split(title)[0]
    return f"{_clean(title)}|{_clean(artist)}"


def parse_iso(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


@dataclass(frozen=True)
class Track:
    id: str
    uri: str
    name: str
    artists: tuple[str, ...]
    duration_ms: int

    @property
    def name_key(self) -> str:
        return make_name_key(self.name, self.artists[0] if self.artists else "")

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> Track | None:
        if not data or data.get("type", "track") != "track" or not data.get("id"):
            return None
        track_id = data["id"]
        return cls(
            id=track_id,
            uri=data.get("uri") or f"spotify:track:{track_id}",
            name=data.get("name") or "",
            artists=tuple(a.get("name") or "" for a in data.get("artists") or []),
            duration_ms=int(data.get("duration_ms") or 0),
        )


@dataclass(frozen=True)
class Playback:
    is_playing: bool
    device_id: str | None
    device_name: str
    device_type: str
    item: Track | None
    progress_ms: int
    context_uri: str | None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Playback:
        device = data.get("device") or {}
        is_track = data.get("currently_playing_type", "track") == "track"
        return cls(
            is_playing=bool(data.get("is_playing")),
            device_id=device.get("id"),
            device_name=device.get("name") or "",
            device_type=device.get("type") or "",
            item=Track.from_json(data.get("item")) if is_track else None,
            progress_ms=int(data.get("progress_ms") or 0),
            context_uri=(data.get("context") or {}).get("uri"),
        )


@dataclass(frozen=True)
class Play:
    track: Track
    played_at: int


@dataclass(frozen=True)
class SavedTrack:
    track: Track
    added_at: int


@dataclass(frozen=True)
class Device:
    id: str | None
    name: str
    type: str
    is_active: bool
```

- [x] **Step 4: Implement `src/spotifymix/store.py`**

```python
"""SQLite store: 7-day history of plays, saved tracks and proposals."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from spotifymix.models import Play, SavedTrack, Track

DAY = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
    track_id TEXT NOT NULL, played_at INTEGER NOT NULL, name_key TEXT NOT NULL,
    PRIMARY KEY (track_id, played_at));
CREATE TABLE IF NOT EXISTS saved (
    track_id TEXT PRIMARY KEY, added_at INTEGER NOT NULL, name_key TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposed (
    track_id TEXT NOT NULL, proposed_at INTEGER NOT NULL, name_key TEXT NOT NULL,
    mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (ts INTEGER NOT NULL, event TEXT NOT NULL, mode TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS plays_key ON plays(name_key);
CREATE INDEX IF NOT EXISTS saved_key ON saved(name_key);
CREATE INDEX IF NOT EXISTS proposed_id ON proposed(track_id);
CREATE INDEX IF NOT EXISTS proposed_key ON proposed(name_key);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _write(self, sql: str, rows: Iterable[tuple]) -> int:
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(sql, rows)
            self._conn.commit()
            return self._conn.total_changes - before

    def add_plays(self, plays: Iterable[Play]) -> int:
        rows = [(p.track.id, int(p.played_at), p.track.name_key) for p in plays]
        return self._write("INSERT OR IGNORE INTO plays VALUES (?, ?, ?)", rows)

    def upsert_saved(self, saved: Iterable[SavedTrack]) -> None:
        rows = [(s.track.id, int(s.added_at), s.track.name_key) for s in saved]
        self._write("INSERT OR REPLACE INTO saved VALUES (?, ?, ?)", rows)

    def add_proposed(self, track: Track, mode: str, ts: float) -> None:
        self._write(
            "INSERT INTO proposed VALUES (?, ?, ?, ?)",
            [(track.id, int(ts), track.name_key, mode)],
        )

    def is_excluded(self, track: Track, now: float, days: int) -> bool:
        cutoff = int(now - days * DAY)
        args = (cutoff, track.id, track.name_key)
        sql = (
            "SELECT 1 FROM plays WHERE played_at >= ? AND (track_id = ? OR name_key = ?) "
            "UNION ALL SELECT 1 FROM saved WHERE added_at >= ? AND (track_id = ? OR name_key = ?) "
            "UNION ALL SELECT 1 FROM proposed WHERE proposed_at >= ? "
            "AND (track_id = ? OR name_key = ?) LIMIT 1"
        )
        with self._lock:
            return self._conn.execute(sql, args * 3).fetchone() is not None

    def recent_proposed_ids(self, limit: int = 100) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_id FROM proposed ORDER BY proposed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return {r[0] for r in rows}

    def log_event(self, ts: float, event: str, mode: str | None) -> None:
        self._write("INSERT INTO events VALUES (?, ?, ?)", [(int(ts), event, mode)])

    def recent_events(self, limit: int = 10) -> list[tuple[int, str, str | None]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, event, mode FROM events ORDER BY ts DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [tuple(r) for r in rows]

    def counts(self, now: float, days: int) -> dict[str, int]:
        cutoff = int(now - days * DAY)
        with self._lock:
            plays = self._conn.execute(
                "SELECT COUNT(*) FROM plays WHERE played_at >= ?", (cutoff,)
            ).fetchone()[0]
            saved = self._conn.execute(
                "SELECT COUNT(*) FROM saved WHERE added_at >= ?", (cutoff,)
            ).fetchone()[0]
            proposed = self._conn.execute(
                "SELECT COUNT(*) FROM proposed WHERE proposed_at >= ?", (cutoff,)
            ).fetchone()[0]
        return {"plays": plays, "saved": saved, "proposed": proposed}

    def prune(self, now: float, keep_days: int = 8) -> None:
        cutoff = int(now - keep_days * DAY)
        with self._lock:
            self._conn.execute("DELETE FROM plays WHERE played_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM saved WHERE added_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM proposed WHERE proposed_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def get_kv(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self._write("INSERT OR REPLACE INTO kv VALUES (?, ?)", [(key, value)])
```

- [x] **Step 5: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: track models and 7-day SQLite store

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Classifier

**Files:**
- Create: `src/spotifymix/classifier.py`
- Test: `tests/test_classifier.py`

**Interfaces:**
- Consumes: `config.Rule`, `config.load_config`.
- Produces: `Classifier(rules: Sequence[Rule], window_seconds: float, switch_after_seconds: float)` with `classify(payload: dict) -> tuple[str, float] | None`, `observe(payload: dict, now: float) -> str | None`, `leader(now: float) -> str | None`, `stable_mode(now: float) -> str | None`, `set_manual(mode: str, until: float) -> None`, `clear_manual() -> None`, `snapshot(now: float) -> dict`. `snapshot` keys: `stable`, `leader`, `candidate`, `candidate_since`, `manual`, `manual_until`.

- [x] **Step 1: Write the failing tests**

`tests/test_classifier.py`:
```python
import pytest

from spotifymix.classifier import Classifier
from spotifymix.config import load_config


@pytest.fixture
def clf():
    return Classifier(load_config().rules, window_seconds=180, switch_after_seconds=180)


def pre(tool, session="s1", **tool_input):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
            "session_id": session, "permission_mode": "default"}


@pytest.mark.parametrize("payload, mode", [
    ({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}, "debugging"),
    (pre("Skill", skill="superpowers:systematic-debugging"), "debugging"),
    ({**pre("Read", file_path="a.py"), "permission_mode": "plan"}, "planning"),
    (pre("EnterPlanMode"), "planning"),
    (pre("Skill", skill="superpowers:writing-plans"), "planning"),
    (pre("Skill", skill="superpowers:brainstorming"), "brainstorming"),
    (pre("AskUserQuestion"), "brainstorming"),
    (pre("Bash", command="git push origin main"), "release"),
    (pre("PowerShell", command="gh pr create --fill"), "release"),
    (pre("Bash", command="uv run pytest -q"), "testing"),
    (pre("Bash", command="pnpm run test"), "testing"),
    (pre("Skill", skill="code-review"), "reviewing"),
    (pre("Bash", command="git diff HEAD~1"), "reviewing"),
    ({"hook_event_name": "SubagentStart", "agent_type": "Explore"}, "exploring"),
    ({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}, "orchestrating"),
    (pre("Workflow"), "orchestrating"),
    (pre("mcp__claude-in-chrome__navigate"), "ui"),
    (pre("Edit", file_path="src/App.tsx"), "ui"),
    (pre("Write", file_path="docs/NOTES.md"), "writing"),
    (pre("Edit", file_path="src/app.py"), "coding"),
    (pre("Agent", subagent_type="Explore"), "exploring"),
    (pre("Agent", subagent_type="general-purpose"), "orchestrating"),
    (pre("Grep", pattern="x"), "exploring"),
    (pre("WebSearch", query="x"), "exploring"),
])
def test_classify_rules(clf, payload, mode):
    assert clf.classify(payload)[0] == mode


@pytest.mark.parametrize("payload", [
    {"hook_event_name": "Notification", "notification_type": "idle_prompt"},
    {"hook_event_name": "SessionEnd", "reason": "other"},
    {"hook_event_name": "Stop"},
    {"hook_event_name": "UserPromptSubmit", "prompt": "ciao, come va?"},
    {},
])
def test_signals_without_vote(clf, payload):
    assert clf.classify(payload) is None


def test_edit_with_string_tool_input_still_counts_as_coding(clf):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": "x"}
    assert clf.classify(payload) == ("coding", 1.0)


def test_prompt_keywords_are_a_weak_vote(clf):
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "C'è un ERRORE nel login"}
    assert clf.classify(payload) == ("debugging", 0.5)


def test_first_leader_is_adopted_immediately(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    assert clf.stable_mode(0) == "coding"


def test_switch_needs_three_minutes_of_leadership(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    assert clf.stable_mode(0) == "coding"
    for second in range(200, 380, 10):
        clf.observe({"hook_event_name": "PostToolUseFailure"}, now=second)
        clf.stable_mode(second)
    # coding vote expired at 180; debugging leads since 200; switch at 380.
    assert clf.stable_mode(370) == "coding"
    assert clf.stable_mode(380) == "debugging"


def test_interrupted_leadership_resets_the_timer(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    clf.stable_mode(0)
    clf.observe({"hook_event_name": "PostToolUseFailure"}, now=190)
    assert clf.stable_mode(190) == "coding"
    for second in range(200, 300, 5):
        clf.observe(pre("Edit", file_path="a.py"), now=second)
        clf.observe(pre("Edit", file_path="b.py"), now=second)
    assert clf.stable_mode(300) == "coding"
    assert clf.snapshot(300)["candidate"] is None


def test_empty_window_keeps_the_stable_mode(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    clf.stable_mode(0)
    assert clf.stable_mode(10_000) == "coding"
    assert clf.leader(10_000) is None


def test_votes_from_all_sessions_are_summed(clf):
    clf.observe(pre("Edit", session="a", file_path="a.py"), now=0)
    clf.observe(pre("Bash", session="b", command="pytest"), now=1)
    clf.observe(pre("Bash", session="c", command="pytest"), now=2)
    assert clf.leader(3) == "testing"


def test_manual_mode_wins_until_it_expires(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    clf.set_manual("release", until=100)
    assert clf.stable_mode(50) == "release"
    assert clf.stable_mode(101) == "coding"
    clf.set_manual("release", until=1000)
    clf.clear_manual()
    assert clf.stable_mode(102) == "coding"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.classifier'`.

- [x] **Step 3: Implement `src/spotifymix/classifier.py`**

```python
"""Turns Claude Code hook payloads into a stable activity mode."""

from __future__ import annotations

import fnmatch
import re
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from spotifymix.config import REGEX_KEYS, Rule


@dataclass(frozen=True)
class Vote:
    ts: float
    mode: str
    weight: float


def _field(payload: dict[str, Any], name: str) -> str:
    tool_input = payload.get("tool_input")
    ti = tool_input if isinstance(tool_input, dict) else {}
    if name == "path":
        return str(ti.get("file_path") or ti.get("notebook_path") or "")
    if name in ("agent_type", "prompt"):
        return str(payload.get(name) or "")
    return str(ti.get(name) or "")


class Classifier:
    def __init__(
        self, rules: Sequence[Rule], window_seconds: float, switch_after_seconds: float
    ) -> None:
        self._rules = [
            (rule, {k: re.compile(getattr(rule, k), re.IGNORECASE)
                    for k in REGEX_KEYS if getattr(rule, k) is not None})
            for rule in rules
        ]
        self._window = window_seconds
        self._switch_after = switch_after_seconds
        self._votes: deque[Vote] = deque()
        self._stable: str | None = None
        self._candidate: str | None = None
        self._candidate_since: float | None = None
        self._manual: str | None = None
        self._manual_until = 0.0
        self._lock = threading.Lock()

    def classify(self, payload: dict[str, Any]) -> tuple[str, float] | None:
        for rule, regexes in self._rules:
            if self._matches(rule, regexes, payload):
                return rule.mode, rule.weight
        return None

    @staticmethod
    def _matches(rule: Rule, regexes: dict[str, re.Pattern[str]], payload: dict[str, Any]) -> bool:
        if rule.events and payload.get("hook_event_name") not in rule.events:
            return False
        if rule.tools:
            tool = str(payload.get("tool_name") or "")
            if not any(fnmatch.fnmatchcase(tool, pattern) for pattern in rule.tools):
                return False
        if rule.permission_mode and payload.get("permission_mode") != rule.permission_mode:
            return False
        return all(rx.search(_field(payload, name)) for name, rx in regexes.items())

    def observe(self, payload: dict[str, Any], now: float) -> str | None:
        result = self.classify(payload)
        if result is None:
            return None
        mode, weight = result
        with self._lock:
            self._votes.append(Vote(now, mode, weight))
        self.stable_mode(now)
        return mode

    def _prune(self, now: float) -> None:
        while self._votes and self._votes[0].ts < now - self._window:
            self._votes.popleft()

    def _leader_locked(self, now: float) -> str | None:
        self._prune(now)
        totals: dict[str, float] = {}
        latest: dict[str, float] = {}
        for vote in self._votes:
            totals[vote.mode] = totals.get(vote.mode, 0.0) + vote.weight
            latest[vote.mode] = vote.ts
        if not totals:
            return None
        return max(totals, key=lambda m: (totals[m], latest[m]))

    def leader(self, now: float) -> str | None:
        with self._lock:
            return self._leader_locked(now)

    def stable_mode(self, now: float) -> str | None:
        with self._lock:
            if self._manual is not None and now < self._manual_until:
                return self._manual
            lead = self._leader_locked(now)
            if lead is None or lead == self._stable:
                self._candidate = None
                self._candidate_since = None
            elif self._stable is None:
                self._stable = lead
            elif self._candidate != lead:
                self._candidate = lead
                self._candidate_since = now
            elif now - (self._candidate_since or now) >= self._switch_after:
                self._stable = lead
                self._candidate = None
                self._candidate_since = None
            return self._stable

    def set_manual(self, mode: str, until: float) -> None:
        with self._lock:
            self._manual = mode
            self._manual_until = until

    def clear_manual(self) -> None:
        with self._lock:
            self._manual = None
            self._manual_until = 0.0

    def snapshot(self, now: float) -> dict[str, Any]:
        stable = self.stable_mode(now)
        with self._lock:
            manual_active = self._manual is not None and now < self._manual_until
            return {
                "stable": stable,
                "leader": self._leader_locked(now),
                "candidate": self._candidate,
                "candidate_since": self._candidate_since,
                "manual": self._manual if manual_active else None,
                "manual_until": self._manual_until if manual_active else None,
            }
```

- [x] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed. If `test_switch_needs_three_minutes_of_leadership` fails, check that `stable_mode` is called on every observed event (it is, from `observe`) and that the candidate timer starts at the first evaluation where debugging leads (second 200).

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: rule-based activity classifier with stable mode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Spotify login (PKCE) and token storage

**Files:**
- Create: `src/spotifymix/spotify/__init__.py` (empty), `src/spotifymix/spotify/auth.py`
- Create: `tests/fakes.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `paths.write_private`.
- Produces: `SCOPES: tuple[str, ...]`; `AuthError(Exception)`; `LoginRequired(AuthError)`; `TokenSet(access_token: str, expires_at: float, refresh_token: str)`; `make_verifier() -> str`; `make_challenge(verifier: str) -> str`; `redirect_uri(port: int) -> str`; `build_authorize_url(client_id: str, redirect: str, challenge: str, state: str) -> str`; `exchange_code(http: httpx.Client, client_id: str, code: str, redirect: str, verifier: str, now: float) -> TokenSet`; `refresh_access(http: httpx.Client, client_id: str, refresh_token: str, now: float) -> TokenSet`; `TokenStore(fallback_file: Path, backend: Any = "default")` with `load() -> str | None`, `save(refresh_token: str) -> str` (returns `"keyring"` or `"file"`), `clear() -> None`; `login_interactive(http, client_id, port, store, open_browser=webbrowser.open, timeout=300.0, clock=time.time) -> TokenSet`.
- Produces (tests/fakes.py): `FakeKeyring(broken: bool = False)`, `free_port() -> int`.

- [x] **Step 1: Create `tests/fakes.py`**

```python
"""Test doubles shared by several test modules. Never touches network or OS keyring."""

from __future__ import annotations

import socket


class FakeKeyring:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[tuple[str, str], str] = {}
        self.broken = broken

    def _check(self) -> None:
        if self.broken:
            raise RuntimeError("no keyring backend available")

    def get_password(self, service: str, user: str) -> str | None:
        self._check()
        return self.data.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self._check()
        self.data[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self._check()
        self.data.pop((service, user), None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
```

- [x] **Step 2: Write the failing tests**

`tests/test_auth.py`:
```python
import re
import threading
import urllib.request
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fakes import FakeKeyring, free_port

from spotifymix.spotify import auth


def token_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pkce_challenge_matches_rfc7636_example():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert auth.make_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_verifier_length_and_charset():
    verifier = auth.make_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)


def test_authorize_url_contains_pkce_and_scopes():
    url = auth.build_authorize_url("cid", auth.redirect_uri(47616), "chal", "st")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["cid"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == ["chal"]
    assert query["state"] == ["st"]
    assert query["redirect_uri"] == ["http://127.0.0.1:47616/callback"]
    assert set(auth.SCOPES) == set(query["scope"][0].split())


def test_refresh_keeps_old_refresh_token_when_not_rotated():
    seen = {}

    def handler(request):
        seen.update(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "A", "expires_in": 3600})

    tokens = auth.refresh_access(token_client(handler), "cid", "R1", now=100)
    assert tokens == auth.TokenSet("A", 3700, "R1")
    assert seen["grant_type"] == ["refresh_token"]
    assert seen["client_id"] == ["cid"]
    assert "client_secret" not in seen


def test_refresh_returns_rotated_token():
    client = token_client(lambda r: httpx.Response(
        200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R2"}))
    assert auth.refresh_access(client, "cid", "R1", now=0).refresh_token == "R2"


def test_invalid_grant_raises_login_required():
    client = token_client(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(auth.LoginRequired):
        auth.refresh_access(client, "cid", "R1", now=0)


def test_token_endpoint_down_raises_auth_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(auth.AuthError) as info:
        auth.refresh_access(token_client(handler), "cid", "R1", now=0)
    assert not isinstance(info.value, auth.LoginRequired)


def test_token_store_prefers_keyring(tmp_path):
    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring())
    assert store.save("R") == "keyring"
    assert store.load() == "R"
    assert not (tmp_path / "t.json").exists()


def test_token_store_falls_back_to_file(tmp_path):
    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring(broken=True))
    assert store.save("R") == "file"
    assert store.load() == "R"
    store.clear()
    assert store.load() is None


def _browser_that_calls_back(port, code="the-code", state=None):
    def open_browser(url):
        real_state = parse_qs(urlparse(url).query)["state"][0]
        target = (f"http://127.0.0.1:{port}/callback?code={code}"
                  f"&state={state or real_state}")
        threading.Thread(
            target=lambda: urllib.request.urlopen(target, timeout=5).read(), daemon=True
        ).start()
        return True

    return open_browser


def test_interactive_login_saves_refresh_token(tmp_path):
    port = free_port()

    def handler(request):
        body = parse_qs(request.content.decode())
        assert body["code"] == ["the-code"]
        assert body["code_verifier"][0]
        assert body["redirect_uri"] == [f"http://127.0.0.1:{port}/callback"]
        return httpx.Response(
            200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R"})

    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring())
    tokens = auth.login_interactive(
        token_client(handler), "cid", port, store,
        open_browser=_browser_that_calls_back(port), timeout=10,
    )
    assert tokens.refresh_token == "R"
    assert store.load() == "R"


def test_interactive_login_rejects_wrong_state(tmp_path):
    port = free_port()
    store = auth.TokenStore(tmp_path / "t.json", backend=FakeKeyring())
    with pytest.raises(auth.AuthError, match="state"):
        auth.login_interactive(
            token_client(lambda r: httpx.Response(500)), "cid", port, store,
            open_browser=_browser_that_calls_back(port, state="forged"), timeout=10,
        )
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.spotify'`.

- [x] **Step 4: Implement `src/spotifymix/spotify/auth.py`** (and create an empty `src/spotifymix/spotify/__init__.py`)

```python
"""Spotify login with Authorization Code + PKCE. No client secret exists anywhere."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from spotifymix.paths import write_private

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = (
    "user-read-recently-played",
    "user-library-read",
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-modify-playback-state",
)
KEYRING_SERVICE = "spotifymix"
KEYRING_USER = "refresh_token"


class AuthError(Exception):
    pass


class LoginRequired(AuthError):
    pass


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    expires_at: float
    refresh_token: str


def make_verifier() -> str:
    return secrets.token_urlsafe(96)[:128]


def make_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def build_authorize_url(client_id: str, redirect: str, challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _post_token(
    http: httpx.Client, data: dict[str, str], now: float, previous_refresh: str | None = None
) -> TokenSet:
    try:
        response = http.post(TOKEN_URL, data=data, timeout=15)
    except httpx.HTTPError as exc:
        raise AuthError(f"Spotify accounts service unreachable: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 400 and body.get("error") == "invalid_grant":
        raise LoginRequired("Spotify login expired or revoked; run `spotifymix login`")
    if response.status_code != 200 or "access_token" not in body:
        raise AuthError(f"Spotify token request failed (HTTP {response.status_code})")
    return TokenSet(
        access_token=body["access_token"],
        expires_at=now + float(body.get("expires_in", 3600)),
        refresh_token=body.get("refresh_token") or previous_refresh or "",
    )


def exchange_code(
    http: httpx.Client, client_id: str, code: str, redirect: str, verifier: str, now: float
) -> TokenSet:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    return _post_token(http, data, now)


def refresh_access(http: httpx.Client, client_id: str, refresh_token: str, now: float) -> TokenSet:
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return _post_token(http, data, now, previous_refresh=refresh_token)


class TokenStore:
    """Refresh token in the OS keyring; private file only when no keyring is available."""

    def __init__(self, fallback_file: Path, backend: Any = "default") -> None:
        if backend == "default":
            import keyring

            backend = keyring
        self._backend = backend
        self._file = fallback_file

    # Keyring backends raise many unrelated exception types, hence the broad catches.
    def load(self) -> str | None:
        if self._backend is not None:
            try:
                value = self._backend.get_password(KEYRING_SERVICE, KEYRING_USER)
                if value:
                    return value
            except Exception:
                pass
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8")).get("refresh_token")
            except (OSError, ValueError):
                return None
        return None

    def save(self, refresh_token: str) -> str:
        if self._backend is not None:
            try:
                self._backend.set_password(KEYRING_SERVICE, KEYRING_USER, refresh_token)
                self._file.unlink(missing_ok=True)
                return "keyring"
            except Exception:
                pass
        write_private(self._file, json.dumps({"refresh_token": refresh_token}))
        return "file"

    def clear(self) -> None:
        if self._backend is not None:
            try:
                self._backend.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass
        self._file.unlink(missing_ok=True)


_DONE_PAGE = b"<h1>SpotifyMix: login complete. You can close this tab.</h1>"
_FAILED_PAGE = b"<h1>SpotifyMix: login failed. Go back to the terminal.</h1>"


def login_interactive(
    http: httpx.Client,
    client_id: str,
    port: int,
    store: TokenStore,
    open_browser: Callable[[str], Any] = webbrowser.open,
    timeout: float = 300.0,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    verifier = make_verifier()
    state = secrets.token_urlsafe(16)
    redirect = redirect_uri(port)
    result: dict[str, str] = {}
    done = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
            ok = result.get("state") == state and "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE if ok else _FAILED_PAGE)
            done.set()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        open_browser(build_authorize_url(client_id, redirect, make_challenge(verifier), state))
        if not done.wait(timeout):
            raise AuthError("timed out waiting for the Spotify login")
    finally:
        server.shutdown()
        server.server_close()
    if result.get("state") != state:
        raise AuthError("state mismatch: login aborted")
    if "error" in result or "code" not in result:
        raise AuthError(f"Spotify refused the login: {result.get('error', 'no code')}")
    tokens = exchange_code(http, client_id, result["code"], redirect, verifier, clock())
    store.save(tokens.refresh_token)
    return tokens
```

- [x] **Step 5: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: Spotify PKCE login and keyring token storage

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Spotify Web API client

**Files:**
- Create: `src/spotifymix/spotify/client.py`
- Modify: `tests/fakes.py` (add `track_json`, `FakeApi`, `make_client`; keep all imports at the top of the file)
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `auth.TokenStore`, `auth.refresh_access`, `auth.AuthError`, `auth.LoginRequired`, models from Task 3.
- Produces: `SpotifyError(Exception)`, `RateLimited(SpotifyError)` with attribute `retry_after: float`, `SpotifyUnavailable(SpotifyError)`; `SpotifyClient(http: httpx.Client, client_id: str, tokens: TokenStore, clock: Callable[[], float] = time.time)` with `get_playback() -> Playback | None`, `devices() -> list[Device]`, `add_to_queue(uri: str, device_id: str | None = None) -> None`, `search_tracks(query: str, offset: int = 0, limit: int = 10) -> tuple[list[Track], int]`, `recently_played(limit: int = 50) -> list[Play]`, `saved_tracks(offset: int = 0, limit: int = 50) -> tuple[list[SavedTrack], bool]`.

- [x] **Step 1: Extend `tests/fakes.py`**

Add these imports at the top of the file (after `import socket`):
```python
import httpx

from spotifymix import paths
from spotifymix.spotify.auth import TokenStore
```

Append at the end of the file:
```python
def track_json(track_id="t1", name=None, artist=None, **extra):
    data = {
        "type": "track", "id": track_id, "uri": f"spotify:track:{track_id}",
        "name": name or f"Song {track_id}", "artists": [{"name": artist or f"Artist {track_id}"}],
        "duration_ms": 200000,
    }
    data.update(extra)
    return data


class FakeApi:
    """httpx MockTransport handler emulating the Spotify endpoints SpotifyMix uses."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list] = {}
        self.calls: list[httpx.Request] = []
        self.token_responses: list = [
            httpx.Response(200, json={"access_token": "A1", "expires_in": 3600})
        ]

    def on(self, method: str, path: str, *responses) -> None:
        self.routes[(method, path)] = list(responses)

    @staticmethod
    def _next(queue: list):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "accounts.spotify.com":
            return self._next(self.token_responses)
        queue = self.routes.get((request.method, request.url.path.removeprefix("/v1")))
        if not queue:
            return httpx.Response(404, json={"error": "no fake route"})
        return self._next(queue)

    def token_calls(self) -> list[httpx.Request]:
        return [c for c in self.calls if c.url.host == "accounts.spotify.com"]

    def api_calls(self, path: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.url.path == "/v1" + path]


def make_client(api: FakeApi, refresh: str | None = "R1", clock=lambda: 1000.0):
    from spotifymix.spotify.client import SpotifyClient

    tokens = TokenStore(paths.token_fallback_file(), backend=FakeKeyring())
    if refresh:
        tokens.save(refresh)
    http = httpx.Client(transport=httpx.MockTransport(api))
    return SpotifyClient(http, "cid", tokens, clock=clock), tokens
```

- [x] **Step 2: Write the failing tests**

`tests/test_client.py`:
```python
import httpx
import pytest
from fakes import FakeApi, make_client, track_json

from spotifymix.spotify.auth import LoginRequired
from spotifymix.spotify.client import RateLimited, SpotifyUnavailable


def test_access_token_is_refreshed_once_and_reused():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(204))
    client, _ = make_client(api)
    assert client.get_playback() is None
    assert client.get_playback() is None
    assert len(api.token_calls()) == 1


def test_expired_token_is_refreshed_again():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(204))
    now = [1000.0]
    client, _ = make_client(api, clock=lambda: now[0])
    client.get_playback()
    now[0] += 3600
    client.get_playback()
    assert len(api.token_calls()) == 2


def test_401_triggers_refresh_and_retry():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(401), httpx.Response(204))
    client, _ = make_client(api)
    assert client.get_playback() is None
    assert len(api.token_calls()) == 2
    assert len(api.api_calls("/me/player")) == 2


def test_429_raises_rate_limited():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(429, headers={"Retry-After": "12"}))
    client, _ = make_client(api)
    with pytest.raises(RateLimited) as info:
        client.get_playback()
    assert info.value.retry_after == 12


def test_429_with_unreadable_retry_after_defaults_to_30():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(429, headers={"Retry-After": "soon"}))
    client, _ = make_client(api)
    with pytest.raises(RateLimited) as info:
        client.get_playback()
    assert info.value.retry_after == 30


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.ConnectError("offline")])
def test_server_and_network_errors_raise_unavailable(response):
    api = FakeApi()
    api.on("GET", "/me/player", response)
    client, _ = make_client(api)
    with pytest.raises(SpotifyUnavailable):
        client.get_playback()


def test_missing_login_raises_login_required():
    client, _ = make_client(FakeApi(), refresh=None)
    with pytest.raises(LoginRequired):
        client.get_playback()


def test_invalid_grant_raises_login_required():
    api = FakeApi()
    api.token_responses = [httpx.Response(400, json={"error": "invalid_grant"})]
    client, _ = make_client(api)
    with pytest.raises(LoginRequired):
        client.get_playback()


def test_rotated_refresh_token_is_saved():
    api = FakeApi()
    api.token_responses = [httpx.Response(
        200, json={"access_token": "A", "expires_in": 3600, "refresh_token": "R2"})]
    api.on("GET", "/me/player", httpx.Response(204))
    client, tokens = make_client(api)
    client.get_playback()
    assert tokens.load() == "R2"


def test_get_playback_parses_track():
    api = FakeApi()
    api.on("GET", "/me/player", httpx.Response(200, json={
        "is_playing": True, "progress_ms": 5000, "currently_playing_type": "track",
        "device": {"id": "d1", "name": "MY-PC", "type": "Computer"}, "item": track_json("x"),
    }))
    client, _ = make_client(api)
    playback = client.get_playback()
    assert playback.item.id == "x" and playback.device_name == "MY-PC"
    assert api.api_calls("/me/player")[0].headers["Authorization"] == "Bearer A1"


def test_search_filters_unplayable_and_returns_total():
    api = FakeApi()
    api.on("GET", "/search", httpx.Response(200, json={"tracks": {
        "total": 321,
        "items": [track_json("ok"), track_json("blocked", is_playable=False), None],
    }}))
    client, _ = make_client(api)
    tracks, total = client.search_tracks('genre:"techno"', offset=20)
    assert [t.id for t in tracks] == ["ok"]
    assert total == 321
    params = api.api_calls("/search")[0].url.params
    assert params["q"] == 'genre:"techno"'
    assert params["type"] == "track"
    assert params["limit"] == "10"
    assert params["offset"] == "20"
    assert params["market"] == "from_token"


def test_recently_played_and_saved_tracks_parse():
    api = FakeApi()
    api.on("GET", "/me/player/recently-played", httpx.Response(200, json={
        "items": [{"track": track_json("a"), "played_at": "1970-01-01T00:01:00Z"}]}))
    api.on("GET", "/me/tracks", httpx.Response(200, json={
        "items": [{"track": track_json("s"), "added_at": "1970-01-01T00:02:00Z"}],
        "next": "https://api.spotify.com/v1/me/tracks?offset=50"}))
    client, _ = make_client(api)
    plays = client.recently_played()
    assert plays[0].track.id == "a" and plays[0].played_at == 60
    saved, has_next = client.saved_tracks()
    assert saved[0].added_at == 120 and has_next is True


def test_add_to_queue_sends_uri_and_device():
    api = FakeApi()
    api.on("POST", "/me/player/queue", httpx.Response(204))
    client, _ = make_client(api)
    client.add_to_queue("spotify:track:x", "d1")
    params = api.api_calls("/me/player/queue")[0].url.params
    assert params["uri"] == "spotify:track:x" and params["device_id"] == "d1"


def test_devices():
    api = FakeApi()
    api.on("GET", "/me/player/devices", httpx.Response(200, json={"devices": [
        {"id": "d", "name": "MY-PC", "type": "Computer", "is_active": True}]}))
    client, _ = make_client(api)
    assert client.devices()[0].name == "MY-PC"
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.spotify.client'`.

- [x] **Step 4: Implement `src/spotifymix/spotify/client.py`**

```python
"""Thin Spotify Web API wrapper: token refresh, rate limits and error mapping."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from spotifymix.models import Device, Play, Playback, SavedTrack, Track, parse_iso
from spotifymix.spotify.auth import AuthError, LoginRequired, TokenStore, refresh_access

API_BASE = "https://api.spotify.com/v1"


class SpotifyError(Exception):
    pass


class RateLimited(SpotifyError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Spotify rate limit: retry in {retry_after:.0f}s")
        self.retry_after = retry_after


class SpotifyUnavailable(SpotifyError):
    pass


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("Retry-After", "30"))
    except ValueError:
        return 30.0


class SpotifyClient:
    def __init__(
        self,
        http: httpx.Client,
        client_id: str,
        tokens: TokenStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._tokens = tokens
        self._clock = clock
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _token(self, force: bool = False) -> str:
        with self._lock:
            now = self._clock()
            if not force and self._access and now < self._expires_at - 60:
                return self._access
            refresh = self._tokens.load()
            if not refresh:
                raise LoginRequired("no Spotify login stored; run `spotifymix login`")
            try:
                tokens = refresh_access(self._http, self._client_id, refresh, now)
            except LoginRequired:
                raise
            except AuthError as exc:
                raise SpotifyUnavailable(str(exc)) from exc
            if tokens.refresh_token and tokens.refresh_token != refresh:
                self._tokens.save(tokens.refresh_token)
            self._access, self._expires_at = tokens.access_token, tokens.expires_at
            return self._access

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in (1, 2):
            token = self._token(force=attempt == 2)
            try:
                response = self._http.request(
                    method, API_BASE + path, params=params,
                    headers={"Authorization": f"Bearer {token}"}, timeout=15,
                )
            except httpx.HTTPError as exc:
                raise SpotifyUnavailable(f"Spotify unreachable: {exc}") from exc
            if response.status_code == 401 and attempt == 1:
                continue
            if response.status_code == 429:
                raise RateLimited(_retry_after(response))
            if response.status_code >= 500:
                raise SpotifyUnavailable(f"Spotify returned HTTP {response.status_code}")
            if response.status_code >= 400:
                raise SpotifyError(f"Spotify returned HTTP {response.status_code} for {path}")
            if response.status_code in (202, 204) or not response.content:
                return None
            return response.json()
        raise LoginRequired("Spotify rejected a fresh access token; run `spotifymix login`")

    def get_playback(self) -> Playback | None:
        data = self._request("GET", "/me/player", {"additional_types": "episode"})
        return Playback.from_json(data) if data else None

    def devices(self) -> list[Device]:
        data = self._request("GET", "/me/player/devices") or {}
        return [
            Device(d.get("id"), d.get("name") or "", d.get("type") or "", bool(d.get("is_active")))
            for d in data.get("devices") or []
        ]

    def add_to_queue(self, uri: str, device_id: str | None = None) -> None:
        params = {"uri": uri}
        if device_id:
            params["device_id"] = device_id
        self._request("POST", "/me/player/queue", params)

    def search_tracks(
        self, query: str, offset: int = 0, limit: int = 10
    ) -> tuple[list[Track], int]:
        params = {"q": query, "type": "track", "limit": limit, "offset": offset,
                  "market": "from_token"}
        block = (self._request("GET", "/search", params) or {}).get("tracks") or {}
        tracks = []
        for raw in block.get("items") or []:
            if raw and raw.get("is_playable", True):
                track = Track.from_json(raw)
                if track is not None:
                    tracks.append(track)
        return tracks, int(block.get("total") or 0)

    def recently_played(self, limit: int = 50) -> list[Play]:
        data = self._request("GET", "/me/player/recently-played", {"limit": limit}) or {}
        plays = []
        for item in data.get("items") or []:
            track = Track.from_json(item.get("track"))
            if track is not None and item.get("played_at"):
                plays.append(Play(track, parse_iso(item["played_at"])))
        return plays

    def saved_tracks(self, offset: int = 0, limit: int = 50) -> tuple[list[SavedTrack], bool]:
        data = self._request("GET", "/me/tracks", {"offset": offset, "limit": limit}) or {}
        saved = []
        for item in data.get("items") or []:
            track = Track.from_json(item.get("track"))
            if track is not None and item.get("added_at"):
                saved.append(SavedTrack(track, parse_iso(item["added_at"])))
        return saved, bool(data.get("next"))
```

- [x] **Step 5: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: Spotify Web API client with refresh and rate-limit handling

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Recorder (listening history and saved tracks)

**Files:**
- Create: `src/spotifymix/recorder.py`
- Modify: `tests/fakes.py` (add `make_track`, `playing`, `FakeSpotify`, `FakeModes`, `FakeFinder`)
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `Store` (Task 3), client exceptions (Task 6), `auth.AuthError`.
- Produces: `Recorder(client, store: Store, recent_every: float, saved_every: float, exclusion_days: int)` with `maybe_run(now: float) -> None`, `sync_recent(now: float) -> int`, `sync_saved(now: float, max_pages: int = 20) -> int`, attribute `last_error: str | None`.
- Produces (fakes): `make_track(i: int, prefix: str = "t") -> Track`; `playing(track, progress_ms, *, is_playing=True, device_type="Computer", device_name="MY-PC", context=None) -> Playback`; `FakeSpotify` (attributes `playback`, `queued`, `search_pool: dict[str, list[Track]]`, `search_calls`, `recent`, `recent_calls`, `saved_pages`, `saved_calls`, `error`, `queue_error`; same public methods as `SpotifyClient`); `FakeModes(mode)` with `mode` attribute and `stable_mode(now)`; `FakeFinder` with `pools: dict[str, list[Track]]`, `calls: int`, `last_note`, `next_track(mode, now)`.

- [x] **Step 1: Extend `tests/fakes.py`**

Add these imports at the top of the file:
```python
import re

from spotifymix.models import Playback, Track
```

Append at the end of the file:
```python
def make_track(i: int, prefix: str = "t") -> Track:
    track_id = f"{prefix}{i}"
    return Track(track_id, f"spotify:track:{track_id}", f"Song {track_id}",
                 (f"Artist {track_id}",), 200000)


def playing(track, progress_ms, *, is_playing=True, device_type="Computer",
            device_name="MY-PC", context=None) -> Playback:
    return Playback(is_playing, "dev1", device_name, device_type, track, progress_ms, context)


class FakeSpotify:
    """In-memory stand-in for SpotifyClient."""

    def __init__(self) -> None:
        self.playback: Playback | None = None
        self.queued: list[tuple[str, str | None]] = []
        self.search_pool: dict[str, list[Track]] = {}
        self.search_calls: list[tuple[str, int, int]] = []
        self.recent: list = []
        self.recent_calls = 0
        self.saved_pages: list[tuple[list, bool]] = []
        self.saved_calls: list[int] = []
        self.error: Exception | None = None
        self.queue_error: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    def get_playback(self):
        self._maybe_fail()
        return self.playback

    def devices(self):
        self._maybe_fail()
        return []

    def add_to_queue(self, uri, device_id=None):
        self._maybe_fail()
        if self.queue_error is not None:
            raise self.queue_error
        self.queued.append((uri, device_id))

    def search_tracks(self, query, offset=0, limit=10):
        self._maybe_fail()
        self.search_calls.append((query, offset, limit))
        genre = re.search(r'genre:"([^"]+)"', query).group(1)
        pool = self.search_pool.get(genre, [])
        return pool[offset:offset + limit], len(pool)

    def recently_played(self, limit=50):
        self._maybe_fail()
        self.recent_calls += 1
        return list(self.recent[:limit])

    def saved_tracks(self, offset=0, limit=50):
        self._maybe_fail()
        self.saved_calls.append(offset)
        index = offset // limit
        return self.saved_pages[index] if index < len(self.saved_pages) else ([], False)


class FakeModes:
    def __init__(self, mode):
        self.mode = mode

    def stable_mode(self, now):
        return self.mode


class FakeFinder:
    def __init__(self):
        self.pools: dict[str, list[Track]] = {}
        self.calls = 0
        self.last_note = None

    def next_track(self, mode, now):
        self.calls += 1
        pool = self.pools.get(mode, [])
        return pool.pop(0) if pool else None
```

- [x] **Step 2: Write the failing tests**

`tests/test_recorder.py`:
```python
from fakes import FakeSpotify, make_track

from spotifymix.models import Play, SavedTrack
from spotifymix.recorder import Recorder
from spotifymix.spotify.auth import LoginRequired
from spotifymix.spotify.client import SpotifyUnavailable
from spotifymix.store import Store

DAY = 86400
NOW = 1000 * DAY


def make():
    sp = FakeSpotify()
    store = Store(":memory:")
    return Recorder(sp, store, recent_every=600, saved_every=1800, exclusion_days=7), sp, store


def test_sync_recent_stores_plays():
    rec, sp, store = make()
    sp.recent = [Play(make_track(1), NOW - 60)]
    assert rec.sync_recent(NOW) == 1
    assert store.is_excluded(make_track(1), NOW, 7)


def test_sync_saved_stops_at_the_cutoff():
    rec, sp, store = make()
    sp.saved_pages = [([SavedTrack(make_track(1), NOW - DAY),
                        SavedTrack(make_track(2), NOW - 8 * DAY)], True)]
    assert rec.sync_saved(NOW) == 1
    assert sp.saved_calls == [0]
    assert store.is_excluded(make_track(1), NOW, 7)
    assert not store.is_excluded(make_track(2), NOW, 7)


def test_sync_saved_follows_pages():
    rec, sp, _ = make()
    sp.saved_pages = [
        ([SavedTrack(make_track(i), NOW - DAY) for i in range(50)], True),
        ([SavedTrack(make_track(99), NOW - DAY)], False),
    ]
    assert rec.sync_saved(NOW) == 51
    assert sp.saved_calls == [0, 50]


def test_maybe_run_respects_intervals():
    rec, sp, _ = make()
    rec.maybe_run(NOW)
    assert (sp.recent_calls, len(sp.saved_calls)) == (1, 1)
    rec.maybe_run(NOW + 100)
    assert (sp.recent_calls, len(sp.saved_calls)) == (1, 1)
    rec.maybe_run(NOW + 600)
    assert (sp.recent_calls, len(sp.saved_calls)) == (2, 1)
    rec.maybe_run(NOW + 1800)
    assert (sp.recent_calls, len(sp.saved_calls)) == (3, 2)


def test_errors_are_recorded_not_raised():
    rec, sp, _ = make()
    sp.error = SpotifyUnavailable("Spotify is down")
    rec.maybe_run(NOW)
    assert rec.last_error == "Spotify is down"
    sp.error = LoginRequired("login again")
    rec.maybe_run(NOW + 600)
    assert rec.last_error == "login again"
    sp.error = None
    rec.maybe_run(NOW + 1200)
    assert rec.last_error is None
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.recorder'`.

- [x] **Step 4: Implement `src/spotifymix/recorder.py`**

```python
"""Keeps the local 7-day history in sync with Spotify."""

from __future__ import annotations

import logging
from collections.abc import Callable

from spotifymix.spotify.auth import AuthError
from spotifymix.spotify.client import SpotifyError
from spotifymix.store import DAY, Store

log = logging.getLogger(__name__)


class Recorder:
    def __init__(
        self, client, store: Store, recent_every: float, saved_every: float, exclusion_days: int
    ) -> None:
        self._client = client
        self._store = store
        self._recent_every = recent_every
        self._saved_every = saved_every
        self._days = exclusion_days
        self._next_recent = 0.0
        self._next_saved = 0.0
        self.last_error: str | None = None

    def maybe_run(self, now: float) -> None:
        if now >= self._next_recent:
            self._next_recent = now + self._recent_every
            self._run(self.sync_recent, now)
        if now >= self._next_saved:
            self._next_saved = now + self._saved_every
            self._run(self.sync_saved, now)

    def _run(self, job: Callable[[float], int], now: float) -> None:
        try:
            job(now)
            self.last_error = None
        except (SpotifyError, AuthError) as exc:
            self.last_error = str(exc)
            log.warning("history sync failed: %s", exc)

    def sync_recent(self, now: float) -> int:
        return self._store.add_plays(self._client.recently_played(limit=50))

    def sync_saved(self, now: float, max_pages: int = 20) -> int:
        cutoff = now - self._days * DAY
        offset = 0
        stored = 0
        for _ in range(max_pages):
            items, has_next = self._client.saved_tracks(offset=offset, limit=50)
            fresh = [s for s in items if s.added_at >= cutoff]
            self._store.upsert_saved(fresh)
            stored += len(fresh)
            if len(fresh) < len(items) or not has_next:
                break
            offset += 50
        return stored
```

- [x] **Step 5: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: recorder keeps 7 days of plays and saved tracks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Finder (fresh tracks by genre)

**Files:**
- Create: `src/spotifymix/finder.py`
- Test: `tests/test_finder.py`

**Interfaces:**
- Consumes: `ModeConfig` (Task 2), `Store.is_excluded` (Task 3), `client.search_tracks` (Task 6).
- Produces: `Finder(client, store: Store, modes: dict[str, ModeConfig], exclusion_days: int, rng: random.Random | None = None, buffer_size: int = 10, refill_below: int = 3, max_queries: int = 6, page_size: int = 10)` with `next_track(mode_id: str, now: float) -> Track | None` and attribute `last_note: str | None`. Client exceptions propagate to the caller.

- [x] **Step 1: Write the failing tests**

`tests/test_finder.py`:
```python
import random
import re

from fakes import FakeSpotify, make_track

from spotifymix.config import ModeConfig, load_config
from spotifymix.finder import Finder
from spotifymix.models import Play
from spotifymix.store import Store

DAY = 86400
NOW = 20_000 * DAY
QUERY = re.compile(r'^genre:"[^"]+"( year:\d{4}-\d{4})?$')
# One primary genre keeps these tests independent of the random genre choice.
SINGLE = {"coding": ModeConfig("coding", "Coding", ("deep house",), ("house",))}


def make(pool=None, modes=None, **kw):
    sp = FakeSpotify()
    sp.search_pool = pool or {}
    store = Store(":memory:")
    finder = Finder(sp, store, modes or SINGLE, exclusion_days=7, rng=random.Random(1), **kw)
    return finder, sp, store


def pool(n, prefix):
    return [make_track(i, prefix) for i in range(n)]


def test_returns_fresh_track_for_mode():
    finder, sp, _ = make({"deep house": pool(30, "d")})
    track = finder.next_track("coding", NOW)
    assert track.id.startswith("d")
    assert all(QUERY.match(q) for q, _, _ in sp.search_calls)
    assert all(limit == 10 for _, _, limit in sp.search_calls)


def test_skips_excluded_tracks():
    tracks = pool(10, "d")
    finder, _, store = make({"deep house": tracks})
    store.add_plays([Play(t, NOW - DAY) for t in tracks[:9]])
    assert finder.next_track("coding", NOW).id == "d9"


def test_proposed_track_is_not_returned_again():
    tracks = pool(1, "d")
    finder, _, store = make({"deep house": tracks})
    first = finder.next_track("coding", NOW)
    store.add_proposed(first, "coding", NOW)
    assert finder.next_track("coding", NOW + 1) is None


def test_buffer_avoids_new_searches():
    finder, sp, _ = make({"deep house": pool(30, "d")})
    finder.next_track("coding", NOW)
    calls = len(sp.search_calls)
    finder.next_track("coding", NOW + 1)
    assert len(sp.search_calls) == calls


def test_buffered_tracks_are_rechecked_before_use():
    tracks = pool(12, "d")
    finder, _, store = make({"deep house": tracks})
    first = finder.next_track("coding", NOW)
    store.add_plays([Play(t, NOW) for t in tracks if t.id != first.id])
    store.add_proposed(first, "coding", NOW)
    assert finder.next_track("coding", NOW + 1) is None


def test_uses_fallback_when_primary_is_empty():
    finder, _, _ = make({"house": pool(20, "h")})
    track = finder.next_track("coding", NOW)
    assert track.id.startswith("h")
    assert "fallback" in finder.last_note


def test_gives_up_when_everything_is_excluded():
    tracks = pool(10, "d")
    finder, sp, store = make({"deep house": tracks}, max_queries=6)
    store.add_plays([Play(t, NOW - DAY) for t in tracks])
    assert finder.next_track("coding", NOW) is None
    assert len(sp.search_calls) <= 12
    assert finder.last_note is not None


def test_offsets_stay_inside_results():
    finder, sp, store = make(
        {"deep house": pool(25, "d"), "tech house": pool(25, "t")}, modes=load_config().modes)
    for step in range(15):
        track = finder.next_track("coding", NOW + step)
        if track:
            store.add_proposed(track, "coding", NOW + step)
    assert sp.search_calls
    assert all(offset + limit <= 25 for _, offset, limit in sp.search_calls)


def test_unknown_mode_returns_none():
    finder, _, _ = make()
    assert finder.next_track("dancing", NOW) is None
    assert "unknown mode" in finder.last_note
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_finder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.finder'`.

- [x] **Step 3: Implement `src/spotifymix/finder.py`**

```python
"""Finds tracks of a genre that were not heard, saved or proposed in the last days."""

from __future__ import annotations

import random
from collections import deque
from datetime import UTC, datetime

from spotifymix.config import ModeConfig
from spotifymix.models import Track
from spotifymix.store import Store

MAX_OFFSET = 1000


class Finder:
    def __init__(
        self,
        client,
        store: Store,
        modes: dict[str, ModeConfig],
        exclusion_days: int,
        rng: random.Random | None = None,
        buffer_size: int = 10,
        refill_below: int = 3,
        max_queries: int = 6,
        page_size: int = 10,
    ) -> None:
        self._client = client
        self._store = store
        self._modes = modes
        self._days = exclusion_days
        self._rng = rng or random.Random()
        self._size = buffer_size
        self._refill_below = refill_below
        self._max_queries = max_queries
        self._page = page_size
        self._buffers: dict[str, deque[Track]] = {}
        self._totals: dict[str, int] = {}
        self.last_note: str | None = None

    def next_track(self, mode_id: str, now: float) -> Track | None:
        mode = self._modes.get(mode_id)
        if mode is None:
            self.last_note = f"unknown mode {mode_id!r}"
            return None
        buffer = self._buffers.setdefault(mode_id, deque())
        if len(buffer) < self._refill_below:
            self._refill(mode, buffer, now)
        while buffer:
            track = buffer.popleft()
            if not self._store.is_excluded(track, now, self._days):
                return track
        self.last_note = f"no fresh track found for {mode_id}"
        return None

    @staticmethod
    def _year_ranges(now: float) -> list[str | None]:
        year = datetime.fromtimestamp(now, tz=UTC).year
        return [
            f"{year - 2}-{year}", f"{year - 6}-{year - 3}", f"{year - 11}-{year - 7}",
            f"{year - 18}-{year - 12}", None,
        ]

    @staticmethod
    def _query(genre: str, years: str | None) -> str:
        return f'genre:"{genre}"' + (f" year:{years}" if years else "")

    def _refill(self, mode: ModeConfig, buffer: deque[Track], now: float) -> None:
        seen_ids = {t.id for t in buffer}
        seen_keys = {t.name_key for t in buffer}
        self.last_note = None
        for label, genres in (("primary", mode.genres), ("fallback", mode.fallback)):
            if not genres or len(buffer) >= self._refill_below:
                continue
            attempts = 0
            while len(buffer) < self._size and attempts < self._max_queries:
                attempts += 1
                genre = self._rng.choice(genres)
                query = self._query(genre, self._rng.choice(self._year_ranges(now)))
                known_total = self._totals.get(query)
                if known_total == 0:
                    continue
                if known_total is None:
                    offset = 0
                else:
                    offset = self._rng.randrange(
                        0, max(1, min(known_total, MAX_OFFSET) - self._page + 1))
                tracks, total = self._client.search_tracks(query, offset=offset, limit=self._page)
                self._totals[query] = total
                for track in tracks:
                    if track.id in seen_ids or track.name_key in seen_keys:
                        continue
                    if self._store.is_excluded(track, now, self._days):
                        continue
                    buffer.append(track)
                    seen_ids.add(track.id)
                    seen_keys.add(track.name_key)
            if label == "fallback" and buffer:
                self.last_note = f"used fallback genres for {mode.id}"
```

- [x] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed. Note on `test_offsets_stay_inside_results`: the first query of each combination uses offset 0, later ones use `randrange(0, total - 10 + 1)`, so `offset + 10 <= 25` always holds.

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: finder picks fresh tracks by genre with fallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Director (watch playback, queue one track before the end)

**Files:**
- Create: `src/spotifymix/director.py`
- Test: `tests/test_director.py`

**Interfaces:**
- Consumes: `Store.add_proposed` (Task 3), client methods and exceptions (Task 6), `Finder.next_track` and `last_note` (Task 8), any object with `stable_mode(now) -> str | None` (the `Classifier` from Task 4).
- Produces: `Director(client, finder, store: Store, modes, device_name: str | None, lead_seconds: float)` with `tick(now: float) -> float` (seconds until it wants to run again), `pause(until: float) -> None`, `resume() -> None`, `clear_aside() -> None`, properties `aside_mode: str | None`, `paused_until: float | None`, attributes `last_action: str | None`, `last_error: str | None`.

- [x] **Step 1: Write the failing tests**

`tests/test_director.py`:
```python
from fakes import FakeFinder, FakeModes, FakeSpotify, make_track, playing

from spotifymix.director import Director
from spotifymix.models import Playback
from spotifymix.spotify.auth import LoginRequired
from spotifymix.spotify.client import RateLimited, SpotifyUnavailable
from spotifymix.store import Store

NOW = 1_000_000.0
CUR = make_track(99, "cur")  # 200 s long


def make(mode="coding", device_name="MY-PC"):
    sp = FakeSpotify()
    store = Store(":memory:")
    modes = FakeModes(mode)
    finder = FakeFinder()
    finder.pools = {
        "coding": [make_track(i, "c") for i in range(5)],
        "debugging": [make_track(i, "d") for i in range(5)],
    }
    director = Director(sp, finder, store, modes, device_name, lead_seconds=20)
    return director, sp, store, modes, finder


def queued_ids(sp):
    return [uri.rsplit(":", 1)[1] for uri, _ in sp.queued]


def test_ignores_paused_playback():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 195_000, is_playing=False)
    d.tick(NOW)
    assert sp.queued == []


def test_ignores_ads_and_episodes():
    d, sp, *_ = make()
    sp.playback = Playback(True, "dev1", "MY-PC", "Computer", None, 0, None)
    d.tick(NOW)
    assert sp.queued == []


def test_ignores_other_devices():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 195_000, device_type="Smartphone", device_name="PHONE")
    d.tick(NOW)
    sp.playback = playing(CUR, 195_000, device_name="OTHER-PC")
    d.tick(NOW + 100)
    assert sp.queued == []


def test_any_computer_when_device_name_not_set():
    d, sp, *_ = make(device_name=None)
    sp.playback = playing(CUR, 185_000, device_name="WHATEVER")
    d.tick(NOW)
    assert queued_ids(sp) == ["c0"]


def test_waits_until_lead_time():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 100_000)
    assert d.tick(NOW) == 30
    sp.playback = playing(CUR, 170_000)
    assert d.tick(NOW + 30) == 10
    assert sp.queued == []


def test_queues_one_track_near_the_end():
    d, sp, store, *_ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    d.tick(NOW + 100)
    assert queued_ids(sp) == ["c0"]
    assert sp.queued[0][1] == "dev1"
    assert store.recent_proposed_ids() == {"c0"}
    assert "c0" in d.last_action


def test_mode_change_applies_to_next_track():
    d, sp, _, modes, _ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    sp.playback = playing(make_track(0, "c"), 185_000)
    modes.mode = "debugging"
    d.tick(NOW + 100)
    assert queued_ids(sp) == ["c0", "d0"]


def test_steps_aside_when_user_picks_own_music():
    d, sp, _, modes, _ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    own = make_track(1, "own")
    sp.playback = playing(own, 185_000, context="spotify:playlist:mine")
    d.tick(NOW + 100)
    assert queued_ids(sp) == ["c0"]
    assert d.aside_mode == "coding"
    modes.mode = "debugging"
    d.tick(NOW + 200)
    assert queued_ids(sp) == ["c0", "d0"]
    assert d.aside_mode is None


def test_resume_clears_aside():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    sp.playback = playing(make_track(1, "own"), 185_000)
    d.tick(NOW + 100)
    d.resume()
    d.tick(NOW + 101)
    assert queued_ids(sp) == ["c0", "c1"]


def test_pause_skips_spotify_calls():
    d, sp, *_ = make()
    sp.error = AssertionError("Spotify must not be called while paused")
    d.pause(NOW + 600)
    assert d.tick(NOW) <= 30
    assert d.paused_until == NOW + 600


def test_rate_limit_backs_off():
    d, sp, *_ = make()
    sp.error = RateLimited(42)
    assert d.tick(NOW) == 42
    assert "rate limit" in d.last_error


def test_unavailable_backs_off():
    d, sp, *_ = make()
    sp.error = SpotifyUnavailable("offline")
    assert d.tick(NOW) == 30


def test_login_required_slows_polling():
    d, sp, *_ = make()
    sp.error = LoginRequired("run spotifymix login")
    assert d.tick(NOW) == 60
    assert "login" in d.last_error


def test_no_track_found_is_reported_once():
    d, sp, _, _, finder = make()
    finder.pools = {}
    sp.playback = playing(CUR, 185_000)
    d.tick(NOW)
    d.tick(NOW + 100)
    assert sp.queued == []
    assert finder.calls == 1
    assert d.last_error


def test_queue_failure_is_retried():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 185_000)
    sp.queue_error = SpotifyUnavailable("hiccup")
    assert d.tick(NOW) == 10
    sp.queue_error = None
    d.tick(NOW + 10)
    assert len(sp.queued) == 1


def test_polls_only_when_due():
    d, sp, *_ = make()
    sp.playback = playing(CUR, 100_000)
    assert d.tick(NOW) == 30
    sp.error = AssertionError("not due yet")
    assert d.tick(NOW + 10) == 20
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_director.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.director'`.

- [x] **Step 3: Implement `src/spotifymix/director.py`**

```python
"""Watches Spotify playback and queues the next track of the current mode."""

from __future__ import annotations

import logging
from typing import Protocol

from spotifymix.models import Playback
from spotifymix.spotify.auth import AuthError, LoginRequired
from spotifymix.spotify.client import RateLimited, SpotifyError
from spotifymix.store import Store

log = logging.getLogger(__name__)

IDLE_SECONDS = 15.0
MAX_WAIT = 30.0


class ModeSource(Protocol):
    def stable_mode(self, now: float) -> str | None: ...


class Director:
    def __init__(
        self,
        client,
        finder,
        store: Store,
        modes: ModeSource,
        device_name: str | None,
        lead_seconds: float,
    ) -> None:
        self._client = client
        self._finder = finder
        self._store = store
        self._modes = modes
        self._device_name = device_name
        self._lead = lead_seconds
        self._last_seen: str | None = None
        self._queued_for: str | None = None
        self._expected_next: str | None = None
        self._aside_mode: str | None = None
        self._paused_until = 0.0
        self._next_poll = 0.0
        self.last_action: str | None = None
        self.last_error: str | None = None

    @property
    def aside_mode(self) -> str | None:
        return self._aside_mode

    @property
    def paused_until(self) -> float | None:
        return self._paused_until or None

    def pause(self, until: float) -> None:
        self._paused_until = until

    def resume(self) -> None:
        self._paused_until = 0.0
        self._aside_mode = None
        self._next_poll = 0.0

    def clear_aside(self) -> None:
        self._aside_mode = None
        self._next_poll = 0.0

    def tick(self, now: float) -> float:
        mode = self._modes.stable_mode(now)
        if now < self._paused_until:
            return min(MAX_WAIT, self._paused_until - now)
        if now < self._next_poll:
            return self._next_poll - now
        delay = max(1.0, self._poll(now, mode))
        self._next_poll = now + delay
        return delay

    def _on_this_device(self, playback: Playback) -> bool:
        if playback.device_type.lower() != "computer":
            return False
        if not self._device_name:
            return True
        return playback.device_name.lower() == self._device_name.lower()

    def _failure_delay(self, exc: Exception, default: float) -> float:
        self.last_error = str(exc)
        if isinstance(exc, RateLimited):
            return max(exc.retry_after, 5.0)
        if isinstance(exc, LoginRequired):
            return 60.0
        return default

    def _poll(self, now: float, mode: str | None) -> float:
        try:
            playback = self._client.get_playback()
        except (SpotifyError, AuthError) as exc:
            return self._failure_delay(exc, MAX_WAIT)
        if playback is None or not playback.is_playing or playback.item is None:
            return IDLE_SECONDS
        if not self._on_this_device(playback):
            return MAX_WAIT
        track_id = playback.item.id
        if track_id != self._last_seen:
            if self._expected_next is not None and track_id != self._expected_next:
                self._aside_mode = mode
                self.last_action = "stepped aside: you picked your own music"
            self._expected_next = None
            self._last_seen = track_id
        if self._aside_mode is not None:
            if mode is None or mode == self._aside_mode:
                return MAX_WAIT
            self._aside_mode = None
        if mode is None:
            return IDLE_SECONDS
        remaining = max(0.0, (playback.item.duration_ms - playback.progress_ms) / 1000)
        if remaining > self._lead:
            return min(MAX_WAIT, remaining - self._lead)
        if self._queued_for == track_id:
            return remaining + 1
        return self._queue_next(now, mode, playback, track_id, remaining)

    def _queue_next(
        self, now: float, mode: str, playback: Playback, track_id: str, remaining: float
    ) -> float:
        try:
            track = self._finder.next_track(mode, now)
            if track is None:
                self.last_error = self._finder.last_note or f"no fresh track found for {mode}"
                self._queued_for = track_id
                return remaining + 1
            self._client.add_to_queue(track.uri, playback.device_id)
        except (SpotifyError, AuthError) as exc:
            return self._failure_delay(exc, 10.0)
        self._store.add_proposed(track, mode, now)
        self._queued_for = track_id
        self._expected_next = track.id
        self.last_action = f"queued {track.id} {track.name} by {', '.join(track.artists)} ({mode})"
        self.last_error = None
        log.info("queued a %s track", mode)
        return remaining + 1
```

- [x] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed. Check against the tests: `test_waits_until_lead_time` expects `min(30, 100 - 20) = 30` and `min(30, 30 - 20) = 10`; `test_polls_only_when_due` expects the second tick to return the 20 s left until the planned poll.

- [x] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: director queues one fresh track before the current one ends

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Local server and the Claude Code hook

**Files:**
- Modify: `src/spotifymix/paths.py` (add `DEFAULT_SERVICE_PORT`, `read_service_port`)
- Create: `src/spotifymix/server.py`, `src/spotifymix/hook.py`
- Test: `tests/test_server.py`, `tests/test_hook.py`

**Interfaces:**
- Consumes: `paths.config_file`, `paths.service_token_file`, `paths.ensure_service_token`.
- Produces: `paths.DEFAULT_SERVICE_PORT = 47615`; `paths.read_service_port(config_file: Path) -> int`; `server.TOKEN_HEADER = "X-SpotifyMix-Token"`; `server.make_server(port: int, token: str, on_event: Callable[[dict], None], on_status: Callable[[], dict], on_control: Callable[[dict], dict]) -> ThreadingHTTPServer`; `server.ServiceDown(Exception)`; `server.call_service(method: str, path: str, body: dict | None = None, *, port: int, token: str, timeout: float = 3.0) -> dict` (raises `ServiceDown` when unreachable, `ValueError` with the server's error message on HTTP errors); `hook.main() -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_server.py`:
```python
import threading
import urllib.error
import urllib.request

import pytest
from fakes import free_port

from spotifymix import paths
from spotifymix.server import TOKEN_HEADER, ServiceDown, call_service, make_server

TOKEN = "secret-token"


@pytest.fixture
def running():
    servers = []

    def start(on_event=None, on_status=None, on_control=None):
        port = free_port()
        server = make_server(
            port, TOKEN,
            on_event or (lambda event: None),
            on_status or (lambda: {"ok": True}),
            on_control or (lambda command: {"done": command}),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server, port

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_binds_loopback_only(running):
    server, _ = running()
    assert server.server_address[0] == "127.0.0.1"


def test_rejects_wrong_token(running):
    _, port = running()
    with pytest.raises(ValueError, match="forbidden"):
        call_service("GET", "/status", port=port, token="wrong")


def test_event_is_delivered(running):
    received = []
    _, port = running(on_event=received.append)
    assert call_service("POST", "/event", {"hook_event_name": "Stop"}, port=port, token=TOKEN) == {}
    assert received == [{"hook_event_name": "Stop"}]


def test_event_handler_errors_do_not_leak(running):
    def boom(event):
        raise RuntimeError("bug")

    _, port = running(on_event=boom)
    assert call_service("POST", "/event", {"x": 1}, port=port, token=TOKEN) == {}


def test_bad_json_is_rejected(running):
    _, port = running()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/event", data=b"not json", method="POST",
        headers={TOKEN_HEADER: TOKEN},
    )
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(request, timeout=3)
    assert info.value.code == 400


def test_status_and_control(running):
    _, port = running()
    assert call_service("GET", "/status", port=port, token=TOKEN) == {"ok": True}
    reply = call_service("POST", "/control", {"action": "resume"}, port=port, token=TOKEN)
    assert reply == {"done": {"action": "resume"}}


def test_control_errors_become_messages(running):
    def reject(command):
        raise ValueError("unknown action 'dance'")

    _, port = running(on_control=reject)
    with pytest.raises(ValueError, match="unknown action"):
        call_service("POST", "/control", {"action": "dance"}, port=port, token=TOKEN)


def test_service_down_raises():
    with pytest.raises(ServiceDown):
        call_service("GET", "/status", port=free_port(), token=TOKEN, timeout=2)


def test_read_service_port(tmp_path):
    config = tmp_path / "config.toml"
    assert paths.read_service_port(config) == 47615
    config.write_text("[service]\nport = 50001\n", encoding="utf-8")
    assert paths.read_service_port(config) == 50001
    config.write_text("garbage = = =", encoding="utf-8")
    assert paths.read_service_port(config) == 47615
```

`tests/test_hook.py`:
```python
import io
import socket
import sys
import threading
import time

from fakes import free_port

from spotifymix import hook, paths
from spotifymix.server import make_server

EVENT = b'{"hook_event_name": "PreToolUse", "tool_name": "Edit"}'


class FakeStdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


def configure_port(port: int) -> None:
    paths.config_file().write_text(f"[service]\nport = {port}\n", encoding="utf-8")


def run_hook(monkeypatch, data=EVENT):
    monkeypatch.setattr(sys, "stdin", FakeStdin(data))
    start = time.monotonic()
    code = hook.main()
    return code, time.monotonic() - start


def serve(port, received):
    server = make_server(port, paths.ensure_service_token(), received.append, dict, dict)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_hook_delivers_event(monkeypatch):
    port = free_port()
    configure_port(port)
    received = []
    server = serve(port, received)
    try:
        code, _ = run_hook(monkeypatch)
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert received == [{"hook_event_name": "PreToolUse", "tool_name": "Edit"}]


def test_hook_ignores_proxy_settings(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    port = free_port()
    configure_port(port)
    received = []
    server = serve(port, received)
    try:
        run_hook(monkeypatch)
    finally:
        server.shutdown()
        server.server_close()
    assert len(received) == 1


def test_hook_exits_zero_when_service_down(monkeypatch):
    configure_port(free_port())
    paths.ensure_service_token()
    code, elapsed = run_hook(monkeypatch)
    assert code == 0
    assert elapsed < 2.0


def test_hook_gives_up_quickly_when_service_hangs(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)  # accepts connections at OS level but never answers
    try:
        configure_port(listener.getsockname()[1])
        paths.ensure_service_token()
        code, elapsed = run_hook(monkeypatch)
    finally:
        listener.close()
    assert code == 0
    assert elapsed < 2.5


def test_hook_without_token_does_nothing(monkeypatch):
    configure_port(free_port())
    assert not paths.service_token_file().exists()
    code, _ = run_hook(monkeypatch)
    assert code == 0


def test_hook_survives_garbage_input(monkeypatch):
    port = free_port()
    configure_port(port)
    received = []
    server = serve(port, received)
    try:
        code, _ = run_hook(monkeypatch, data=b"\xff\x00 not json")
    finally:
        server.shutdown()
        server.server_close()
    assert code == 0
    assert received == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py tests/test_hook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.server'`.

- [ ] **Step 3: Add to `src/spotifymix/paths.py`**

Add `import tomllib` to the imports, then append:
```python
DEFAULT_SERVICE_PORT = 47615


def read_service_port(config_file: Path) -> int:
    """Service port from config.toml without loading the full configuration."""
    try:
        data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        return int(data.get("service", {}).get("port", DEFAULT_SERVICE_PORT))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_SERVICE_PORT
```
(`tomllib.TOMLDecodeError` is a subclass of `ValueError`.)

- [ ] **Step 4: Implement `src/spotifymix/server.py`**

```python
"""Local HTTP endpoint for hook events and CLI control. Bound to 127.0.0.1 only."""

from __future__ import annotations

import hmac
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_BODY = 1_000_000
TOKEN_HEADER = "X-SpotifyMix-Token"

log = logging.getLogger(__name__)


class ServiceDown(Exception):
    pass


def make_server(
    port: int,
    token: str,
    on_event: Callable[[dict], None],
    on_status: Callable[[], dict],
    on_control: Callable[[dict], dict],
) -> ThreadingHTTPServer:
    expected = token.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # never print request data

        def _authorized(self) -> bool:
            supplied = self.headers.get(TOKEN_HEADER, "").encode("utf-8")
            return hmac.compare_digest(supplied, expected)

        def _body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY:
                return None
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                return None
            return data if isinstance(data, dict) else None

        def _reply(self, code: int, payload: dict | None = None) -> None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(code)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._authorized():
                return self._reply(403, {"error": "forbidden"})
            if self.path == "/status":
                return self._reply(200, on_status())
            return self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return self._reply(403, {"error": "forbidden"})
            data = self._body()
            if data is None:
                return self._reply(400, {"error": "expected a JSON object"})
            if self.path == "/event":
                try:
                    on_event(data)
                except Exception:
                    log.exception("event handling failed")
                return self._reply(204)
            if self.path == "/control":
                try:
                    return self._reply(200, on_control(data))
                except (ValueError, KeyError) as exc:
                    return self._reply(400, {"error": str(exc)})
            return self._reply(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def call_service(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    port: int,
    token: str,
    timeout: float = 3.0,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method,
        headers={TOKEN_HEADER: token, "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail).get("error", detail)
        except ValueError:
            message = detail or f"HTTP {exc.code}"
        raise ValueError(message) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ServiceDown(str(exc)) from exc
    return json.loads(raw) if raw else {}
```

- [ ] **Step 5: Implement `src/spotifymix/hook.py`**

```python
"""Claude Code hook: forward the event to the local service.

Runs as an async hook. It must never delay or break Claude Code, so it always
exits 0 and gives up after a 1 second network timeout.
"""

from __future__ import annotations

import sys
import urllib.request

MAX_BYTES = 1_000_000
TIMEOUT_SECONDS = 1.0


def _forward(raw: bytes) -> None:
    from spotifymix import paths

    token_file = paths.service_token_file()
    if not token_file.exists():
        return
    token = token_file.read_text(encoding="utf-8").strip()
    port = paths.read_service_port(paths.config_file())
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/event", data=raw, method="POST",
        headers={"Content-Type": "application/json", "X-SpotifyMix-Token": token},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.open(request, timeout=TIMEOUT_SECONDS).close()


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES)
        if raw:
            _forward(raw)
    except BaseException:  # the hook must never disturb Claude Code
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

The `[project.scripts]` entry `spotifymix-hook = "spotifymix.hook:main"` makes the console script call `sys.exit(main())`, so the exit code is 0.

- [ ] **Step 6: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed. If `test_hook_exits_zero_when_service_down` is slow on Windows, that is the known Windows loopback connect delay; the 1 s timeout caps it, so the bound of 2 s must still hold. Do not raise the bound above 2 s; fix the code instead.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: loopback service endpoint and non-blocking Claude Code hook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Service wiring and main loop

**Files:**
- Create: `src/spotifymix/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 10.
- Produces: `Service(cfg: Config, store: Store, classifier: Classifier, director: Director, recorder: Recorder, clock: Callable[[], float] = time.time)` with `handle_event(payload: dict) -> None`, `control(command: dict) -> dict`, `status() -> dict`, `step() -> float`, `run_forever(stop: threading.Event) -> None`; module constant `LOOP_MAX_SLEEP = 5.0`; `build(cfg: Config) -> Service`; `run() -> int`; `main() -> None` (entry point of `spotifymix-service`). `control` accepts `{"action": "mode", "mode": str, "seconds": float | None}`, `{"action": "pause", "seconds": float}`, `{"action": "resume"}` and raises `ValueError` otherwise. `status()` keys: `mode`, `leader`, `candidate`, `manual`, `manual_until`, `paused_until`, `aside_mode`, `last_action`, `last_error`, `device_name`, `history`, `recent_events`.

- [ ] **Step 1: Write the failing tests**

`tests/test_service.py`:
```python
import random
import threading

import pytest
from fakes import FakeSpotify, make_track, playing

from spotifymix import service as service_module
from spotifymix.classifier import Classifier
from spotifymix.config import load_config
from spotifymix.director import Director
from spotifymix.finder import Finder
from spotifymix.recorder import Recorder
from spotifymix.service import Service
from spotifymix.store import Store

NOW = 2_000_000_000.0
CUR = make_track(1, "cur")
EDIT = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": "C:/Users/someone/secret-project/app.py"},
        "prompt": "top secret words", "session_id": "s1"}


def build(store=None):
    cfg = load_config()
    sp = FakeSpotify()
    sp.search_pool = {
        genre: [make_track(i, genre.replace(" ", "")[:4]) for i in range(30)]
        for genre in ("deep house", "tech house", "hip hop", "rap")
    }
    store = store or Store(":memory:")
    clock = [NOW]
    classifier = Classifier(cfg.rules, cfg.window_seconds, cfg.switch_after_seconds)
    finder = Finder(sp, store, cfg.modes, cfg.exclusion_days, rng=random.Random(3))
    director = Director(sp, finder, store, classifier, "MY-PC", cfg.queue_lead_seconds)
    recorder = Recorder(sp, store, cfg.recent_poll_seconds, cfg.saved_poll_seconds,
                        cfg.exclusion_days)
    svc = Service(cfg, store, classifier, director, recorder, clock=lambda: clock[0])
    return svc, sp, store, clock


def queued_prefixes(sp):
    return [uri.rsplit(":", 1)[1][:4] for uri, _ in sp.queued]


def test_event_leads_to_a_queued_track_of_the_mode():
    svc, sp, _, _ = build()
    svc.handle_event(EDIT)
    sp.playback = playing(CUR, 185_000)
    svc.step()
    assert queued_prefixes(sp) in (["deep"], ["tech"])


def test_events_are_persisted_without_payload_details(tmp_path):
    db = tmp_path / "db.sqlite"
    svc, _, store, _ = build(Store(db))
    svc.handle_event(EDIT)
    assert store.recent_events() == [(int(NOW), "PreToolUse", "coding")]
    store.close()
    content = db.read_bytes()
    assert b"secret" not in content
    assert b"someone" not in content


def test_manual_mode_control():
    svc, sp, _, _ = build()
    svc.handle_event(EDIT)
    reply = svc.control({"action": "mode", "mode": "debugging", "seconds": 600})
    assert reply["mode"] == "debugging"
    assert svc.status()["manual"] == "debugging"
    sp.playback = playing(CUR, 185_000)
    svc.step()
    # Track ids start with the first 4 letters of the genre: "hiph..." or "rap...".
    assert queued_prefixes(sp)[0].startswith(("hiph", "rap"))


def test_unknown_mode_and_action_are_rejected():
    svc, *_ = build()
    with pytest.raises(ValueError, match="unknown mode"):
        svc.control({"action": "mode", "mode": "dancing"})
    with pytest.raises(ValueError, match="unknown action"):
        svc.control({"action": "dance"})


def test_pause_and_resume():
    svc, sp, _, _ = build()
    svc.step()  # the recorder runs now and is not due again for 10 minutes
    svc.control({"action": "pause", "seconds": 600})
    assert svc.status()["paused_until"] == NOW + 600
    sp.error = AssertionError("Spotify must not be called while paused")
    svc.step()
    sp.error = None
    svc.control({"action": "resume"})
    assert svc.status()["paused_until"] is None


def test_status_shape():
    svc, *_ = build()
    assert set(svc.status()) == {
        "mode", "leader", "candidate", "manual", "manual_until", "paused_until",
        "aside_mode", "last_action", "last_error", "device_name", "history", "recent_events",
    }


def test_run_forever_survives_errors_and_stops(monkeypatch):
    svc, *_ = build()
    stop = threading.Event()
    calls = []

    def failing_step():
        calls.append(1)
        if len(calls) >= 2:
            stop.set()
        raise RuntimeError("unexpected")

    monkeypatch.setattr(service_module, "LOOP_MAX_SLEEP", 0.01)
    monkeypatch.setattr(svc, "step", failing_step)
    svc.run_forever(stop)
    assert len(calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.service'`.

- [ ] **Step 3: Implement `src/spotifymix/service.py`**

```python
"""The SpotifyMix background service: wiring, main loop and entry point."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler

from spotifymix import paths
from spotifymix.classifier import Classifier
from spotifymix.config import Config, load_config
from spotifymix.director import Director
from spotifymix.finder import Finder
from spotifymix.recorder import Recorder
from spotifymix.server import make_server
from spotifymix.store import Store

log = logging.getLogger("spotifymix")

LOOP_MAX_SLEEP = 5.0
PRUNE_EVERY = 3600.0


class Service:
    def __init__(
        self,
        cfg: Config,
        store: Store,
        classifier: Classifier,
        director: Director,
        recorder: Recorder,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._classifier = classifier
        self._director = director
        self._recorder = recorder
        self._clock = clock
        self._last_prune = 0.0

    def handle_event(self, payload: dict) -> None:
        now = self._clock()
        mode = self._classifier.observe(payload, now)
        event = str(payload.get("hook_event_name") or "unknown")[:40]
        self._store.log_event(now, event, mode)

    def control(self, command: dict) -> dict:
        now = self._clock()
        action = command.get("action")
        if action == "mode":
            mode = command.get("mode")
            if mode not in self._cfg.modes:
                raise ValueError(f"unknown mode {mode!r}; choose from {sorted(self._cfg.modes)}")
            seconds = float(command.get("seconds") or self._cfg.manual_default_seconds)
            self._classifier.set_manual(mode, now + seconds)
            self._director.clear_aside()
            return {"ok": True, "mode": mode, "until": now + seconds}
        if action == "pause":
            seconds = float(command["seconds"])
            self._director.pause(now + seconds)
            return {"ok": True, "paused_until": now + seconds}
        if action == "resume":
            self._classifier.clear_manual()
            self._director.resume()
            return {"ok": True}
        raise ValueError(f"unknown action {action!r}")

    def status(self) -> dict:
        now = self._clock()
        snap = self._classifier.snapshot(now)
        return {
            "mode": snap["stable"],
            "leader": snap["leader"],
            "candidate": snap["candidate"],
            "manual": snap["manual"],
            "manual_until": snap["manual_until"],
            "paused_until": self._director.paused_until,
            "aside_mode": self._director.aside_mode,
            "last_action": self._director.last_action,
            "last_error": self._director.last_error or self._recorder.last_error,
            "device_name": self._cfg.device_name,
            "history": self._store.counts(now, self._cfg.exclusion_days),
            "recent_events": [
                {"ts": ts, "event": event, "mode": mode}
                for ts, event, mode in self._store.recent_events(5)
            ],
        }

    def step(self) -> float:
        now = self._clock()
        self._recorder.maybe_run(now)
        delay = self._director.tick(now)
        if now - self._last_prune >= PRUNE_EVERY:
            self._store.prune(now)
            self._last_prune = now
        return delay

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                delay = self.step()
            except Exception:
                log.exception("unexpected error in the main loop")
                delay = 30.0
            stop.wait(min(max(delay, 1.0), LOOP_MAX_SLEEP))


def setup_logging() -> None:
    handler = RotatingFileHandler(
        paths.log_dir() / "spotifymix.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)


def build(cfg: Config) -> Service:
    import httpx

    from spotifymix.spotify.auth import TokenStore
    from spotifymix.spotify.client import SpotifyClient

    store = Store(paths.db_file())
    tokens = TokenStore(paths.token_fallback_file())
    client = SpotifyClient(httpx.Client(), cfg.client_id or "", tokens)
    classifier = Classifier(cfg.rules, cfg.window_seconds, cfg.switch_after_seconds)
    finder = Finder(client, store, cfg.modes, cfg.exclusion_days)
    director = Director(client, finder, store, classifier, cfg.device_name, cfg.queue_lead_seconds)
    recorder = Recorder(
        client, store, cfg.recent_poll_seconds, cfg.saved_poll_seconds, cfg.exclusion_days
    )
    return Service(cfg, store, classifier, director, recorder)


def run() -> int:
    setup_logging()
    cfg = load_config(paths.config_file())
    if not cfg.client_id:
        # Exit 0 so launchd/systemd do not restart-loop an unconfigured install.
        log.error("SpotifyMix is not configured; run `spotifymix setup`")
        return 0
    service = build(cfg)
    try:
        server = make_server(
            cfg.service_port, paths.ensure_service_token(),
            service.handle_event, service.status, service.control,
        )
    except OSError:
        log.info("port %s is busy: SpotifyMix is probably already running", cfg.service_port)
        return 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    log.info("SpotifyMix service listening on 127.0.0.1:%s", cfg.service_port)
    try:
        service.run_forever(stop)
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main() -> None:
    sys.exit(run())
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: service wiring, control commands and main loop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Claude Code settings installer

**Files:**
- Create: `src/spotifymix/claude_settings.py`
- Test: `tests/test_claude_settings.py`

**Interfaces:**
- Produces: `HOOK_EVENTS = ("PreToolUse", "PostToolUseFailure", "UserPromptSubmit", "SubagentStart")`; `MARKER = "spotifymix-hook"`; `SettingsError(Exception)`; `settings_path(config_dir: Path) -> Path`; `hook_command(executable: Path) -> str`; `load_settings(path: Path) -> dict`; `plan_install(settings: dict, command: str) -> dict`; `plan_uninstall(settings: dict) -> dict`; `write_settings(path: Path, data: dict) -> Path | None` (returns the backup path); `diff_text(old: dict, new: dict) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_settings.py`:
```python
import json
from pathlib import Path

import pytest

from spotifymix.claude_settings import (
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

CMD = '"/opt/bin/spotifymix-hook"'
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
    assert "spotifymix-hook" in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "opus"


def test_write_without_existing_file_has_no_backup(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    assert write_settings(path, {"a": 1}) is None
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_hook_command_quotes_paths_with_spaces():
    exe = Path("C:/Program Files/uv/spotifymix-hook.exe")
    assert hook_command(exe) == '"C:/Program Files/uv/spotifymix-hook.exe"'


def test_settings_path_and_diff(tmp_path):
    assert settings_path(tmp_path) == tmp_path / "settings.json"
    text = diff_text({}, {"a": 1})
    assert '+  "a": 1' in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spotifymix.claude_settings'`.

- [ ] **Step 3: Implement `src/spotifymix/claude_settings.py`**

```python
"""Adds and removes the SpotifyMix hooks in Claude Code's settings.json."""

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
MARKER = "spotifymix-hook"


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
        backup = path.with_name(f"{path.name}.spotifymix-backup-{stamp}")
        backup.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".spotifymix-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def diff_text(old: dict[str, Any], new: dict[str, Any]) -> str:
    before = json.dumps(old, indent=2, ensure_ascii=False).splitlines()
    after = json.dumps(new, indent=2, ensure_ascii=False).splitlines()
    return "\n".join(difflib.unified_diff(before, after, "before", "after", lineterm=""))
```

Note: `plan_uninstall` only deletes an empty `hooks` table; with `FOREIGN` the table keeps `SessionStart` and `PreToolUse`, so `test_uninstall_restores_the_original` holds.

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: safe install and removal of Claude Code hooks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Autostart on Windows, macOS and Linux

**Files:**
- Create: `src/spotifymix/autostart.py`
- Test: `tests/test_autostart.py`

**Interfaces:**
- Consumes: `paths.data_dir`, `paths.log_dir`.
- Produces: `AutostartError(Exception)`; `TASK_NAME = "SpotifyMix"`; `LAUNCHD_LABEL = "io.github.caesla.spotifymix"`; `SYSTEMD_UNIT = "spotifymix.service"`; `service_executable() -> Path`; `render_windows_task(exe: Path, user_id: str) -> str`; `render_launchd_plist(exe: Path, log_dir: Path) -> str`; `render_systemd_unit(exe: Path) -> str`; `install(exe: Path, *, platform: str = sys.platform, runner: Runner = default_runner, home: Path | None = None, uid: int | None = None, user_id: str | None = None) -> str`; `uninstall(*, platform: str = sys.platform, runner: Runner = default_runner, home: Path | None = None, uid: int | None = None) -> str`. `Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_autostart.py`:
```python
import plistlib
import subprocess
from pathlib import Path

import pytest

from spotifymix import autostart, paths

EXE = Path("/opt/tools/spotifymix-service")


class FakeRunner:
    def __init__(self, fail_prefixes=()):
        self.calls = []
        self.fail_prefixes = [tuple(p) for p in fail_prefixes]

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        failed = any(tuple(args[:len(p)]) == p for p in self.fail_prefixes)
        return subprocess.CompletedProcess(args, 1 if failed else 0, "", "denied" if failed else "")


def test_windows_install_registers_task():
    runner = FakeRunner()
    text = autostart.install(EXE, platform="win32", runner=runner, user_id="PC\\me")
    create = runner.calls[0]
    assert create[:4] == ["schtasks", "/Create", "/TN", "SpotifyMix"]
    xml_path = Path(create[create.index("/XML") + 1])
    xml = xml_path.read_text(encoding="utf-16")
    assert str(EXE) in xml and "LogonTrigger" in xml and "RestartOnFailure" in xml
    assert "PC\\me" in xml
    assert xml_path.parent == paths.data_dir()
    assert runner.calls[1] == ["schtasks", "/Run", "/TN", "SpotifyMix"]
    assert "Task Scheduler" in text


def test_windows_falls_back_to_run_key():
    runner = FakeRunner(fail_prefixes=[["schtasks", "/Create"]])
    text = autostart.install(EXE, platform="win32", runner=runner, user_id="PC\\me")
    reg = next(c for c in runner.calls if c[0] == "reg")
    assert reg[:2] == ["reg", "add"] and f'"{EXE}"' in reg
    assert "Run key" in text


def test_windows_xml_escapes_special_characters():
    xml = autostart.render_windows_task(Path("C:/A&B/spotifymix-service.exe"), "PC\\me")
    assert "A&amp;B" in xml


def test_macos_install_writes_plist_and_bootstraps(tmp_path):
    runner = FakeRunner()
    autostart.install(EXE, platform="darwin", runner=runner, home=tmp_path, uid=501)
    plist_path = tmp_path / "Library" / "LaunchAgents" / "io.github.caesla.spotifymix.plist"
    data = plistlib.loads(plist_path.read_bytes())
    assert data["ProgramArguments"] == [str(EXE)]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}
    assert runner.calls[-1] == ["launchctl", "bootstrap", "gui/501", str(plist_path)]


def test_linux_install_writes_unit_and_enables(tmp_path):
    runner = FakeRunner()
    autostart.install(EXE, platform="linux", runner=runner, home=tmp_path)
    unit = (tmp_path / ".config" / "systemd" / "user" / "spotifymix.service").read_text()
    assert f'ExecStart="{EXE}"' in unit and "Restart=on-failure" in unit
    assert ["systemctl", "--user", "enable", "--now", "spotifymix.service"] in runner.calls


def test_linux_failure_raises(tmp_path):
    runner = FakeRunner(fail_prefixes=[["systemctl", "--user", "enable"]])
    with pytest.raises(autostart.AutostartError):
        autostart.install(EXE, platform="linux", runner=runner, home=tmp_path)


def test_uninstall_linux_removes_unit(tmp_path):
    autostart.install(EXE, platform="linux", runner=FakeRunner(), home=tmp_path)
    runner = FakeRunner()
    autostart.uninstall(platform="linux", runner=runner, home=tmp_path)
    assert not (tmp_path / ".config" / "systemd" / "user" / "spotifymix.service").exists()
    assert ["systemctl", "--user", "disable", "--now", "spotifymix.service"] in runner.calls


def test_uninstall_windows_removes_task_and_run_key():
    runner = FakeRunner(fail_prefixes=[["reg"]])
    autostart.uninstall(platform="win32", runner=runner)
    assert ["schtasks", "/Delete", "/TN", "SpotifyMix", "/F"] in runner.calls
    assert any(c[:2] == ["reg", "delete"] for c in runner.calls)


def test_unsupported_platform_raises():
    with pytest.raises(autostart.AutostartError, match="unsupported"):
        autostart.install(EXE, platform="plan9", runner=FakeRunner())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_autostart.py -v`
Expected: FAIL with `ImportError: cannot import name 'autostart'`.

- [ ] **Step 3: Implement `src/spotifymix/autostart.py`**

```python
"""Start the service at login: Task Scheduler, launchd or systemd, per platform."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from spotifymix import paths

TASK_NAME = "SpotifyMix"
LAUNCHD_LABEL = "io.github.caesla.spotifymix"
SYSTEMD_UNIT = "spotifymix.service"
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class AutostartError(Exception):
    pass


def default_runner(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def service_executable() -> Path:
    found = shutil.which("spotifymix-service")
    if not found:
        raise AutostartError(
            "spotifymix-service was not found on PATH; install SpotifyMix with `uv tool install`"
        )
    return Path(found)


def render_windows_task(exe: Path, user_id: str) -> str:
    user = escape(user_id)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>SpotifyMix background service</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(exe))}</Command></Exec>
  </Actions>
</Task>
"""


def render_launchd_plist(exe: Path, log_dir: Path) -> str:
    data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [str(exe)],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_dir / "launchd.out.log"),
        "StandardErrorPath": str(log_dir / "launchd.err.log"),
    }
    return plistlib.dumps(data).decode("utf-8")


def render_systemd_unit(exe: Path) -> str:
    return f"""[Unit]
Description=SpotifyMix background service
After=network-online.target

[Service]
ExecStart="{exe}"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def _windows_user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _check(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        raise AutostartError(f"{what} failed: {(result.stderr or result.stdout).strip()}")


def install(
    exe: Path,
    *,
    platform: str = sys.platform,
    runner: Runner = default_runner,
    home: Path | None = None,
    uid: int | None = None,
    user_id: str | None = None,
) -> str:
    home = home or Path.home()
    if platform.startswith("win"):
        xml_path = paths.data_dir() / "spotifymix-task.xml"
        xml_path.write_text(render_windows_task(exe, user_id or _windows_user()), encoding="utf-16")
        created = runner(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"])
        if created.returncode == 0:
            runner(["schtasks", "/Run", "/TN", TASK_NAME])
            return "Windows Task Scheduler task 'SpotifyMix' (starts at logon, restarts on failure)"
        added = runner(["reg", "add", RUN_KEY, "/v", TASK_NAME, "/t", "REG_SZ",
                        "/d", f'"{exe}"', "/f"])
        _check(added, "registering the Run key")
        runner(["cmd", "/c", "start", "", str(exe)])
        return "Windows Run key 'SpotifyMix' (starts at logon; no automatic restart)"
    if platform == "darwin":
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / f"{LAUNCHD_LABEL}.plist"
        plist.write_text(render_launchd_plist(exe, paths.log_dir()), encoding="utf-8")
        domain = f"gui/{uid if uid is not None else os.getuid()}"
        runner(["launchctl", "bootout", domain, str(plist)])  # fine if it was not loaded
        _check(runner(["launchctl", "bootstrap", domain, str(plist)]), "launchctl bootstrap")
        return f"launchd agent {LAUNCHD_LABEL}"
    if platform.startswith("linux"):
        unit_dir = home / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / SYSTEMD_UNIT).write_text(render_systemd_unit(exe), encoding="utf-8")
        _check(runner(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _check(runner(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT]),
               "systemctl enable")
        return f"systemd user service {SYSTEMD_UNIT}"
    raise AutostartError(f"unsupported platform: {platform}")


def uninstall(
    *,
    platform: str = sys.platform,
    runner: Runner = default_runner,
    home: Path | None = None,
    uid: int | None = None,
) -> str:
    home = home or Path.home()
    if platform.startswith("win"):
        runner(["schtasks", "/End", "/TN", TASK_NAME])
        runner(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        runner(["reg", "delete", RUN_KEY, "/v", TASK_NAME, "/f"])
        return "Removed the SpotifyMix scheduled task and Run key (if present)"
    if platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        domain = f"gui/{uid if uid is not None else os.getuid()}"
        runner(["launchctl", "bootout", domain, str(plist)])
        plist.unlink(missing_ok=True)
        return f"Removed launchd agent {LAUNCHD_LABEL}"
    if platform.startswith("linux"):
        runner(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT])
        (home / ".config" / "systemd" / "user" / SYSTEMD_UNIT).unlink(missing_ok=True)
        runner(["systemctl", "--user", "daemon-reload"])
        return f"Removed systemd user service {SYSTEMD_UNIT}"
    raise AutostartError(f"unsupported platform: {platform}")
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed on every OS (the tests pass `platform`, `home` and `uid` explicitly, so `os.getuid` is never called on Windows).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: autostart via Task Scheduler, launchd and systemd

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Command line interface

**Files:**
- Create: `src/spotifymix/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `paths`, `config`, `server.call_service`, `server.ServiceDown`, `claude_settings`, `autostart`, `spotify.auth`, `spotify.client`, `service.run`.
- Produces: `parse_duration(text: str) -> int`; `format_status(status: dict) -> str`; `build_parser() -> argparse.ArgumentParser`; `run(argv: list[str] | None = None) -> int`; `main() -> None`. Module-level helper `_client(cfg: Config)` (monkeypatched in tests). Commands: `setup`, `login`, `logout`, `status`, `mode <mode> [--for DURATION]`, `pause <DURATION>`, `resume`, `modes`, `check-genres`, `hooks install|uninstall [--yes]`, `service run|install|uninstall`, `uninstall [--yes]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'cli'`.

- [ ] **Step 3: Implement `src/spotifymix/cli.py`**

```python
"""The `spotifymix` command."""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import sys
import time
from pathlib import Path

from spotifymix import __version__, autostart, paths
from spotifymix.claude_settings import (
    SettingsError,
    diff_text,
    hook_command,
    load_settings,
    plan_install,
    plan_uninstall,
    settings_path,
    write_settings,
)
from spotifymix.config import Config, ConfigError, load_config, save_user_settings
from spotifymix.server import ServiceDown, call_service

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
WEAK_GENRE_THRESHOLD = 50

SETUP_INTRO = """SpotifyMix setup

1. Open https://developer.spotify.com/dashboard and create an app
   (your account needs Spotify Premium). Any name and description work.
2. Add this Redirect URI exactly as written:
     {redirect}
3. When asked which APIs you plan to use, select "Web API". Save.
4. Copy the app's Client ID. No client secret is needed.
"""


def parse_duration(text: str) -> int:
    value = text.strip().lower()
    if value.isdigit():
        seconds = int(value) * 60
    else:
        match = _DURATION.match(value)
        if not value or not match or not any(match.groups()):
            raise argparse.ArgumentTypeError(
                f"invalid duration {text!r}; use for example 30m, 1h or 1h30m")
        hours, minutes, secs = (int(g) if g else 0 for g in match.groups())
        seconds = hours * 3600 + minutes * 60 + secs
    if seconds <= 0:
        raise argparse.ArgumentTypeError("the duration must be longer than zero")
    return seconds


def _clock(ts: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(ts)) if ts else ""


def format_status(status: dict) -> str:
    lines = []
    mode = status.get("mode") or "none yet"
    if status.get("manual"):
        mode = f"{status['manual']} (manual until {_clock(status.get('manual_until'))})"
    lines.append(f"Mode:          {mode}")
    if status.get("candidate"):
        lines.append(f"Next mode:     {status['candidate']} (if it keeps leading for 3 minutes)")
    if status.get("paused_until"):
        lines.append(f"Paused until:  {_clock(status['paused_until'])}")
    if status.get("aside_mode"):
        lines.append("Stepped aside: you picked your own music; back on the next mode change")
    lines.append(f"Last action:   {status.get('last_action') or 'nothing yet'}")
    if status.get("last_error"):
        lines.append(f"Last problem:  {status['last_error']}")
    lines.append(f"Device:        {status.get('device_name') or 'any computer'}")
    history = status.get("history") or {}
    lines.append(
        f"Last 7 days:   {history.get('plays', 0)} plays, {history.get('saved', 0)} saved, "
        f"{history.get('proposed', 0)} proposed"
    )
    return "\n".join(lines)


def _confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _config() -> Config:
    return load_config(paths.config_file())


def _client(cfg: Config):
    import httpx

    from spotifymix.spotify.auth import TokenStore
    from spotifymix.spotify.client import SpotifyClient

    if not cfg.client_id:
        raise ConfigError("no Spotify Client ID configured; run `spotifymix setup`")
    return SpotifyClient(httpx.Client(), cfg.client_id, TokenStore(paths.token_fallback_file()))


def _service(method: str, path: str, body: dict | None = None) -> dict:
    return call_service(
        method, path, body,
        port=paths.read_service_port(paths.config_file()), token=paths.ensure_service_token(),
    )


def _service_command(body: dict) -> int:
    try:
        _service("POST", "/control", body)
    except ServiceDown:
        print("The SpotifyMix service is not running. Start it with `spotifymix service run`.")
        return 1
    except ValueError as exc:
        print(f"The service refused: {exc}", file=sys.stderr)
        return 2
    print("OK")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    try:
        status = _service("GET", "/status")
    except ServiceDown:
        print("The SpotifyMix service is not running. "
              "Start it with `spotifymix service run` or `spotifymix service install`.")
        return 1
    except ValueError as exc:
        print(f"The service refused: {exc}", file=sys.stderr)
        return 1
    print(format_status(status))
    return 0


def cmd_mode(args: argparse.Namespace) -> int:
    cfg = _config()
    if args.mode not in cfg.modes:
        print(f"Unknown mode {args.mode!r}. Choose from: {', '.join(cfg.modes)}", file=sys.stderr)
        return 2
    seconds = args.duration or cfg.manual_default_seconds
    return _service_command({"action": "mode", "mode": args.mode, "seconds": seconds})


def cmd_pause(args: argparse.Namespace) -> int:
    return _service_command({"action": "pause", "seconds": args.duration})


def cmd_resume(args: argparse.Namespace) -> int:
    return _service_command({"action": "resume"})


def cmd_modes(args: argparse.Namespace) -> int:
    cfg = _config()
    for mode in cfg.modes.values():
        fallback = f" (fallback: {', '.join(mode.fallback)})" if mode.fallback else ""
        print(f"{mode.id:<14} {', '.join(mode.genres)}{fallback}")
    print(f"\nOverride genres in {paths.config_file()}, for example:")
    print('[modes.debugging]\ngenres = ["drum and bass"]')
    return 0


def cmd_check_genres(args: argparse.Namespace) -> int:
    cfg = _config()
    client = _client(cfg)
    totals: dict[str, int] = {}
    for mode in cfg.modes.values():
        for genre in mode.genres + mode.fallback:
            if genre not in totals:
                _, totals[genre] = client.search_tracks(f'genre:"{genre}"', limit=1)
        shown = ", ".join(f"{g} ({totals[g]})" for g in mode.genres)
        print(f"{mode.id:<14} {shown}")
    weak = sorted(g for g, n in totals.items() if n < WEAK_GENRE_THRESHOLD)
    if weak:
        print(f"\nGenres with fewer than {WEAK_GENRE_THRESHOLD} results: {', '.join(weak)}")
        return 1
    return 0


def cmd_hooks(args: argparse.Namespace) -> int:
    path = settings_path(paths.claude_config_dir())
    try:
        current = load_settings(path)
        if args.action == "install":
            exe = shutil.which("spotifymix-hook")
            if not exe:
                print("spotifymix-hook was not found on PATH. Install SpotifyMix first.",
                      file=sys.stderr)
                return 2
            new = plan_install(current, hook_command(Path(exe)))
        else:
            new = plan_uninstall(current)
    except SettingsError as exc:
        print(f"Not touching {path}: {exc}", file=sys.stderr)
        return 2
    if new == current:
        print("Nothing to change.")
        return 0
    print(f"Changes to {path}:\n{diff_text(current, new)}")
    if not args.yes and not _confirm("Apply these changes?"):
        print("Aborted, nothing written.")
        return 1
    backup = write_settings(path, new)
    if backup:
        print(f"Backup of the previous file: {backup}")
    print("Done. New Claude Code sessions will use the change.")
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    if args.action == "run":
        from spotifymix.service import run

        return run()
    try:
        if args.action == "install":
            print(autostart.install(autostart.service_executable()))
        else:
            print(autostart.uninstall())
    except autostart.AutostartError as exc:
        print(f"Autostart problem: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    import httpx

    from spotifymix.spotify.auth import AuthError, TokenStore, login_interactive

    cfg = _config()
    if not cfg.client_id:
        print("Run `spotifymix setup` first.", file=sys.stderr)
        return 2
    print("Opening the Spotify login page in your browser...")
    try:
        login_interactive(httpx.Client(), cfg.client_id, cfg.redirect_port,
                          TokenStore(paths.token_fallback_file()))
    except AuthError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    print("Logged in to Spotify.")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    from spotifymix.spotify.auth import TokenStore

    TokenStore(paths.token_fallback_file()).clear()
    print("Spotify login removed from this computer.")
    return 0


def _choose_device(cfg: Config) -> str:
    try:
        computers = [d for d in _client(cfg).devices() if d.type.lower() == "computer"]
    except Exception as exc:  # setup must go on even if Spotify is closed
        print(f"Could not list Spotify devices ({exc}).")
        computers = []
    default = computers[0].name if computers else socket.gethostname()
    for index, device in enumerate(computers, 1):
        print(f"  {index}. {device.name}")
    answer = input(f"Spotify device to follow [{default}]: ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(computers):
        return computers[int(answer) - 1].name
    return answer or default


def cmd_setup(args: argparse.Namespace) -> int:
    from spotifymix.spotify.auth import redirect_uri

    cfg_file = paths.config_file()
    print(SETUP_INTRO.format(redirect=redirect_uri(_config().redirect_port)))
    client_id = input("Client ID: ").strip()
    if not client_id or " " in client_id:
        print("No valid Client ID entered.", file=sys.stderr)
        return 2
    save_user_settings(cfg_file, client_id=client_id)
    if cmd_login(args) != 0:
        return 1
    save_user_settings(cfg_file, device_name=_choose_device(_config()))
    paths.ensure_service_token()
    print(f"Settings saved in {cfg_file}")
    if _confirm("Add the SpotifyMix hooks to Claude Code now?"):
        cmd_hooks(argparse.Namespace(action="install", yes=False))
    if _confirm("Start SpotifyMix automatically when you log in?"):
        cmd_service(argparse.Namespace(action="install"))
    print("Setup complete. Play something in Spotify on this computer, "
          "then run `spotifymix status`.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    cmd_hooks(argparse.Namespace(action="uninstall", yes=args.yes))
    cmd_service(argparse.Namespace(action="uninstall"))
    print("Your settings and history are kept in:")
    print(f"  {paths.config_dir()}\n  {paths.data_dir()}")
    print("Delete those folders and run `spotifymix logout` to remove everything.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spotifymix", description="Spotify music that follows your Claude Code activity.")
    parser.add_argument("--version", action="version", version=f"spotifymix {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, text in (
        ("setup", cmd_setup, "guided first-time setup"),
        ("login", cmd_login, "log in to Spotify again"),
        ("logout", cmd_logout, "remove the Spotify login from this computer"),
        ("status", cmd_status, "show what the service is doing"),
        ("resume", cmd_resume, "end a pause or manual mode"),
        ("modes", cmd_modes, "list modes and genres"),
        ("check-genres", cmd_check_genres, "count Spotify results for each genre"),
    ):
        sub.add_parser(name, help=text).set_defaults(func=func)
    mode = sub.add_parser("mode", help="force a mode for a while")
    mode.add_argument("mode")
    mode.add_argument("--for", dest="duration", type=parse_duration, default=None)
    mode.set_defaults(func=cmd_mode)
    pause = sub.add_parser("pause", help="stop changing music for a while")
    pause.add_argument("duration", type=parse_duration)
    pause.set_defaults(func=cmd_pause)
    hooks = sub.add_parser("hooks", help="add or remove the Claude Code hooks")
    hooks.add_argument("action", choices=["install", "uninstall"])
    hooks.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    hooks.set_defaults(func=cmd_hooks)
    service = sub.add_parser("service", help="run the service or manage autostart")
    service.add_argument("action", choices=["run", "install", "uninstall"])
    service.set_defaults(func=cmd_service)
    uninstall = sub.add_parser("uninstall", help="remove hooks and autostart")
    uninstall.add_argument("--yes", action="store_true")
    uninstall.set_defaults(func=cmd_uninstall)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows consoles may not be UTF-8
        except (AttributeError, ValueError):
            pass
    sys.exit(run())
```

- [ ] **Step 4: Run tests and lint**

Run: `uv run pytest -q && uv run ruff check`
Expected: all passed.

- [ ] **Step 5: Smoke-test the installed commands**

Run:
```bash
uv run spotifymix --version
uv run spotifymix modes
echo '{"hook_event_name":"Stop"}' | uv run spotifymix-hook; echo "exit=$?"
```
Expected: `spotifymix 0.1.0`; the list of 11 modes; `exit=0` with no output.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: spotifymix command line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: README and spec alignment

**Files:**
- Modify: `README.md` (replace entirely)
- Modify: `docs/superpowers/specs/2026-09-30-spotifymix-design.md` (command names)

- [ ] **Step 1: Replace `README.md`**

````markdown
# SpotifyMix

SpotifyMix watches what you are doing in [Claude Code](https://claude.com/claude-code)
(coding, debugging, planning, reviewing, ...) and queues fresh Spotify tracks that fit the
activity. One rule is never broken: **no track you listened to, saved, or were offered in the
last 7 days**.

It runs locally as a small background service. There is no server, no telemetry and no secret
to leak: login uses Spotify's PKCE flow, which needs no client secret.

## Requirements

- Spotify Premium (Spotify requires it for playback control and for owning a developer app).
- Your own free Spotify developer app (Spotify limits each app to 5 users, so everyone creates one).
- Python 3.11+ and [uv](https://docs.astral.sh/uv/).
- Claude Code.
- Windows is tested on a real machine. macOS and Linux are covered by automated tests only.

## Install

```bash
uv tool install git+https://github.com/caesla/spotifymix
spotifymix setup
```

`setup` walks you through creating the Spotify app, logs you in, asks which Spotify device to
follow, shows the exact change it wants to make to your Claude Code settings before writing it
(a backup is saved), and can register the service to start at login.

## How it works

1. Claude Code runs `spotifymix-hook` asynchronously on tool use, failures, prompts and
   subagent starts. The hook forwards the event to the local service and exits at once; if the
   service is down, Claude Code does not notice.
2. The service turns each event into a vote for a mode. The mode with most votes in the last
   3 minutes wins; a new mode must lead for 3 minutes before the music changes.
3. About 20 seconds before the current track ends, SpotifyMix queues **one** track of the
   current mode's genre, found with Spotify search and filtered against your last 7 days.
4. It only acts while music is playing on this computer. Paused, or playing on your phone:
   it does nothing. Pick your own playlist and it steps aside until the next mode change.

| Mode | Genres | Typical signal |
|---|---|---|
| planning | ambient | plan mode, planning skills |
| brainstorming | post-rock, nu jazz | brainstorming skills, multiple choice questions |
| coding | deep house, tech house | editing code |
| debugging | hip hop, rap | failed tools, debugging skills |
| testing | techno | running tests |
| reviewing | lo-fi hip hop | review skills, `git diff` |
| exploring | downtempo, trip hop | reading files, web research |
| writing | deep house, tech house | editing Markdown and documents |
| ui | synthwave, nu disco | editing styles and pages, browser tools |
| release | funk, disco | commit, push, pull requests, deploy |
| orchestrating | deep house, tech house | several subagents |

Change genres or rules in your `config.toml` (`spotifymix modes` prints its location), then
run `spotifymix check-genres` to see how many Spotify results each genre label returns.

## Commands

```text
spotifymix status               what the service is doing
spotifymix mode debugging --for 30m
spotifymix pause 1h             stop changing music for a while
spotifymix resume               end a pause or a manual mode
spotifymix modes                list modes and genres
spotifymix check-genres         count Spotify results per genre label
spotifymix hooks install|uninstall
spotifymix service run|install|uninstall
spotifymix login | logout
spotifymix uninstall            remove hooks and autostart
```

## Privacy

- Settings, history and logs live in your user data folders, never in this repository.
- The Spotify refresh token is stored in the OS keyring (a private file only if no keyring exists).
- Hook events are processed in memory. Only the event name, the mode and the time are saved;
  prompt text and file paths are never written to disk.
- The service listens on `127.0.0.1` only and requires a local random token.

## Limitations

- The 7-day rule covers what SpotifyMix could see: Spotify only returns your last 50 plays, so
  plays on other devices while this computer was off for many hours can be missed.
- Spotify no longer offers recommendations to new apps, so discovery is based on genre search.
- Spotify's genre labels vary; use `check-genres` and adjust labels in `config.toml`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

Design document (Italian): [docs/superpowers/specs/2026-09-30-spotifymix-design.md](docs/superpowers/specs/2026-09-30-spotifymix-design.md)

## License

MIT
````

- [ ] **Step 2: Align the spec's command names**

In `docs/superpowers/specs/2026-09-30-spotifymix-design.md` apply these replacements:
- In section 1, list "Richiesto esplicitamente": leave as is.
- In the components table (section 3), row `cli`: replace `` `setup`, `stato`, `modo <nome>`, `pausa <durata>`, `riprendi`, `service install/uninstall` `` with `` `setup`, `status`, `mode <nome>`, `pause <durata>`, `resume`, `service install/uninstall` (nomi in inglese per il pubblico internazionale) ``.
- In section 6, point 4: replace `` `spotifymix modo debug` `` with `` `spotifymix mode debugging` `` and `` `spotifymix riprendi` `` with `` `spotifymix resume` ``.
- In section 7.4: replace `` `spotifymix riprendi` `` with `` `spotifymix resume` ``.
- In sections 8, 10 and 14: replace every `` `spotifymix stato` `` with `` `spotifymix status` ``.

Then check that no Italian command name is left:
Run: `grep -nE "spotifymix (stato|modo|pausa|riprendi)" docs/superpowers/specs/2026-09-30-spotifymix-design.md || echo clean`
Expected: `clean`.

- [ ] **Step 3: Check the repository for personal data and dashes**

Run:
```bash
git grep -nIiE "gmail|[A-Z]:\\\\Users\\\\|/home/[a-z]|claude-config" -- . ':!docs/superpowers/plans' || echo "no personal data"
git grep -nP "\x{2014}|\x{2013}" -- . || echo "no dashes"
```
Expected: `no personal data` and `no dashes`. (The plan is excluded from the first search because it contains this very command; the GitHub noreply address in `CLAUDE.md` is public on purpose and is not searched for.)

- [ ] **Step 4: Run everything and commit**

```bash
uv run pytest -q && uv run ruff check
git add -A
git commit -m "docs: README and spec command names

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Push and confirm CI**

```bash
git push -u origin HEAD
gh run watch --exit-status
```
Expected: the CI workflow passes on ubuntu, windows and macos, and the gitleaks job passes. If a job fails, read its log with `gh run view --log-failed`, fix, commit, push again. Open a pull request to `main` with `gh pr create --fill` if you are on a branch.

---

### Task 16: Local verification on the author's PC (LOCAL ONLY, not in a cloud session)

This task needs a real Spotify account, a browser, the Spotify desktop app and the author's
Claude Code installation. A cloud session must stop after Task 15 and report. Every step that
changes the author's global configuration requires the author's explicit confirmation first.

- [ ] **Step 1: Install the merged build**

```bash
git checkout main && git pull
uv tool install --force .
spotifymix --version
```
Expected: `spotifymix 0.1.0`.

- [ ] **Step 2: Capture real hook payloads (project-local, temporary)**

Create `.claude/settings.local.json` in this repository (git-ignored) with a capture hook that
appends each payload to a file in the session scratchpad, for the events `PreToolUse`,
`PostToolUseFailure`, `UserPromptSubmit` and `SubagentStart`. Open a new Claude Code session in
this repository, make it read a file, edit a file, run a failing command, invoke a skill and
start an Explore subagent. Then compare the captured payloads with the fields used in
`defaults.toml` (`tool_name`, `tool_input.skill`, `tool_input.command`, `tool_input.file_path`,
`tool_input.subagent_type`, `agent_type`, `permission_mode`, `prompt`). Fix any rule whose field
differs, add a test in `tests/test_classifier.py` with an anonymized copy of the payload, then
delete the capture hook and the captured files. Never commit a raw payload.

- [ ] **Step 3: Spotify app and login (author)**

The author creates the app at https://developer.spotify.com/dashboard with Redirect URI
`http://127.0.0.1:47616/callback`, then runs `spotifymix setup` and pastes the Client ID. The
Client ID is typed by the author into the terminal; it is not pasted into the chat.

- [ ] **Step 4: Verify genre labels**

Run: `spotifymix check-genres`
For each label with fewer than 50 results, pick the closest label that returns more, update
`defaults.toml`, run `spotifymix check-genres` again, and list every replacement for the author.

- [ ] **Step 5: Install the global hooks (author confirms)**

Show the author the diff printed by `spotifymix hooks install` (it targets
`$CLAUDE_CONFIG_DIR/settings.json`) and apply it only after an explicit yes.

- [ ] **Step 6: Autostart**

Run: `spotifymix service install`, then `spotifymix status`.
Expected: the Task Scheduler line, then a status with `Mode:` and `Device:`. Check in Task
Manager that `spotifymix-service` runs without a console window.

- [ ] **Step 7: End-to-end checklist (spec section 14)**

1. `spotifymix status` shows no problem.
2. Work in Claude Code editing code for 5 minutes, then trigger failing commands for 5 minutes:
   the genre switches on the next track after the 3 minute threshold, then switches back.
3. Every proposed track is absent from the 7 days of plays and saved tracks before it was
   proposed. From the repository checkout run:
   ```bash
   uv run python -c "import sqlite3; from spotifymix import paths; c = sqlite3.connect(paths.db_file()); q = 'SELECT COUNT(*) FROM proposed p JOIN {t} l ON (l.track_id = p.track_id OR l.name_key = p.name_key) AND l.{c} < p.proposed_at AND l.{c} >= p.proposed_at - 604800'; print(c.execute(q.format(t='plays', c='played_at')).fetchone()[0], c.execute(q.format(t='saved', c='added_at')).fetchone()[0])"
   ```
   Expected: `0 0`.
4. Pause Spotify and work 10 minutes: nothing is queued (`Last action` unchanged).
5. Play on the phone: nothing is queued.
6. Stop the service (`schtasks /End /TN SpotifyMix`): Claude Code shows no delay or error.
7. Reboot: the service is running again (`spotifymix status`).
8. `git log --format='%an <%ae>' | sort -u` prints exactly one line,
   `caesla <219047344+caesla@users.noreply.github.com>`, and the gitleaks CI job is green.
9. The hook really runs on Windows: after a few tool calls in a new Claude Code session,
   `spotifymix status` shows a mode other than `none yet` (spec section 11, item 2).
10. Quota: after a full working day, search `spotifymix.log` in the folder printed by
    `uv run python -c "from spotifymix import paths; print(paths.log_dir())"` for
    `rate limit`; report how many times it appears (spec section 11, item 6).

Report each item with its output to the author.
