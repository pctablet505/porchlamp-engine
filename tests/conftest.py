"""Shared pytest configuration.

The package is installed editable into the venv (``pip install -e .``), so
``import porchlamp`` resolves from anywhere. This file is intentionally minimal to
avoid coupling the per-module test suites to shared fixtures.

The one exception is :func:`block_live_network`: a shared, opt-in fixture that
a test file requests explicitly (it is not autouse, so it changes nothing for a
file that does not ask for it). Requesting it makes any real (non-loopback)
socket connection or ``subprocess.Popen`` raise immediately. Raising alone is
not enough: the code under test here is ``scripts/gather.py``'s ``gather_repo``,
and every one of its per-collector calls is wrapped in a broad
``except Exception`` that swallows exceptions and records
``status[collector] = "fail"`` -- a test that never asserts that status would
still pass with the call blocked and unnoticed. So the fixture also records
every blocked attempt and fails the test itself at teardown if any occurred,
independent of whatever the test body asserted. Mark an individual test with
``@pytest.mark.live_network`` to opt that one test back into real network access
(the fixture is a no-op for it).
"""
from __future__ import annotations

import socket
import subprocess
from typing import Any

import pytest

def _install_darwin_loopback_shim() -> None:
    """On macOS, lo0 only assigns 127.0.0.1 by default (unlike Linux where 127.0.0.0/8 routes to lo).
    When 127.0.0.2 cannot be bound natively, emulate 127.0.0.x (x > 1) over 127.0.0.1 for in-process socket tests."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.2", 0))
        return
    except OSError:
        pass
    finally:
        probe.close()

    real_socket = socket.socket
    listeners: dict[int, str] = {}
    sources: dict[int, str] = {}

    def _is_loopback_v4(host: object) -> bool:
        return isinstance(host, str) and host.startswith("127.0.0.") and host != "127.0.0.0"

    class LoopbackSocket(real_socket):
        def __init__(self, family: int = -1, type: int = -1, proto: int = -1, fileno: Any = None) -> None:
            super().__init__(family, type, proto, fileno)
            self._virtual_bind_host: str | None = None
            self._bound_port: int | None = None
            self._is_listener: bool = False

        def bind(self, address: Any) -> None:
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

        def listen(self, *args: Any) -> None:
            super().listen(*args)
            if self._bound_port is not None and self._virtual_bind_host is not None:
                self._is_listener = True
                sources.pop(self._bound_port, None)
                listeners[self._bound_port] = self._virtual_bind_host

        def getsockname(self) -> Any:
            name = super().getsockname()
            if isinstance(name, tuple) and len(name) == 2 and self._virtual_bind_host and _is_loopback_v4(self._virtual_bind_host):
                return (self._virtual_bind_host, name[1])
            return name

        def connect(self, address: Any) -> None:
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

        def connect_ex(self, address: Any) -> int:
            if isinstance(address, tuple) and len(address) == 2:
                host, port = address
                if _is_loopback_v4(host):
                    bound_host = listeners.get(port)
                    if bound_host is not None and bound_host != "0.0.0.0" and bound_host != host:
                        return 61
                    if host != "127.0.0.1":
                        if bound_host is None:
                            return 61
                        return super().connect_ex(("127.0.0.1", port))
            return super().connect_ex(address)

        def accept(self) -> tuple[Any, Any]:
            fd, addr = self._accept()  # type: ignore[attr-defined]
            sock = LoopbackSocket(self.family, self.type, self.proto, fileno=fd)
            if socket.getdefaulttimeout() is None and self.gettimeout():
                sock.setblocking(True)
            if isinstance(addr, tuple) and len(addr) == 2:
                src_ip = sources.get(addr[1])
                if src_ip is not None:
                    addr = (src_ip, addr[1])
            return sock, addr

        def close(self) -> None:
            if self._is_listener and self._bound_port is not None:
                if listeners.get(self._bound_port) == self._virtual_bind_host:
                    listeners.pop(self._bound_port, None)
            elif self._bound_port is not None:
                sources.pop(self._bound_port, None)
            super().close()

    socket.socket = LoopbackSocket  # type: ignore[misc]


_install_darwin_loopback_shim()

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_POPEN_INIT = subprocess.Popen.__init__

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _address_host(address: Any) -> Any:
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def _is_loopback_host(host: Any) -> bool:
    if host in _LOOPBACK:
        return True
    return isinstance(host, str) and host.startswith("127.") and host != "127.0.0.0"


class LiveNetworkBlocked(RuntimeError):
    """Raised by :func:`block_live_network` when a test attempts a real
    (non-loopback) network call without opting in via
    ``@pytest.mark.live_network``."""


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    if _is_loopback_host(_address_host(address)):
        return _REAL_CONNECT(self, address)
    _ATTEMPTS.append(f"socket.connect {address!r}")
    raise LiveNetworkBlocked(f"blocked live socket connect to {address!r}")


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    if _is_loopback_host(_address_host(address)):
        return _REAL_CONNECT_EX(self, address)
    _ATTEMPTS.append(f"socket.connect_ex {address!r}")
    raise LiveNetworkBlocked(f"blocked live socket connect_ex to {address!r}")


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    if _is_loopback_host(_address_host(address)):
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)
    _ATTEMPTS.append(f"socket.create_connection {address!r}")
    raise LiveNetworkBlocked(f"blocked live socket.create_connection to {address!r}")


def _guarded_popen_init(self: subprocess.Popen, args: Any, *pargs: Any, **kwargs: Any) -> None:
    cmd = args if isinstance(args, (list, tuple)) else [args]
    _ATTEMPTS.append(f"subprocess.Popen {tuple(cmd)!r}")
    raise LiveNetworkBlocked(f"blocked live subprocess: {cmd!r}")


#: Attempts blocked during the currently-running test (module-level so it
#: survives being read after the guarded call was itself caught and
#: swallowed by the code under test's own ``except Exception``).
_ATTEMPTS: list[str] = []


@pytest.fixture()
def block_live_network(request: pytest.FixtureRequest) -> Any:
    """Opt-in guard: raise on any real socket connect or ``subprocess.Popen``
    for the duration of one test, unless the test carries
    ``@pytest.mark.live_network``. Loopback connections (a test's own
    ``ThreadingHTTPServer``, for instance) are always allowed.

    Fails the test at teardown if any attempt was blocked, even when the
    code under test caught and swallowed the raise itself (the exact way
    ``gather_repo``'s per-collector ``except Exception`` does) -- a leak
    must never pass silently just because nothing in the test body asserted
    the resulting "fail" status.
    """
    if request.node.get_closest_marker("live_network") is not None:
        yield
        return
    del _ATTEMPTS[:]
    prev_connect = socket.socket.connect
    prev_connect_ex = socket.socket.connect_ex
    prev_create_conn = socket.create_connection
    prev_popen_init = subprocess.Popen.__init__
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.create_connection = _guarded_create_connection
    subprocess.Popen.__init__ = _guarded_popen_init
    try:
        yield
    finally:
        socket.socket.connect = prev_connect
        socket.socket.connect_ex = prev_connect_ex
        socket.create_connection = prev_create_conn
        subprocess.Popen.__init__ = prev_popen_init
    if _ATTEMPTS:
        attempts, count = list(_ATTEMPTS), len(_ATTEMPTS)
        del _ATTEMPTS[:]
        raise AssertionError(
            f"{count} live network attempt(s) blocked during this test "
            f"(caught internally, so no other assertion saw it): {attempts}")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_network: opt this test back into real network access under "
        "the block_live_network fixture (default: none needed).",
    )
