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
