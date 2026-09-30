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
