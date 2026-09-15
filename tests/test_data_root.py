"""Tests for cvi.paths.data_root and its use in scripts/gather.py and
scripts/derive.py.

The "override honoured" tests pin that both scripts can be pointed at a
data root other than the checkout's own ``data/``, via env var or flag.
The "default unchanged" tests are the over-correction guard: the switch
must be inert when unset.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cvi.paths import CVI_DATA_ROOT_ENV, REPO_ROOT, data_root

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_data_root_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CVI_DATA_ROOT_ENV, raising=False)
    assert data_root() == REPO_ROOT / "data"


def test_override_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch-root"
    monkeypatch.setenv(CVI_DATA_ROOT_ENV, str(scratch))
    assert data_root() == scratch.resolve()


def test_override_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CVI_DATA_ROOT_ENV, "~/cvi-does-not-need-to-exist")
    assert data_root() == Path.home() / "cvi-does-not-need-to-exist"


def test_gather_default_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CVI_DATA_ROOT_ENV, raising=False)
    gather = _load_script("gather")
    assert gather.DATA == REPO_ROOT / "data" / "raw"


def test_gather_override_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch-root"
    monkeypatch.setenv(CVI_DATA_ROOT_ENV, str(scratch))
    gather = _load_script("gather")
    assert gather.DATA == scratch.resolve() / "raw"


def test_derive_default_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CVI_DATA_ROOT_ENV, raising=False)
    derive = _load_script("derive")
    assert derive.DATA_DIR == REPO_ROOT / "data"


def test_derive_override_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch-root"
    monkeypatch.setenv(CVI_DATA_ROOT_ENV, str(scratch))
    derive = _load_script("derive")
    assert derive.DATA_DIR == scratch.resolve()
