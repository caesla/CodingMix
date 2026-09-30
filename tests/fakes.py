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
