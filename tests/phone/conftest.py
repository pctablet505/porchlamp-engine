"""Fixtures shared by the engine tests (``usefixtures`` in each module; the older phone tests do not use them)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from phone import httpgate, runner

ENV_NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "PORCHLAMP_GH", "PORCHLAMP_GH_API", "PORCHLAMP_ALLOW_TEST_API",
             "PORCHLAMP_RATE_FLOOR", "PORCHLAMP_ENGINE", "PORCHLAMP_DATA_ROOT")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No engine environment variable leaks in or out, and the process gate is put back afterwards."""
    saved = {name: os.environ.pop(name, None) for name in ENV_NAMES}
    old_gate = httpgate.install(httpgate.Gate())
    monkeypatch.setattr(runner, "_GATHER_CACHE", {})
    try:
        yield
    finally:
        httpgate.install(old_gate)
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture
def utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Local time is UTC so clock times and days in the assertions do not depend on the machine."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def files_dir(tmp_path: Path) -> Path:
    return tmp_path / "files"


def _install_darwin_loopback_shim() -> None:
    """On macOS, lo0 only assigns 127.0.0.1 by default (unlike Linux where 127.0.0.0/8 routes to lo).
    When 127.0.0.2 cannot be bound natively, emulate 127.0.0.x (x > 1) over 127.0.0.1 for in-process socket tests."""
    import socket as _socket

    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.2", 0))
        return
    except OSError:
        pass
    finally:
        probe.close()

    real_socket = _socket.socket
    listeners: dict[int, str] = {}
    sources: dict[int, str] = {}

    def _is_loopback_v4(host: object) -> bool:
        return isinstance(host, str) and host.startswith("127.0.0.") and host != "127.0.0.0"

    class LoopbackSocket(real_socket):
        def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
            super().__init__(family, type, proto, fileno)
            self._virtual_bind_host: str | None = None
            self._bound_port: int | None = None
            self._is_listener: bool = False

        def bind(self, address):
            if isinstance(address, tuple) and len(address) == 2:
                host, port = address
                if _is_loopback_v4(host) and host != "127.0.0.1":
                    super().bind(("127.0.0.1", port))
                    actual_port = super().getsockname()[1]
                    self._virtual_bind_host = host
                    self._bound_port = actual_port
                    sources[actual_port] = host
                    return
                super().bind(address)
                actual_port = super().getsockname()[1]
                self._virtual_bind_host = "0.0.0.0" if host in ("", "0.0.0.0") else host
                self._bound_port = actual_port
                return
            super().bind(address)

        def listen(self, *args):
            super().listen(*args)
            if self._bound_port is not None and self._virtual_bind_host is not None:
                self._is_listener = True
                sources.pop(self._bound_port, None)
                listeners[self._bound_port] = self._virtual_bind_host

        def getsockname(self):
            name = super().getsockname()
            if isinstance(name, tuple) and len(name) == 2 and self._virtual_bind_host and _is_loopback_v4(self._virtual_bind_host):
                return (self._virtual_bind_host, name[1])
            return name

        def connect(self, address):
            if isinstance(address, tuple) and len(address) == 2:
                host, port = address
                if _is_loopback_v4(host):
                    bound_host = listeners.get(port)
                    if bound_host is not None and bound_host != "0.0.0.0" and bound_host != host:
                        raise ConnectionRefusedError(61, "Connection refused")
                    if host != "127.0.0.1":
                        if bound_host is None:
                            raise ConnectionRefusedError(61, "Connection refused")
                        return super().connect(("127.0.0.1", port))
            return super().connect(address)

        def accept(self):
            fd, addr = self._accept()
            sock = LoopbackSocket(self.family, self.type, self.proto, fileno=fd)
            if _socket.getdefaulttimeout() is None and self.gettimeout():
                sock.setblocking(True)
            if isinstance(addr, tuple) and len(addr) == 2:
                src_ip = sources.get(addr[1])
                if src_ip is not None:
                    addr = (src_ip, addr[1])
            return sock, addr

        def close(self):
            if self._is_listener and self._bound_port is not None:
                if listeners.get(self._bound_port) == self._virtual_bind_host:
                    listeners.pop(self._bound_port, None)
            elif self._bound_port is not None:
                sources.pop(self._bound_port, None)
            super().close()

    _socket.socket = LoopbackSocket  # type: ignore[misc]


_install_darwin_loopback_shim()
