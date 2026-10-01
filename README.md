# CodingMix

CodingMix watches what you are doing in [Claude Code](https://claude.com/claude-code)
(coding, debugging, planning, reviewing, ...) and queues fresh Spotify tracks that fit the
activity. One rule is never broken: **no track you listened to, saved, or were offered in the
last 7 days**.

**Spotify only.** CodingMix works with the Spotify desktop app and a Spotify Premium account.
It does not support Apple Music, Amazon Music, SoundCloud, YouTube Music or other services.

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
uv tool install git+https://github.com/caesla/CodingMix
codingmix setup
```

`setup` walks you through creating the Spotify app, logs you in, asks which Spotify device to
follow, shows the exact change it wants to make to your Claude Code settings before writing it
(a backup is saved), and registers the service to start at login. Both steps are accepted
with Enter; if you skip one, `setup` prints the command to add it later.

## How it works

1. Claude Code runs `codingmix-hook` asynchronously on tool use, failures, prompts and
   subagent starts. The hook forwards the event to the local service and exits at once; if the
   service is down, Claude Code does not notice.
2. The service turns each event into a vote for a mode. The mode with most votes in the last
   3 minutes wins; a new mode must lead for 3 minutes before the music changes.
3. About 20 seconds before the current track ends, CodingMix queues **one** track of the
   current mode's genre, found with Spotify search and filtered against your last 7 days.
4. It only acts while music is playing on this computer. Paused, or playing on your phone:
   it does nothing. Pick your own playlist and it steps aside until the next mode change.

| Mode | Genres | Typical signal |
|---|---|---|
| planning | deep house, tech house | plan mode, planning skills |
| brainstorming | melodic house, progressive house | brainstorming skills, multiple choice questions |
| coding | deep house, tech house | editing code |
| debugging | french house, disco house, funky house | failed tools, debugging skills |
| testing | techno | running tests |
| reviewing | deep house, tech house | review skills, `git diff` |
| exploring | minimal house, ambient house | reading files, web research |
| writing | deep house, tech house | editing Markdown and documents |
| ui | synthwave, disco house | editing styles and pages, browser tools |
| release | funk, disco | commit, push, pull requests, deploy |
| orchestrating | deep house, tech house | several subagents |

Change genres or rules in your `config.toml` (`codingmix modes` prints its location), then
run `codingmix check-genres` to see how many Spotify results each genre label returns.

## Commands

```text
codingmix status               what the service is doing
codingmix mode debugging --for 30m
codingmix pause 1h             stop changing music for a while
codingmix resume               end a pause or a manual mode
codingmix modes                list modes and genres
codingmix check-genres         count Spotify results per genre label
codingmix hooks install|uninstall
codingmix service run|install|uninstall
codingmix login | logout
codingmix uninstall            remove hooks and autostart
```

## Privacy

- Settings, history and logs live in your user data folders, never in this repository.
- The Spotify refresh token is stored in the OS keyring (a private file only if no keyring exists).
- Hook events are processed in memory. Only the event name, the mode and the time are saved;
  prompt text and file paths are never written to disk.
- The service listens on `127.0.0.1` only and requires a local random token.

## Limitations

- The 7-day rule covers what CodingMix could see: Spotify only returns your last 50 plays, so
  plays on other devices while this computer was off for many hours can be missed.
- Spotify no longer offers recommendations to new apps, so discovery is based on genre search.
- Spotify's genre labels vary; use `check-genres` and adjust labels in `config.toml`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

Design document (Italian): [docs/superpowers/specs/2026-09-30-codingmix-design.md](docs/superpowers/specs/2026-09-30-codingmix-design.md)

## License

MIT. CodingMix is an independent project for Spotify, not affiliated with or endorsed by
Spotify.
