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
