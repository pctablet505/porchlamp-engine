"""Envelope writes are atomic; a failed keyless fetch is not a measurement.

Two confirmed defects (`03` section 5 and the gather keyless path):

* B1 -- ``write_envelope`` used ``Path.write_text``, which truncates the target
  and then writes. A crash in the middle left a half-written file on disk, and
  because every resume check in the codebase is only ``path.is_file()``, a torn
  file is treated as "already collected, skip" forever. The fix writes to a
  sibling temp file and ``os.replace``\\ s it onto the target (atomic within a
  filesystem), so a reader ever sees the old file or the complete new one.

* B2 -- ``scripts/gather.py``'s keyless block recorded a failed fetch (network
  error / 503) as a permanent envelope stamped ``http_status=200``, so the repo
  was marked done with ``data = null`` forever. The fix distinguishes "the
  source answered, and says there is nothing" from "we could not reach the
  source": nothing is written for a failed fetch, and a real 404 carries a
  true ``http_status=404``.

Tests 1/2/4/5 fail before the fix; test 3 is the regression guard (the happy
path and the envelope's JSON shape must be unchanged by the fix).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

from porchlamp.collect.envelope import envelope_hash, read_envelope, write_envelope

ROOT = Path(__file__).resolve().parents[2]

#: The exact JSON keys an envelope has always been written with. Test 3 pins
#: that the atomic-write fix changes nothing about the file's shape.
ENVELOPE_KEYS = {
    "source", "fetched_at", "http_status", "etag", "sha256",
    "collector_version", "data",
}


@pytest.fixture
def gather(tmp_path: Path) -> ModuleType:
    """``scripts/gather.py`` loaded by path, pointed at a throwaway data dir.

    Mirrors ``tests/snapshot/test_gather_envelope.py``; ``DATA`` is repointed at
    ``tmp_path`` so no test ever writes into the real ``data/`` directory.
    """
    spec = importlib.util.spec_from_file_location(
        "porchlamp_gather_atomicity", ROOT / "scripts/gather.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA = tmp_path / "raw"
    return module


def _truncating_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
    """Stand in for ``Path.write_text``: open the target (truncating it) then
    die before any bytes are written -- a process killed mid-write."""
    with self.open("w"):  # "w" truncates an existing file to zero bytes first
        raise RuntimeError("simulated crash part-way through the write")


# --- B1: write_envelope is atomic -------------------------------------------

def test_interrupted_new_write_leaves_no_target(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that dies part-way leaves no envelope and no temp litter."""
    target = tmp_path / "depsdev" / "org__alpha.json"
    monkeypatch.setattr(Path, "write_text", _truncating_write_text)
    with pytest.raises(RuntimeError):
        write_envelope(target, "src", {"stars": 1}, "2026-01-01T00:00:00+00:00")
    assert not target.exists()
    assert list(target.parent.glob("*.tmp*")) == []


def test_interrupted_overwrite_keeps_the_original(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that dies part-way leaves the previous complete envelope intact."""
    target = tmp_path / "org__alpha.json"
    write_envelope(target, "src", {"first": True}, "2026-01-01T00:00:00+00:00")
    original = target.read_bytes()

    monkeypatch.setattr(Path, "write_text", _truncating_write_text)
    with pytest.raises(RuntimeError):
        write_envelope(target, "src", {"second": True}, "2026-02-02T00:00:00+00:00")

    assert target.read_bytes() == original
    assert json.loads(target.read_text(encoding="utf-8"))["data"] == {"first": True}


def test_happy_path_shape_is_unchanged(tmp_path: Path) -> None:
    """A successful write still reads back as a self-consistent envelope."""
    data = {"stars": 1000, "forks": [1, 2, 3]}
    target = write_envelope(tmp_path / "org__alpha.json", "src", data,
                            "2026-01-01T00:00:00+00:00")
    env = read_envelope(target)
    assert env.hash_matches_data is True
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert set(doc) == ENVELOPE_KEYS
    assert doc["http_status"] == 200
    assert doc["sha256"] == envelope_hash(data)


# --- B2: a failed keyless fetch is not recorded as a measurement ------------

def test_failed_keyless_fetch_writes_no_envelope(gather: ModuleType,
                                                 monkeypatch: pytest.MonkeyPatch,
                                                 block_live_network: object) -> None:
    """A transport error writes nothing and reports fail, so the next run retries."""
    def _raise(url: object, *args: object, **kwargs: object) -> object:
        raise urllib.error.URLError("simulated network down")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)

    status = gather.gather_repo("org", "alpha", "pypi", collectors=frozenset({"depsdev"}))

    assert status["depsdev"] == "fail"
    assert not (gather.DATA / "depsdev" / "org__alpha.json").exists()


def test_keyless_404_is_recorded_as_404(gather: ModuleType,
                                        monkeypatch: pytest.MonkeyPatch,
                                        block_live_network: object) -> None:
    """A real 404 (source says "not present") may be recorded, but with status 404."""
    def _http_404(url: object, *args: object, **kwargs: object) -> object:
        raise urllib.error.HTTPError(str(url), 404, "Not Found", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", _http_404)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)

    status = gather.gather_repo("org", "alpha", "pypi", collectors=frozenset({"depsdev"}))

    path = gather.DATA / "depsdev" / "org__alpha.json"
    assert path.is_file(), "the chosen policy is to record an authoritative 404"
    assert json.loads(path.read_text(encoding="utf-8"))["http_status"] == 404
    assert status["depsdev"] == "fail"
