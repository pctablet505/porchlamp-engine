"""Unit tests for :mod:`cvi.snapshot.reproduce` (packet W0-3).

Reproduction rebuilds the database from ``raw/`` and compares a canonical dump
hash (`03` section 5, `04` section 2). The canonical dump is: tables sorted by
name, rows of each table sorted by their canonical JSON, columns in declared
order, floats formatted with a fixed ``FIXED_FLOAT_DECIMALS`` decimal places so
a platform-dependent ``repr`` cannot change the hash.

The end-to-end test drives the real ``derive -> verify -> promote`` chain on a
synthetic one-repo data directory; no network is touched (``fetch=False``).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cvi.collect.envelope import write_envelope
from cvi.snapshot.manifest import build_manifest, envelope_entries, write_manifest
from cvi.snapshot.reproduce import (
    canonical_dump,
    canonical_dump_hash,
    check_envelopes,
    rebuild,
    reproduce,
    resolve_ecosystems,
)

FETCHED_AT = "2026-09-11T19:49:53.263996+00:00"
AS_OF = "2026-09-12T00:00:00+00:00"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO_KEY = "org__alpha"
GITHUB_META: dict[str, Any] = {
    "stars": 1000,
    "forks": 12,
    "pushed_at": "2026-09-01T00:00:00Z",
    "archived": False,
    "created_at": "2020-01-01T00:00:00Z",
    "license_spdx": "MIT",
    "default_branch": "main",
}
PR_EVENTS: list[dict[str, Any]] = [
    {
        "number": 1,
        "created_at": "2026-08-01T00:00:00Z",
        "closed_at": "2026-08-02T00:00:00Z",
        "merged_at": "2026-08-02T00:00:00Z",
        "author_login": "outside",
        "author_association": "NONE",
        "comments_total": 0,
        "state": "merged",
    }
]
DOWNLOADS: dict[str, Any] = {"monthly": 4000}


def _legacy(data_dir: Path, collector: str, key: str, data: Any) -> Path:
    path = data_dir / "raw" / collector / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": FETCHED_AT, "source": f"github-rest:{collector}", "data": data}),
        encoding="utf-8",
    )
    return path


def _sample_manifest(repos: list[tuple[str, str]]) -> dict[str, Any]:
    """The W0-1 sample-manifest shape: the only ecosystem source in a sample."""
    return {"repos": [{"repo": repo, "ecosystem": eco} for repo, eco in repos]}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """One repo with the envelopes the pipeline reads, in the legacy shape."""
    root = tmp_path / "data"
    _legacy(root, "github_meta", REPO_KEY, GITHUB_META)
    _legacy(root, "pr_events", REPO_KEY, PR_EVENTS)
    _legacy(root, "pr_reviews", REPO_KEY, {"1": "2026-08-01T02:00:00Z"})
    _legacy(root, "downloads", f"pypi__{REPO_KEY.split('__')[1]}", DOWNLOADS)
    (root / "manifest.json").write_text(
        json.dumps(_sample_manifest([("org/alpha", "pypi")])), encoding="utf-8"
    )
    return root


def _db(path: Path, rows: list[tuple[str, float, str | None]]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE repo (name TEXT PRIMARY KEY, score REAL, note TEXT)")
    conn.executemany("INSERT INTO repo VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def test_canonical_dump_sorts_rows_and_formats_floats(tmp_path: Path) -> None:
    db = _db(tmp_path / "one.db", [("beta", 2.5, None), ("alpha", 1.25, "ok")])
    assert canonical_dump(db) == [
        'repo ["alpha","1.250000000","ok"]',
        'repo ["beta","2.500000000",null]',
    ]


def test_canonical_dump_hash_ignores_row_insertion_order(tmp_path: Path) -> None:
    first = _db(tmp_path / "one.db", [("beta", 2.5, None), ("alpha", 1.25, "ok")])
    second = _db(tmp_path / "two.db", [("alpha", 1.25, "ok"), ("beta", 2.5, None)])
    assert canonical_dump_hash(first) == canonical_dump_hash(second)


def test_canonical_dump_hash_sees_an_eight_decimal_change(tmp_path: Path) -> None:
    first = _db(tmp_path / "one.db", [("alpha", 1.000000000, None)])
    second = _db(tmp_path / "two.db", [("alpha", 1.000000010, None)])
    assert canonical_dump_hash(first) != canonical_dump_hash(second)


def test_canonical_dump_hash_sees_a_null_become_a_value(tmp_path: Path) -> None:
    first = _db(tmp_path / "one.db", [("alpha", 1.0, None)])
    second = _db(tmp_path / "two.db", [("alpha", 1.0, "ok")])
    assert canonical_dump_hash(first) != canonical_dump_hash(second)


def test_resolve_ecosystems_uses_the_sample_manifest_then_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    (root / "raw").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(_sample_manifest([("org/alpha", "pypi")])), encoding="utf-8"
    )
    (root / "raw" / "candidates.jsonl").write_text(
        json.dumps({"owner": "org", "name": "beta", "ecosystem": "crates"}) + "\n",
        encoding="utf-8",
    )
    resolved = resolve_ecosystems(root, ["org/alpha", "org/beta", "org/unknown"])
    assert resolved == {"org/alpha": "pypi", "org/beta": "crates"}


def test_check_envelopes_names_the_flipped_envelope(data_dir: Path) -> None:
    entries = envelope_entries(data_dir).entries
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="x"
    )
    untouched = check_envelopes(data_dir, manifest)
    assert untouched.mismatched == []
    assert untouched.missing == []
    assert untouched.unexpected == []
    assert untouched.root == manifest["merkle_root"]

    path = data_dir / "raw/github_meta/org__alpha.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000'),
                    encoding="utf-8")
    flipped = check_envelopes(data_dir, manifest)
    assert flipped.mismatched == ["github_meta/org__alpha"]
    assert flipped.root != manifest["merkle_root"]


def test_check_envelopes_reports_missing_and_added_envelopes(data_dir: Path) -> None:
    manifest = build_manifest(
        envelope_entries(data_dir).entries, as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="x",
    )
    (data_dir / "raw/github_meta/org__alpha.json").unlink()
    write_envelope(
        data_dir / "raw/gfi/org__alpha.json",
        source="github-rest:issues",
        data=[],
        fetched_at=FETCHED_AT,
    )
    report = check_envelopes(data_dir, manifest)
    assert report.missing == ["github_meta/org__alpha"]
    assert report.unexpected == ["gfi/org__alpha"]
    assert report.mismatched == []


def test_rebuild_promotes_the_repo_and_is_repeatable(data_dir: Path) -> None:
    manifest = build_manifest(
        envelope_entries(data_dir).entries, as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="x",
    )
    first = rebuild(data_dir, manifest, data_dir / "work-a")
    second = rebuild(data_dir, manifest, data_dir / "work-b")
    assert first.is_file() and second.is_file()
    assert canonical_dump_hash(first) == canonical_dump_hash(second)
    dump = "\n".join(canonical_dump(first))
    assert '"org"' in dump
    assert '"alpha"' in dump


def test_rebuild_output_moves_when_an_envelope_byte_moves(data_dir: Path) -> None:
    manifest = build_manifest(
        envelope_entries(data_dir).entries, as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash="x",
    )
    before = canonical_dump_hash(rebuild(data_dir, manifest, data_dir / "work-a"))
    path = data_dir / "raw/github_meta/org__alpha.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9900'),
                    encoding="utf-8")
    after = canonical_dump_hash(rebuild(data_dir, manifest, data_dir / "work-b"))
    assert before != after


def test_reproduce_reports_a_matching_snapshot(data_dir: Path) -> None:
    entries = envelope_entries(data_dir).entries
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="pending"
    )
    db = rebuild(data_dir, manifest, data_dir / "work")
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash=canonical_dump_hash(db)
    )
    write_manifest(data_dir, manifest)

    report = reproduce(data_dir, manifest["snapshot_id"])
    assert report.ok is True
    assert report.dump_hash == manifest["canonical_dump_hash"]
    assert report.envelope_check.mismatched == []


def test_reproduce_names_the_key_of_a_flipped_envelope(data_dir: Path) -> None:
    entries = envelope_entries(data_dir).entries
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="pending"
    )
    db = rebuild(data_dir, manifest, data_dir / "work")
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash=canonical_dump_hash(db)
    )
    write_manifest(data_dir, manifest)

    path = data_dir / "raw/github_meta/org__alpha.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000'),
                    encoding="utf-8")
    report = reproduce(data_dir, manifest["snapshot_id"])
    assert report.ok is False
    assert report.envelope_check.mismatched == ["github_meta/org__alpha"]
    assert report.dump_hash != manifest["canonical_dump_hash"]


def test_reproduce_reuses_the_frozen_envelope_set(data_dir: Path) -> None:
    """An envelope added after the freeze is reported, not silently scored."""
    entries = envelope_entries(data_dir).entries
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="pending"
    )
    db = rebuild(data_dir, manifest, data_dir / "work")
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash=canonical_dump_hash(db)
    )
    write_manifest(data_dir, manifest)
    write_envelope(
        data_dir / "raw/gfi/org__alpha.json",
        source="github-rest:issues",
        data=[{"number": 4}],
        fetched_at=FETCHED_AT,
    )
    report = reproduce(data_dir, manifest["snapshot_id"])
    assert report.ok is False
    assert report.envelope_check.unexpected == ["gfi/org__alpha"]


# ---------------------------------------------------------------------------
# W0-3f: gate findings P-09 (corrupt-JSON envelope) and P-07b (stale native
# hash in the frozen manifest)
# ---------------------------------------------------------------------------


def _corrupt(path: Path) -> None:
    """Break the JSON in place: the first '}' becomes 'x' (the W0-gate P-09 repro)."""
    text = path.read_text(encoding="utf-8")
    i = text.index("}")
    path.write_text(text[:i] + "x" + text[i + 1:], encoding="utf-8")


def _stale_native(data_dir: Path, collector: str, key: str, data: Any) -> Path:
    """An envelope whose recorded ``sha256`` no longer describes its ``data``."""
    path = data_dir / "raw" / collector / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "fetched_at": FETCHED_AT, "source": f"github-rest:{collector}",
            "sha256": "0" * 64, "http_status": 200, "collector_version": "test/1",
            "data": data,
        }),
        encoding="utf-8",
    )
    return path


def _freeze(data_dir: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Freeze ``entries`` under ``data_dir`` with the real rebuilt dump hash."""
    pending = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT, canonical_dump_hash="pending"
    )
    db = rebuild(data_dir, pending, data_dir / "work")
    manifest = build_manifest(
        entries, as_of=AS_OF, code_commit=COMMIT,
        canonical_dump_hash=canonical_dump_hash(db),
    )
    write_manifest(data_dir, manifest)
    return manifest


def test_check_envelopes_names_a_corrupt_envelope_invalid_json(data_dir: Path) -> None:
    """P-09: an envelope that is no longer valid JSON is reported, not fatal."""
    manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
    _corrupt(data_dir / "raw/github_meta/org__alpha.json")
    report = check_envelopes(data_dir, manifest)
    assert report.invalid_json == ["github_meta/org__alpha"]
    assert report.missing == []
    assert report.mismatched == []
    assert report.matched == 3
    assert report.clean is False
    assert report.root != manifest["merkle_root"]


def test_check_envelopes_names_a_corrupt_file_added_after_freeze(data_dir: Path) -> None:
    """P-09: a new unreadable file is INVALID_JSON, not UNEXPECTED."""
    manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
    (data_dir / "raw/gfi").mkdir(parents=True, exist_ok=True)
    corrupt = data_dir / "raw/gfi/org__alpha.json"
    corrupt.write_text('{"fetched_at": "2026-09-11", "data": {BROKEN', encoding="utf-8")
    report = check_envelopes(data_dir, manifest)
    assert report.invalid_json == ["gfi/org__alpha"]
    assert report.unexpected == []
    assert report.missing == []
    assert report.clean is False


def test_check_envelopes_names_a_flipped_and_a_missing_envelope_still(
    data_dir: Path,
) -> None:
    """Over-correction guard: P-06 (byte flip) and P-08 (deletion) keep their
    categories when a third envelope becomes corrupt JSON at the same time."""
    manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
    path = data_dir / "raw/github_meta/org__alpha.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"stars": 1000', '"stars": 9000'),
                    encoding="utf-8")
    (data_dir / "raw/pr_events/org__alpha.json").unlink()
    _corrupt(data_dir / "raw/downloads/pypi__alpha.json")
    report = check_envelopes(data_dir, manifest)
    assert report.mismatched == ["github_meta/org__alpha"]
    assert report.missing == ["pr_events/org__alpha"]
    assert report.invalid_json == ["downloads/pypi__alpha"]
    assert report.matched == 1
    assert report.clean is False


def test_reproduce_survives_a_corrupt_envelope_and_names_it(data_dir: Path) -> None:
    """P-09: reproduce completes over the other envelopes and names the key."""
    manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
    _corrupt(data_dir / "raw/github_meta/org__alpha.json")
    report = reproduce(data_dir, manifest["snapshot_id"])
    assert report.ok is False
    assert report.envelope_check.invalid_json == ["github_meta/org__alpha"]
    assert report.envelope_check.missing == []
    assert report.envelope_check.mismatched == []
    assert report.dump_hash != manifest["canonical_dump_hash"]


def test_check_envelopes_names_a_stale_native_hash_from_the_frozen_manifest(
    data_dir: Path,
) -> None:
    """P-07b: the frozen manifest's hash_mismatches make the check unclean."""
    _stale_native(data_dir, "gfi", "org__alpha", [])
    manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
    assert manifest["hash_mismatches"] == ["gfi/org__alpha"]
    check = check_envelopes(data_dir, manifest)
    assert check.stale_native_hashes == ["gfi/org__alpha"]
    assert check.mismatched == []
    assert check.matched == 5
    assert check.clean is False


def test_reproduce_fails_when_the_frozen_manifest_records_a_stale_hash(
    data_dir: Path,
) -> None:
    """P-07b: reproduce cannot report MATCH while hash_mismatches is non-empty."""
    _stale_native(data_dir, "gfi", "org__alpha", [])
    manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
    report = reproduce(data_dir, manifest["snapshot_id"])
    assert report.ok is False
    assert report.envelope_check.stale_native_hashes == ["gfi/org__alpha"]
    assert report.dump_hash == manifest["canonical_dump_hash"]
