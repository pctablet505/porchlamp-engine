"""One directory, two names: on Android the files directory is ``/data/user/0/<pkg>/files`` (what the app passes) and
``/data/data/<pkg>/files`` (a symlink to it, what gather.py resolves its data root to). The engine must treat them as one
directory every time it asks "is this the same place?", with real files and a real symlink."""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from phone import entry, runner
from phone.entry import Engine
from tests.helpers.fakes import loopback_gate_options
from tests.helpers.rig import LAN, WIFI

pytestmark = pytest.mark.usefixtures("clean_env", "utc")

FAKE_GATHER = '''"""A stand-in for scripts/gather.py: the same data-root rule (fixed and resolved at import), a main that records
each call and can be told to hold between requests."""
from phone import httpgate
from porchlamp.paths import data_root

DATA = data_root() / "raw"
CALLS = []


def main(argv):
    CALLS.append(list(argv))
    hold = DATA.parent / "hold"
    while hold.exists():
        httpgate.current().sleep(0.05)       # a collector busy between requests: a stop or a restart unwinds it
    (DATA / "depsdev").mkdir(parents=True, exist_ok=True)
    (DATA / "depsdev" / f"run{len(CALLS)}.json").write_text("{}")
    print("done: 2 clean, 0 with failures")
    return 0
'''


def wait_until(predicate, seconds: float = 20.0, step: float = 0.05) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def names(tmp_path: Path) -> tuple[Path, Path]:
    """(real, alias): ``alias`` is a symlink to the directory ``real``."""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    assert alias.resolve() == real.resolve() and str(alias) != str(real.resolve())
    return real, alias


@pytest.fixture
def fake_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "engine"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "gather.py").write_text(FAKE_GATHER, encoding="utf-8")
    monkeypatch.setattr(entry, "ENGINE_ROOT", root)
    monkeypatch.setattr(sys, "path", list(sys.path))          # load_gather puts the engine directory on the path
    return root


def loaded() -> object:
    (module,) = runner._GATHER_CACHE.values()
    return module


def calls() -> list:
    return loaded().CALLS if len(runner._GATHER_CACHE) == 1 else []


def start_engine(files_dir: Path, clock=time.time) -> Engine:
    eng = Engine(files_dir, clock=clock, gate_options=loopback_gate_options(), tick_s=0.2)
    (eng.home / "raw").mkdir(parents=True, exist_ok=True)
    (eng.home / "raw" / "candidates.jsonl").write_text(
        json.dumps({"owner": "a", "name": "b", "ecosystem": "pypi"}) + "\n", encoding="utf-8")
    eng.settings.set({"layers": ["LK"], "battery_override": True})
    eng.start(None, LAN)
    eng.update_device(json.dumps(WIFI))
    return eng


# ---- (a) the loader ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("order", ["alias-then-real", "real-then-alias", "alias-twice"])
def test_load_gather_takes_the_same_directory_under_both_names(names, order):
    real, alias = names
    first, second = {"alias-then-real": (alias, real), "real-then-alias": (real, alias), "alias-twice": (alias, alias)}[order]
    one = runner.load_gather(entry.ENGINE_ROOT, first)                  # the real scripts/gather.py
    assert one.DATA == real.resolve() / "raw"                           # gather resolved its data root at import
    two = runner.load_gather(entry.ENGINE_ROOT, second)
    assert two is one


def test_load_gather_still_refuses_a_different_directory(names, tmp_path):
    real, alias = names
    other = tmp_path / "other"
    other.mkdir()
    runner.load_gather(entry.ENGINE_ROOT, alias)
    with pytest.raises(RuntimeError, match="already loaded with data root"):
        runner.load_gather(entry.ENGINE_ROOT, other)
    with pytest.raises(RuntimeError, match="already loaded with data root"):
        runner.load_gather(entry.ENGINE_ROOT, real / "sub")             # a sub-directory is not the same directory


# ---- (b) through the engine: reset, then stop and start ----------------------------------------------------------
def test_the_collector_starts_again_after_a_reset_and_after_a_stop_when_the_directory_has_two_names(names, fake_engine):
    _real, alias = names
    eng = start_engine(alias / "files")
    try:
        assert wait_until(lambda: (eng.home / "state/refresh/LK.complete").is_file())
        module = loaded()
        assert len(module.CALLS) == 1                                  # a run with no failure completes the layer
        reply = json.loads(eng.danger("reset", "reset"))               # moves the folders aside, restarts the collector
        assert reply["ok"] is True
        assert wait_until(lambda: len(module.CALLS) == 2), eng.techlog.read_text()[-600:]
        assert wait_until(lambda: (eng.home / "state/refresh/LK.complete").is_file())
        aside = [p for p in (eng.home / "state").iterdir() if p.name.startswith("refresh-prev-reset-")]
        assert len(aside) == 1 and (aside[0] / "LK.complete").is_file()  # the new marker is a fresh one
        eng.stop()
        shutil.rmtree(eng.home / "state" / "refresh")                  # a new cycle is due
        eng.start(None, LAN)
        eng.update_device(json.dumps(WIFI))
        assert wait_until(lambda: len(module.CALLS) == 3), eng.techlog.read_text()[-600:]
        assert wait_until(lambda: (eng.home / "state/refresh/LK.complete").is_file())
        errors = [e for e in eng.timeline.list(0, 100) if e["text"] == "The collector hit an error"]
        assert errors == [] and "already loaded" not in eng.techlog.read_text()
    finally:
        eng.stop()


# ---- (c) the stuck detector's restart ----------------------------------------------------------------------------
def test_a_stuck_restart_brings_a_working_collector_back_when_the_directory_has_two_names(names, fake_engine):
    _real, alias = names
    skew = [0.0]
    hold = alias / "files" / "porchlamp" / "hold"
    hold.parent.mkdir(parents=True)
    hold.write_text("x")                                               # the first run waits between requests
    eng = start_engine(alias / "files", clock=lambda: time.time() + skew[0])
    try:
        assert wait_until(lambda: len(calls()) == 1)                  # the first run is holding between requests
        module = loaded()
        assert wait_until(lambda: eng._work_since is not None)           # the monitor has seen it working
        skew[0] += 26 * 60                                             # nothing moves for 26 minutes
        assert wait_until(lambda: eng.stuck is True)
        assert wait_until(lambda: "collector thread ended" in eng.techlog.read_text())   # the old thread has unwound
        hold.unlink()                                                  # the new run does not wait
        assert wait_until(lambda: len(module.CALLS) == 2), eng.techlog.read_text()[-600:]   # the held run, then run 1 again
        assert wait_until(lambda: (eng.home / "state/refresh/LK.complete").is_file())
        errors = [e for e in eng.timeline.list(0, 100) if e["text"] == "The collector hit an error"]
        assert errors == [] and "already loaded" not in eng.techlog.read_text()
    finally:
        eng.stop()


# ---- the app's module-level start knows one directory by both names ----------------------------------------------
def test_the_module_level_start_keeps_one_engine_for_one_directory_under_both_names(names, monkeypatch):
    real, alias = names
    monkeypatch.setattr(entry, "_ENGINE", None)
    monkeypatch.setattr(entry, "_PENDING_DEVICE", None)
    made: list[Engine] = []
    real_init = Engine.__init__

    def counting(self, *args, **kwargs):
        real_init(self, *args, threads=False, **kwargs)
        made.append(self)

    monkeypatch.setattr(Engine, "__init__", counting)
    try:
        entry.start(str(alias / "files"), None, None)
        first = entry._ENGINE
        entry.start(str(real / "files"), None, None)                    # the same directory, spelt the other way
        assert entry._ENGINE is first and len(made) == 1 and first.stopped is False
        other = real / "elsewhere"
        entry.start(str(other), None, None)                             # a different directory still replaces it
        assert entry._ENGINE is not first and len(made) == 2 and first.stopped is True
    finally:
        if entry._ENGINE is not None:
            entry._ENGINE.stop()
