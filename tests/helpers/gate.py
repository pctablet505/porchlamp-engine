"""Test helper: a gate that also admits the loopback address of a fake server, installed for the length of a test."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from phone import httpgate

LOOPBACK = "127.0.0.1"


def loopback_gate(**kwargs: Any) -> httpgate.Gate:
    """A gate with the real allow-list plus 127.0.0.1 (plain http, any port, and it may receive the test token)."""
    kwargs.setdefault("allowed", httpgate.PHONE_HOSTS | {LOOPBACK})
    kwargs.setdefault("http_hosts", frozenset({LOOPBACK}))
    kwargs.setdefault("token_hosts", httpgate.TOKEN_HOSTS | {LOOPBACK})
    kwargs.setdefault("budget_hosts", frozenset({LOOPBACK}))
    return httpgate.Gate(**kwargs)


@contextmanager
def installed(gate: httpgate.Gate) -> Iterator[httpgate.Gate]:
    old = httpgate.install(gate)
    try:
        yield gate
    finally:
        httpgate.install(old)
