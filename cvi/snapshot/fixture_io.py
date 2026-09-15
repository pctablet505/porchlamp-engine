"""Shared reader for an on-disk, already-scored v2 snapshot directory
(one definition per fact).

The shape read here -- ``bundles/<owner>__<name>.json``,
``populations.json``, ``manifest.json`` and an optional ``expected.json``
-- is exactly what :func:`scripts.build_fixture_snapshot.write_fixture`
writes. Two callers need to read it back without either one importing a
``scripts/`` file (a script is not an importable package member, and
``cvi/snapshot`` -- the CLI's own package -- must not import ``scripts.*``):
``scripts/build_fixture_snapshot.py`` itself (round-trip tests that rebuild
the fixture and compare) and ``cvi.snapshot.store_cmd`` (the
``cvi.snapshot store`` CLI, scoring a real snapshot directory for the
store).

``expected.json`` is OPTIONAL: a real, non-fixture snapshot directory
``cvi.snapshot store`` is pointed at in production has no pinned
expectations to check itself against. ``FrozenFixture.expected`` is
``None`` when the file is absent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cvi.core.gates import GateEvidence
from cvi.core.models import FeatureBundle
from cvi.core.pipeline import RepoKey
from cvi.derive.bundle import load_bundle
from cvi.snapshot.populations import Population, PopulationSet


@dataclass(frozen=True)
class FrozenFixture:
    """A previously-written snapshot directory, read back from disk: bundles,
    the frozen ``PopulationSet``, gate evidence and raw hashes from
    ``manifest.json``, and ``expected.json`` (``None`` if absent) for a
    caller to compare against. Pure I/O, no scoring -- the caller calls
    ``cvi.core.pipeline.score_snapshot`` itself."""

    snapshot_id: str
    as_of: datetime
    bundles: list[FeatureBundle]
    populations: PopulationSet
    gate_evidence: dict[RepoKey, GateEvidence]
    raw_hashes: dict[RepoKey, str]
    expected: Optional[dict[str, Any]]


def load_frozen_fixture(out_dir: Path) -> FrozenFixture:
    """Read back a snapshot directory ``scripts.build_fixture_snapshot.
    write_fixture`` wrote: bundles, the frozen ``PopulationSet``, gate
    evidence and raw hashes from ``manifest.json``, and ``expected.json``
    when present. Pure I/O, no scoring -- the caller (a test, or
    ``cvi.snapshot.store_cmd``) calls ``score_snapshot`` itself."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_path = out_dir / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.is_file() else None
    as_of = datetime.fromisoformat(manifest["as_of"])

    bundles: list[FeatureBundle] = []
    gate_evidence: dict[RepoKey, GateEvidence] = {}
    raw_hashes: dict[RepoKey, str] = {}
    for entry in manifest["repos"]:
        bundle = load_bundle(out_dir / entry["bundle_file"])
        bundles.append(bundle)
        key = f"{entry['owner']}/{entry['name']}"
        gate_evidence[key] = GateEvidence(**entry["gate_evidence"])
        raw_hashes[key] = entry["raw_hash"]

    pop_doc = json.loads((out_dir / "populations.json").read_text(encoding="utf-8"))
    populations = PopulationSet(
        snapshot_id=pop_doc["snapshot_id"],
        benchmark_version=pop_doc["benchmark_version"],
        constants_hash=pop_doc["constants_hash"],
        populations=tuple(
            Population(entry["ecosystem"], entry["param"], entry["n"], entry["fallback"], entry["values"])
            for entry in pop_doc["populations"]
        ),
    )
    return FrozenFixture(
        snapshot_id=manifest["snapshot_id"], as_of=as_of, bundles=bundles, populations=populations,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes, expected=expected,
    )
