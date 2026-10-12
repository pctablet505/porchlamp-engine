"""``porchlamp snapshot build`` driver: the manifest Merkle root covers populations.

The Merkle root in ``manifest.json`` is the integrity anchor of a built
snapshot (D-VER-3). It must cover *every* file the build writes, so that two
builds over the same bundles but different ``populations.json`` produce
different roots (E-01). These tests build a snapshot directory from a copy of
the committed fixture bundles and pin that property.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from porchlamp.core import constants
from porchlamp.core.models import sha256_hex
from porchlamp.snapshot.build_cmd import build_snapshot_dir
from porchlamp.snapshot.manifest import merkle_root
from porchlamp.snapshot.populations import Population, PopulationSet

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_50"
_AS_OF = datetime(2026, 9, 12, tzinfo=timezone.utc)
_COMMIT = "0123456789abcdef0123456789abcdef012345678"


def _make_data_dir(tmp_path: Path, count: int = 3) -> Path:
    """Copy ``count`` fixture bundles into a ``data_dir/derived`` with empty ``raw``.

    The fixture bundles are v2 documents, so ``load_bundle`` reads them without
    needing the raw envelopes; an empty ``raw/`` yields all-false gate evidence,
    which is fine because these tests control populations directly."""
    data_dir = tmp_path / "data"
    (data_dir / "derived").mkdir(parents=True)
    (data_dir / "raw").mkdir(parents=True)
    for source in sorted((_FIXTURE / "bundles").glob("*.json"))[:count]:
        shutil.copy(source, data_dir / "derived" / source.name)
    return data_dir


def _pop_set(snapshot_id: str, param_values: dict[str, float]) -> PopulationSet:
    """A structurally valid :class:`PopulationSet` with one population."""
    return PopulationSet(
        snapshot_id=snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        populations=(
            Population("pypi", "ttm_p90_hours", len(param_values), False, dict(param_values)),
        ),
    )


def _merkle_root_of(out_dir: Path) -> str:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))["merkle_root"]


def test_merkle_root_covers_populations(tmp_path, monkeypatch):
    """Two builds over identical bundles but different populations differ (E-01)."""
    data_dir = _make_data_dir(tmp_path)

    def build(popset_a: bool) -> str:
        def fake_build_populations(members, snapshot_id):
            if popset_a:
                return _pop_set(snapshot_id, {"a/b": 1.0})
            return _pop_set(snapshot_id, {"a/b": 1.0, "c/d": 2.0})

        monkeypatch.setattr(
            "porchlamp.snapshot.build_cmd.build_populations", fake_build_populations
        )
        out_dir = tmp_path / ("out_a" if popset_a else "out_b")
        build_snapshot_dir(
            data_dir,
            out_dir,
            snapshot_id="TEST-POP",
            as_of=_AS_OF,
            code_commit_sha=_COMMIT,
        )
        return _merkle_root_of(out_dir)

    root_a = build(True)
    root_b = build(False)
    assert root_a != root_b, (
        "merkle_root ignored populations.json: identical bundles with different "
        f"populations produced the same root {root_a!r}"
    )


def test_merkle_root_is_deterministic(tmp_path):
    """The same input built twice yields the same Merkle root."""
    data_dir = _make_data_dir(tmp_path)
    roots = set()
    for name in ("run_1", "run_2"):
        out_dir = tmp_path / name
        build_snapshot_dir(
            data_dir,
            out_dir,
            snapshot_id="TEST-DET",
            as_of=_AS_OF,
            code_commit_sha=_COMMIT,
        )
        roots.add(_merkle_root_of(out_dir))
    assert len(roots) == 1


def test_merkle_root_matches_documented_recipe(tmp_path):
    """manifest.merkle_root == merkle_root(sorted(raw_hashes + populations leaf))."""
    data_dir = _make_data_dir(tmp_path)
    out_dir = tmp_path / "out"
    frozen = build_snapshot_dir(
        data_dir,
        out_dir,
        snapshot_id="TEST-RECIPE",
        as_of=_AS_OF,
        code_commit_sha=_COMMIT,
    )
    populations_leaf = sha256_hex((out_dir / "populations.json").read_text(encoding="utf-8"))
    expected = merkle_root(sorted([*frozen.raw_hashes.values(), populations_leaf]))
    assert _merkle_root_of(out_dir) == expected


def test_manifest_carries_the_review_handoff_so_the_stored_snapshot_scores_it(tmp_path):
    """``snapshot store`` scores the evidence it reads back from ``manifest.json``, so a hand-off
    found while building must be written there (RFC-0007), not only used in-process."""
    data_dir = _make_data_dir(tmp_path)
    first, second = sorted((data_dir / "derived").glob("*.json"))[:2]
    notice = {"author": {"login": "gopherbot", "__typename": "User"}, "authorAssociation": "NONE",
              "bodyText": "This PR has been imported to Gerrit for code review.",
              "createdAt": "2026-09-01T01:00:00Z"}
    prs = [{"number": n, "author": {"login": f"c{n}", "__typename": "User"}, "authorAssociation": "NONE",
            "createdAt": "2026-09-01T00:00:00Z", "comments": {"nodes": [notice]}} for n in range(1, 6)]
    envelope = {"data": {"prs": prs, "derived": [
        {"number": n, "is_external": True, "is_bot_author": False} for n in range(1, 6)]}}
    (data_dir / "raw" / "pr_window").mkdir()
    (data_dir / "raw" / "pr_window" / first.name).write_text(json.dumps(envelope), encoding="utf-8")

    out_dir = tmp_path / "out"
    frozen = build_snapshot_dir(
        data_dir, out_dir, snapshot_id="TEST-HANDOFF", as_of=_AS_OF, code_commit_sha=_COMMIT)

    handoff = {key: ev.review_handoff for key, ev in frozen.gate_evidence.items()}
    first_key = first.stem.replace("__", "/", 1)
    assert handoff[first_key] is True
    assert all(value is None for key, value in handoff.items() if key != first_key)
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    by_repo = {f"{r['owner']}/{r['name']}": r["gate_evidence"] for r in manifest["repos"]}
    assert by_repo[first_key]["review_handoff"] is True
    assert second.stem.replace("__", "/", 1) in by_repo
