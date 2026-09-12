"""Tests for the ``scripts/gather.py`` envelope write path (packet W0-3).

Only the write path changed: every envelope gather writes must now carry
``sha256``, ``http_status`` and ``collector_version`` (`03` section 5), and the
hash must be the one definition in :mod:`cvi.collect.envelope`. The keys the
existing readers use (``source``, ``fetched_at``, ``data``) keep their meaning.

The script is loaded by path the way ``tests/test_derive.py`` loads its subject.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cvi.collect.envelope import COLLECTOR_VERSION, envelope_hash

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def gather(tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("cvi_gather_w03", ROOT / "scripts/gather.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.DATA = tmp_path / "raw"
    return module


def test_env_writes_a_hashed_envelope(gather: ModuleType) -> None:
    path = gather._env("gfi", "org__alpha", "github-rest:repos/org/alpha/issues", [])
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["data"] == []
    assert doc["sha256"] == envelope_hash([])
    assert doc["http_status"] == 200
    assert doc["collector_version"] == COLLECTOR_VERSION
    assert doc["source"] == "github-rest:repos/org/alpha/issues"
    assert doc["fetched_at"]


def test_env_hash_matches_the_data_field_only(gather: ModuleType) -> None:
    payload = {"stars": 1000, "forks": 12}
    path = gather._env("github_meta", "org__alpha", "github-graphql", payload)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["sha256"] == envelope_hash(payload)
    mutated = json.loads(json.dumps(payload))
    mutated["stars"] = 9000
    other = gather._env("github_meta", "org__beta", "github-graphql", mutated)
    assert json.loads(other.read_text(encoding="utf-8"))["sha256"] != doc["sha256"]


def test_env_still_creates_the_collector_directory(gather: ModuleType) -> None:
    path = gather._env("tree", "org__alpha", "github-rest:git/trees", {"paths": []})
    assert path == gather.DATA / "tree" / "org__alpha.json"
    assert path.parent.is_dir()
