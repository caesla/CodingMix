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
