"""`reproduce._scripts` must not cache a pipeline script that failed to load.

It registers each script in ``sys.modules`` before executing it. When the
execution raised (found 2026-09-23: CI's environment had no uvicorn, which
``scripts/promote.py`` reaches through ``scripts/serve.py``), the half-built
module stayed registered, every later call got it back, and the real
``ModuleNotFoundError`` surfaced as ``no attribute 'load_clean'`` in 15
unrelated tests.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# the package exports a *function* named reproduce, which shadows the submodule
reproduce = importlib.import_module("porchlamp.snapshot.reproduce")

NAMES = tuple(f"porchlamp_snapshot_{name}" for name in ("derive", "verify", "promote"))


@pytest.fixture
def isolated_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the loader at fake scripts and leave ``sys.modules`` as it was found."""
    saved = {name: sys.modules.pop(name) for name in NAMES if name in sys.modules}
    monkeypatch.setattr(reproduce, "_SCRIPTS", tmp_path)
    (tmp_path / "verify.py").write_text("", encoding="utf-8")
    (tmp_path / "promote.py").write_text("def load_clean(work):\n    return []\n", encoding="utf-8")
    yield tmp_path
    for name in NAMES:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def test_a_script_that_fails_to_load_fails_the_same_way_every_time(isolated_scripts: Path) -> None:
    (isolated_scripts / "derive.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'uvicorn'\")\n", encoding="utf-8"
    )
    for _attempt in range(2):
        with pytest.raises(ModuleNotFoundError, match="uvicorn"):
            reproduce._scripts()
        assert "porchlamp_snapshot_derive" not in sys.modules


def test_a_script_that_loads_is_loaded_once(isolated_scripts: Path) -> None:
    (isolated_scripts / "derive.py").write_text("LOADS = []\nLOADS.append(1)\n", encoding="utf-8")
    first = reproduce._scripts()
    second = reproduce._scripts()
    assert all(first[name] is second[name] for name in ("derive", "verify", "promote"))
    assert first["derive"].LOADS == [1]
    assert callable(first["promote"].load_clean)
