"""Shared pytest configuration.

The package is installed editable into the venv (``pip install -e .``), so
``import cvi`` resolves from anywhere. This file is intentionally minimal to
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

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_POPEN_INIT = subprocess.Popen.__init__

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _address_host(address: Any) -> Any:
    if isinstance(address, tuple) and address:
        return address[0]
    return address


class LiveNetworkBlocked(RuntimeError):
    """Raised by :func:`block_live_network` when a test attempts a real
    (non-loopback) network call without opting in via
    ``@pytest.mark.live_network``."""


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    if _address_host(address) in _LOOPBACK:
        return _REAL_CONNECT(self, address)
    _ATTEMPTS.append(f"socket.connect {address!r}")
    raise LiveNetworkBlocked(f"blocked live socket connect to {address!r}")


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    if _address_host(address) in _LOOPBACK:
        return _REAL_CONNECT_EX(self, address)
    _ATTEMPTS.append(f"socket.connect_ex {address!r}")
    raise LiveNetworkBlocked(f"blocked live socket connect_ex to {address!r}")


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    if _address_host(address) in _LOOPBACK:
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
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket.create_connection = _guarded_create_connection
    subprocess.Popen.__init__ = _guarded_popen_init
    try:
        yield
    finally:
        socket.socket.connect = _REAL_CONNECT
        socket.socket.connect_ex = _REAL_CONNECT_EX
        socket.create_connection = _REAL_CREATE_CONNECTION
        subprocess.Popen.__init__ = _REAL_POPEN_INIT
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
