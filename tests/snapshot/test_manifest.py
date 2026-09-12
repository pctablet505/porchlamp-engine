"""Unit tests for :mod:`cvi.snapshot.manifest` (packet W0-3).

The manifest lists ``(collector, key, sha256)`` for every envelope, a Merkle
root over those entries, and the code commit (`03` section 5, `04` section 2).
The Merkle definition pinned here is: leaves are the digests
``sha256(canonical JSON of [collector, key, sha256])``, each leaf is hashed once
more, parents are ``sha256(left_hex + right_hex)``, a level with an odd count
reuses its last node as its own right child, and an empty tree is
``sha256(canonical JSON of [])``.

Fail-before (R-1): ``cvi.snapshot`` does not exist on the base commit.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cvi.collect.envelope import envelope_hash, write_envelope
from cvi.constants import PILLAR_WEIGHTS
from cvi.snapshot.manifest import (
    MANIFEST_FILENAME,
    build_manifest,
    constants_hash,
    envelope_entries,
    load_manifest,
    merkle_root,
    snapshot_id,
    write_manifest,
)

REPOS = ("org__alpha", "org__beta", "org__gamma")
AS_OF = "2026-09-12T00:00:00+00:00"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FETCHED_AT = "2026-09-11T19:49:53.263996+00:00"
GFI_DATA: dict[str, Any] = [{"number": 7, "created_at": "2026-03-01T00:00:00Z"}]
META_DATA: dict[str, Any] = {"stars": 1000, "forks": 12}


def _legacy(data_dir: Path, collector: str, key: str, data: Any) -> Path:
    """Write an envelope in the pre-W0-3 shape (no ``sha256`` field)."""
    path = data_dir / "raw" / collector / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": FETCHED_AT, "source": f"github-rest:{collector}", "data": data}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Three repos x two collectors, all in the pre-W0-3 envelope shape."""
    root = tmp_path / "data"
    for key in REPOS:
        _legacy(root, "gfi", key, GFI_DATA)
        _legacy(root, "github_meta", key, META_DATA)
    return root


def _leaves(entries: list[dict[str, Any]]) -> list[str]:
    return [envelope_hash([e["collector"], e["key"], e["sha256"]]) for e in entries]


def test_entries_are_sorted_by_collector_then_key(data_dir: Path) -> None:
    entries = envelope_entries(data_dir)
    assert [(e["collector"], e["key"]) for e in entries] == [
        ("gfi", "org__alpha"),
        ("gfi", "org__beta"),
        ("gfi", "org__gamma"),
        ("github_meta", "org__alpha"),
        ("github_meta", "org__beta"),
        ("github_meta", "org__gamma"),
    ]


def test_entries_carry_the_hash_of_the_data_field_and_their_own_path(data_dir: Path) -> None:
    entries = envelope_entries(data_dir)
    assert entries[0]["sha256"] == envelope_hash(GFI_DATA)
    assert entries[3]["sha256"] == envelope_hash(META_DATA)
    assert entries[0]["path"] == "raw/gfi/org__alpha.json"
    assert entries[0]["bytes"] == (data_dir / "raw/gfi/org__alpha.json").stat().st_size
    assert {e["hash_origin"] for e in entries} == {"backfilled"}


def test_a_flipped_byte_inside_one_envelope_changes_only_that_entry(data_dir: Path) -> None:
    before = {e["key"]: e["sha256"] for e in envelope_entries(data_dir)}
    path = data_dir / "raw/github_meta/org__beta.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000'),
                    encoding="utf-8")
    after = {e["key"]: e["sha256"] for e in envelope_entries(data_dir)}
    assert before["org__beta"] != after["org__beta"]
    assert after["org__alpha"] == before["org__alpha"]
    assert after["org__gamma"] == before["org__gamma"]


def test_native_and_backfilled_entries_are_distinguishable(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _legacy(root, "gfi", "org__alpha", GFI_DATA)
    write_envelope(
        root / "raw/gfi/org__beta.json",
        source="github-rest:issues",
        data=GFI_DATA,
        fetched_at=FETCHED_AT,
    )
    entries = envelope_entries(root)
    assert [(e["key"], e["hash_origin"]) for e in entries] == [
        ("org__alpha", "backfilled"),
        ("org__beta", "native"),
    ]


def test_a_flipped_byte_moves_the_root_even_under_a_native_hash(tmp_path: Path) -> None:
    """The root attests the bytes, not the hash an envelope asserts about itself."""
    path = write_envelope(
        tmp_path / "data/raw/github_meta/org__alpha.json",
        source="github-graphql:repository",
        data={"stars": 1000},
        fetched_at=FETCHED_AT,
    )
    before = build_manifest(
        envelope_entries(tmp_path / "data"), as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="x",
    )
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000'),
                    encoding="utf-8")
    after = build_manifest(
        envelope_entries(tmp_path / "data"), as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="x",
    )
    assert before["hash_origins"] == {"native": 1, "backfilled": 0}
    assert after["hash_origins"] == {"native": 1, "backfilled": 0}
    assert after["hash_mismatches"] == ["github_meta/org__alpha"]
    assert after["merkle_root"] != before["merkle_root"]


def test_merkle_root_matches_the_pairwise_definition() -> None:
    leaves = ["leaf-a", "leaf-b", "leaf-c"]
    digest = hashlib.sha256
    level1 = [
        digest((digest(b"leaf-a").hexdigest() + digest(b"leaf-b").hexdigest()).encode()).hexdigest(),
        digest((digest(b"leaf-c").hexdigest() + digest(b"leaf-c").hexdigest()).encode()).hexdigest(),
    ]
    assert merkle_root(leaves) == digest((level1[0] + level1[1]).encode()).hexdigest()


def test_merkle_root_of_one_leaf_is_that_leaf_hashed_once() -> None:
    assert merkle_root(["leaf-a"]) == hashlib.sha256(b"leaf-a").hexdigest()


def test_merkle_root_of_no_entries_is_the_hash_of_an_empty_list() -> None:
    assert merkle_root([]) == hashlib.sha256(b"[]").hexdigest()


def test_merkle_root_changes_when_one_leaf_changes() -> None:
    assert merkle_root(["a", "b", "c"]) != merkle_root(["a", "X", "c"])


def test_merkle_root_is_order_sensitive() -> None:
    assert merkle_root(["a", "b", "c"]) != merkle_root(["c", "b", "a"])


def test_constants_hash_follows_the_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    import cvi.constants as constants

    before = constants_hash()
    monkeypatch.setattr(constants, "PILLAR_WEIGHTS", {**PILLAR_WEIGHTS, "rigor": 0.16})
    assert constants_hash() != before


def test_build_manifest_records_the_snapshot_identity(data_dir: Path) -> None:
    entries = envelope_entries(data_dir)
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="dump-hash"
    )
    assert manifest["manifest_version"] == 1
    assert manifest["as_of"] == AS_OF
    assert manifest["code_commit"] == COMMIT
    assert manifest["canonical_dump_hash"] == "dump-hash"
    assert manifest["envelope_count"] == 6
    assert manifest["hash_origins"] == {"native": 0, "backfilled": 6}
    assert manifest["hash_mismatches"] == []
    assert manifest["merkle_root"] == merkle_root(_leaves(entries))
    assert manifest["snapshot_id"] == snapshot_id(AS_OF, manifest["merkle_root"])
    assert len(manifest["constants_hash"]) == 64
    assert [entry["key"] for entry in manifest["envelopes"]] == [
        e["key"] for e in entries
    ]


def test_manifest_backfill_counts_are_visible_with_their_keys(data_dir: Path) -> None:
    write_envelope(
        data_dir / "raw/gfi/org__delta.json",
        source="github-rest:issues",
        data=GFI_DATA,
        fetched_at=FETCHED_AT,
    )
    manifest = build_manifest(
        envelope_entries(data_dir), as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="dump-hash",
    )
    assert manifest["hash_origins"] == {"native": 1, "backfilled": 6}
    assert manifest["backfilled_keys"] == [
        "gfi/org__alpha", "gfi/org__beta", "gfi/org__gamma",
        "github_meta/org__alpha", "github_meta/org__beta", "github_meta/org__gamma",
    ]


def test_manifest_lists_a_recorded_hash_that_no_longer_matches(data_dir: Path) -> None:
    path = _legacy(data_dir, "pr_reviews", "org__alpha", {"3": None})
    recorded = envelope_hash({"3": None})
    path.write_text(
        json.dumps({
            "fetched_at": FETCHED_AT, "source": "github-graphql:reviews",
            "sha256": recorded, "http_status": 200, "collector_version": "test/1",
            "data": {"3": "2026-03-01T00:00:00Z"},
        }),
        encoding="utf-8",
    )
    manifest = build_manifest(
        envelope_entries(data_dir), as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="dump-hash",
    )
    assert manifest["hash_mismatches"] == ["pr_reviews/org__alpha"]
    assert manifest["hash_origins"] == {"native": 1, "backfilled": 6}


def test_snapshot_id_is_a_deterministic_directory_name() -> None:
    assert snapshot_id(AS_OF, "abcdef0123456789") == "20260912T0000000000-abcdef012345"


def test_written_manifest_is_byte_identical_on_repeat(data_dir: Path) -> None:
    entries = envelope_entries(data_dir)
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="dump-hash"
    )
    first = write_manifest(data_dir, manifest)
    assert first.name == MANIFEST_FILENAME
    written = first.read_bytes()
    assert write_manifest(data_dir, manifest).read_bytes() == written
    assert json.loads(written)["merkle_root"] == manifest["merkle_root"]


def test_load_manifest_reads_back_the_written_file(data_dir: Path) -> None:
    manifest = build_manifest(
        envelope_entries(data_dir), as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="dump-hash",
    )
    write_manifest(data_dir, manifest)
    loaded = load_manifest(data_dir, manifest["snapshot_id"])
    assert loaded == manifest


def test_load_manifest_names_a_missing_snapshot(data_dir: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no snapshot"):
        load_manifest(data_dir, "20260912T0000000000-000000000000")


def test_code_commit_reads_the_checked_out_commit() -> None:
    from cvi.snapshot.manifest import code_commit

    root = Path(__file__).resolve().parents[2]
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert code_commit(root) == expected


def test_code_commit_is_none_outside_a_repository(tmp_path: Path) -> None:
    from cvi.snapshot.manifest import code_commit

    assert code_commit(tmp_path) is None
