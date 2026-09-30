"""The `codingmix` command."""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import sys
import time
from pathlib import Path

from codingmix import __version__, autostart, paths
from codingmix.claude_settings import (
    SettingsError,
    diff_text,
    hook_command,
    load_settings,
    plan_install,
    plan_uninstall,
    settings_path,
    write_settings,
)
from codingmix.config import Config, ConfigError, load_config, save_user_settings
from codingmix.server import ServiceDown, call_service

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
WEAK_GENRE_THRESHOLD = 50

SETUP_INTRO = """CodingMix setup

1. Open https://developer.spotify.com/dashboard and create an app
   (your account needs Spotify Premium). Name it for example CodingMix:
   Spotify does not allow the word "Spotify" in app names.
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


def _confirm(question: str, default: bool = False) -> bool:
    answer = input(f"{question} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _config() -> Config:
    return load_config(paths.config_file())


def _client(cfg: Config):
    import httpx

    from codingmix.spotify.auth import TokenStore
    from codingmix.spotify.client import SpotifyClient

    if not cfg.client_id:
        raise ConfigError("no Spotify Client ID configured; run `codingmix setup`")
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
        print("The CodingMix service is not running. Start it with `codingmix service run`.")
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
        print("The CodingMix service is not running. "
              "Start it with `codingmix service run` or `codingmix service install`.")
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


def cmd_hooks(args: argparse.Namespace, apply_by_default: bool = False) -> int:
    path = settings_path(paths.claude_config_dir())
    try:
        current = load_settings(path)
        if args.action == "install":
            exe = shutil.which("codingmix-hook")
            if not exe:
                print("codingmix-hook was not found on PATH. Install CodingMix first.",
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
    if not args.yes and not _confirm("Apply these changes?", default=apply_by_default):
        print("Aborted, nothing written.")
        return 1
    backup = write_settings(path, new)
    if backup:
        print(f"Backup of the previous file: {backup}")
    print("Done. New Claude Code sessions will use the change.")
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    if args.action == "run":
        from codingmix.service import run

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

    from codingmix.spotify.auth import AuthError, TokenStore, login_interactive

    cfg = _config()
    if not cfg.client_id:
        print("Run `codingmix setup` first.", file=sys.stderr)
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
    from codingmix.spotify.auth import TokenStore

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
    from codingmix.spotify.auth import redirect_uri

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
    missing = []
    if cmd_hooks(argparse.Namespace(action="install", yes=False), apply_by_default=True) != 0:
        missing.append("Claude Code hooks: run `codingmix hooks install`")
    if not (_confirm("Start CodingMix automatically when you log in?", default=True)
            and cmd_service(argparse.Namespace(action="install")) == 0):
        missing.append("automatic start: run `codingmix service install`")
    if missing:
        print("Setup is not finished. CodingMix will not play anything until you add:")
        for item in missing:
            print(f"  - {item}")
        return 0
    print("Setup complete. Play something in Spotify on this computer, "
          "then run `codingmix status`.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    cmd_hooks(argparse.Namespace(action="uninstall", yes=args.yes))
    cmd_service(argparse.Namespace(action="uninstall"))
    print("Your settings and history are kept in:")
    for folder in dict.fromkeys((paths.config_dir(), paths.data_dir())):
        print(f"  {folder}")
    print("Delete those folders and run `codingmix logout` to remove everything.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codingmix", description="Spotify music that follows your Claude Code activity.")
    parser.add_argument("--version", action="version", version=f"codingmix {__version__}")
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
    from codingmix.spotify.auth import AuthError
    from codingmix.spotify.client import SpotifyError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except (SpotifyError, AuthError) as exc:
        print(f"Spotify problem: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows consoles may not be UTF-8
        except (AttributeError, ValueError):
            pass
    sys.exit(run())
