"""Fixtures shared by the engine tests (``usefixtures`` in each module; the older phone tests do not use them)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from phone import httpgate, runner

ENV_NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "PORCHLAMP_GH", "PORCHLAMP_GH_API", "PORCHLAMP_ALLOW_TEST_API",
             "PORCHLAMP_RATE_FLOOR", "PORCHLAMP_ENGINE", "PORCHLAMP_DATA_ROOT",
             "PORCHLAMP_GH_INFLIGHT", "PORCHLAMP_GH_PPM")   # D4: the engine sets the GitHub throttle's settings


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
